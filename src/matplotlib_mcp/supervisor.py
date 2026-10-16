"""Bounded, killable single-process rendering, independent of Matplotlib."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import anyio


class RenderError(ValueError):
    """A renderer failure safe to return to callers."""


@dataclass(frozen=True)
class RenderSettings:
    queue_size: int = 50
    queue_bytes: int = 32 * 1024 * 1024
    queue_seconds: int = 30
    body_seconds: int = 10
    render_seconds: int = 10
    memory_bytes: int = 256 * 1024 * 1024
    recycle_charts: int = 50
    json_tokens: int = 250_000
    json_depth: int = 64

    @classmethod
    def from_env(cls) -> "RenderSettings":
        values = {}
        for field in cls.__dataclass_fields__:
            name = "RENDER_TIMEOUT_SECONDS" if field == "render_seconds" else "RENDER_" + field.upper()
            try:
                value = int(os.environ.get(name, getattr(cls(), field)))
            except ValueError:
                raise RuntimeError(f"{name} must be a positive integer") from None
            if value <= 0:
                raise RuntimeError(f"{name} must be a positive integer")
            values[field] = value
        if sys.platform != "linux":
            raise RuntimeError("renderer memory enforcement requires Linux RLIMIT_AS")
        import resource
        if not hasattr(resource, "RLIMIT_AS"):
            raise RuntimeError("renderer memory enforcement requires RLIMIT_AS")
        return cls(**values)


SETTINGS = RenderSettings.from_env()
# Limit IPC even when validation settings are disabled. PNG data is base64 encoded.
MAX_REPLY_BYTES = 12 * 1024 * 1024
_logger = logging.getLogger("matplotlib_mcp.access")


class Renderer:
    """One process per service; all pipe access and restarts are serialized."""

    def __init__(self, settings: RenderSettings = SETTINGS):
        self.settings = settings
        self.lock = asyncio.Lock()
        self.process: asyncio.subprocess.Process | None = None
        self.completed = 0
        self.waiting = 0

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is not None:
            with anyio.CancelScope(shield=True):
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await process.wait()
        self.completed = 0

    async def _start(self) -> None:
        env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1",
                   MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
        env["RENDER_MEMORY_BYTES"] = str(self.settings.memory_bytes)
        starting = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, "-m", "matplotlib_mcp.worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env, limit=MAX_REPLY_BYTES,
        ))
        try:
            self.process = await asyncio.shield(starting)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                self.process = await starting
                await self.close()
            raise

    async def render(self, config: dict[str, Any], data: dict[str, Any]) -> Any:
        if self.waiting >= self.settings.queue_size:
            raise RenderError("renderer is busy; retry later")
        self.waiting += 1
        try:
            try:
                await asyncio.wait_for(self.lock.acquire(), self.settings.queue_seconds)
            except TimeoutError:
                raise RenderError("render queue deadline exceeded; retry later") from None
        finally:
            self.waiting -= 1
        started = time.monotonic()
        category = "success"
        try:
            async with asyncio.timeout(self.settings.render_seconds):
                if self.process is None:
                    await self._start()
                process = self.process
                assert process is not None and process.stdin and process.stdout
                request = json.dumps({"config": config, "data": data}, separators=(",", ":"))
                process.stdin.write(request.encode() + b"\n")
                await process.stdin.drain()
                reply = await process.stdout.readline()
                if not reply or len(reply) >= MAX_REPLY_BYTES:
                    raise RenderError("renderer exited or exceeded its response limit")
                result = json.loads(reply)
                self.completed += 1
                if self.completed >= self.settings.recycle_charts:
                    await self.close()
                if "error" in result:
                    category = "memory_limit" if result.get("fatal") else "rejected"
                    if result.get("fatal"):
                        await self.close()
                    raise RenderError(result["error"])
                return base64.b64decode(result["png"], validate=True), result["metadata"]
        except asyncio.CancelledError:
            category = "cancelled"
            await self.close()
            raise
        except TimeoutError:
            category = "deadline"
            await self.close()
            raise RenderError("render deadline exceeded; simplify the chart") from None
        except RenderError:
            if category == "success":
                category = "worker_failure"
                await self.close()
            raise
        except Exception:
            category = "worker_failure"
            await self.close()
            raise RenderError("renderer failed; simplify the chart or retry") from None
        finally:
            self.lock.release()
            _logger.info("render outcome=%s duration_ms=%.1f", category,
                         (time.monotonic() - started) * 1000)
