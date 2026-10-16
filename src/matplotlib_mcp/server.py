"""FastMCP and HTTP application for the isolated Matplotlib renderer."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import time
from weakref import WeakKeyDictionary
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import TextContent

from . import rendering
from .errors import InputError
from .supervisor import Renderer, RenderError, SETTINGS


API_KEY = os.environ.get("MCP_API_KEY", "")
if len(API_KEY) < 16:
    raise RuntimeError("MCP_API_KEY must be configured with at least 16 characters")


_MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
try:
    _MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
except ValueError as exc:
    raise RuntimeError("MCP_PORT must be an integer") from exc
_default_allowed_hosts = [f"{host}:{_MCP_PORT}" for host in {_MCP_HOST, "127.0.0.1", "localhost"}]
_allowed_hosts = [host.strip() for host in os.environ.get("MCP_ALLOWED_HOSTS", ",".join(_default_allowed_hosts)).split(",") if host.strip()]


class _UtcAuditFormatter(logging.Formatter):
    converter = time.gmtime


_audit_logger = logging.getLogger("matplotlib_mcp.access")
_audit_logger.setLevel(logging.INFO)
_audit_logger.propagate = False
if not _audit_logger.handlers:
    _audit_handler = logging.StreamHandler()
    _audit_handler.setFormatter(_UtcAuditFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S UTC"))
    _audit_logger.addHandler(_audit_handler)


def _caller(scope: dict[str, Any]) -> str:
    client = scope.get("client")
    if not isinstance(client, (tuple, list)) or not client:
        return "unknown"
    try:
        address = ipaddress.ip_address(str(client[0]))
        port = client[1] if len(client) > 1 and isinstance(client[1], int) else 0
        host = f"[{address}]" if address.version == 6 else str(address)
        return f"{host}:{port}"
    except ValueError:
        return "unknown"


def _audit_action(body: bytes, headers: dict[bytes, bytes]) -> list[str]:
    """Extract only protocol/tool names; never include MCP parameters or results."""
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        payload = None
    messages = payload if isinstance(payload, list) else [payload]
    actions: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        method = message.get("method")
        if not isinstance(method, str):
            header_method = headers.get(b"mcp-method", b"").decode("ascii", errors="ignore")
            method = header_method if header_method in {"initialize", "tools/list", "tools/call", "ping"} else "request"
        if method == "tools/call":
            params = message.get("params")
            tool_name = params.get("name") if isinstance(params, dict) else None
            if isinstance(tool_name, str) and tool_name in {"create_chart", "list_chart_types"}:
                actions.append(tool_name)
            else:
                actions.append("unknown_tool")
        elif method in {"initialize", "tools/list", "ping", "notifications/initialized"}:
            actions.append(method.replace("/", ":"))
        else:
            actions.append("request")
    return actions or ["request"]


def _audit(scope: dict[str, Any], action: str, status: int, started: float) -> None:
    method = scope.get("method", "UNKNOWN")
    if not isinstance(method, str) or not method.isascii() or not method.isalpha():
        method = "UNKNOWN"
    path = scope.get("path", "/mcp")
    if path not in {"/mcp", "/mcp/"}:
        path = "/mcp"
    version = scope.get("http_version", "1.1")
    if version not in {"1.0", "1.1", "2", "3"}:
        version = "1.1"
    elapsed_ms = max(0.0, (time.monotonic() - started) * 1000)
    _audit_logger.info(
        '%s - "%s %s HTTP/%s" %d action=%s duration_ms=%.1f',
        _caller(scope), method, path, version, status, action, elapsed_ms,
    )

_RENDERERS: WeakKeyDictionary = WeakKeyDictionary()


def _renderer() -> Renderer:
    loop = asyncio.get_running_loop()
    if loop not in _RENDERERS:
        _RENDERERS[loop] = Renderer()
    return _RENDERERS[loop]


mcp = FastMCP(
    "Matplotlib Charts",
    log_level="ERROR",
    host=_MCP_HOST,
    port=_MCP_PORT,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(allowed_hosts=_allowed_hosts),
)


@mcp.tool()
def list_chart_types(method: str | None = None) -> dict[str, Any]:
    """List permitted Matplotlib methods, required data, options, and examples."""
    catalog = rendering._catalog(method)
    catalog["rendering"] = {name: getattr(SETTINGS, name) for name in SETTINGS.__dataclass_fields__}
    return catalog


@mcp.tool(name="create_chart")
async def _create_chart_tool(config: dict[str, Any], data: dict[str, Any]) -> Any:
    """Render a PNG using config {figure?, theme?: bool, axes: [{subplot?, calls: [{method, args?, kwargs?, colorbar?}]}]}.

    Supply data as {format: 'columns', values: {name: [...]}} (or rows/csv).
    Use {'$data': 'name'} in args/kwargs. Call list_chart_types for options and examples.
    The server theme applies by default; use theme=false to opt out. Set native color,
    colors, or cmap kwargs where supported to override defaults; see list_chart_types.
    """
    try:
        png, metadata = await _renderer().render(config, data)
    except RenderError as exc:
        raise InputError(str(exc)) from None
    return [Image(data=png, format="png"), TextContent(type="text", text=json.dumps(metadata, separators=(",", ":")))]


def _http_json_encoding_valid(body: bytes) -> bool:
    """Require UTF-8 and prevent downstream byte decoders from detecting UTF-16/32."""
    if b"\x00" in body:
        return False
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _http_json_within_budget(body: bytes) -> bool:
    """Bound decoded JSON expansion without constructing an object tree.

    The caller must first validate UTF-8 with _http_json_encoding_valid.
    Count container delimiters, separators and string starts outside strings.
    String contents need only bounded raw bytes, and escaped quotes cannot hide
    structure. Malformed JSON still goes through normal protocol validation.
    """
    tokens = depth = 0
    quoted = escaped = False
    for byte in body:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
            continue
        if byte == 34:
            quoted = True
            tokens += 1
        elif byte in (123, 91):
            depth += 1
            tokens += 1
            if depth > SETTINGS.json_depth:
                return False
        elif byte in (125, 93):
            depth -= 1
            tokens += 1
        elif byte in (44, 58):
            tokens += 1
        if tokens > SETTINGS.json_tokens:
            return False
    return True


class ApiKeyAndBodyLimit:
    """Authenticate HTTP requests and bound the request body before FastMCP reads it."""

    def __init__(self, app: Any, api_key: str, max_bytes: int):
        self.app = app
        self.api_key = api_key.encode("utf-8")
        self.max_bytes = max_bytes
        self.buffered_bytes = 0
        self.reading = 0
        self.inflight = 0
        self.waiting = 0
        self.chart_lock = asyncio.Lock()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            try:
                await self.app(scope, receive, send)
            finally:
                if scope["type"] == "lifespan":
                    renderer = _RENDERERS.pop(asyncio.get_running_loop(), None)
                    if renderer is not None:
                        await renderer.close()
            return
        started = time.monotonic()
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"authorization", b"")
        expected = b"Bearer " + self.api_key
        if not hmac.compare_digest(supplied, expected):
            await self._respond(send, 401, b'{"error":"unauthorized"}')
            _audit(scope, "unauthorized", 401, started)
            return
        # Retain queued bodies as bytes, never as decoded config/data trees.
        if self.reading >= SETTINGS.queue_size + 4 or self.inflight >= SETTINGS.queue_size + 8:
            await self._respond(send, 429, b'{"error":"request capacity full; retry later"}')
            _audit(scope, "busy", 429, started)
            return
        self.reading += 1
        self.inflight += 1
        reserved = 0
        acquired = False
        status = 500
        actions = "request"
        disconnected = None
        try:
            body_parts: list[bytes] = []
            size = 0
            more = True
            try:
                # One deadline for the whole upload; chunks do not reset it.
                async with asyncio.timeout_at(started + SETTINGS.body_seconds):
                    while more:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            status = 499
                            return
                        chunk = message.get("body", b"")
                        size += len(chunk)
                        if self.max_bytes and size > self.max_bytes:
                            status = 413
                            await self._respond(send, status, b'{"error":"request too large"}')
                            return
                        if self.buffered_bytes + len(chunk) > SETTINGS.queue_bytes:
                            status = 429
                            await self._respond(send, status, b'{"error":"request byte capacity full; retry later"}')
                            return
                        self.buffered_bytes += len(chunk)
                        reserved += len(chunk)
                        body_parts.append(chunk)
                        more = message.get("more_body", False)
                body = b"".join(body_parts)
                body_parts.clear()
            except TimeoutError:
                status = 408
                actions = "body_timeout"
                await self._respond(send, status, b'{"error":"request body deadline exceeded"}')
                return
            finally:
                self.reading -= 1
            # Large bodies wait before either audit or MCP JSON parsing. This
            # also prevents oversized control/unknown-tool requests from creating
            # concurrent decoded trees alongside a chart.
            large_body = size > 8192
            if not large_body and not _http_json_encoding_valid(body):
                status = 400
                await self._respond(send, status, b'{"error":"request body must be UTF-8 JSON without raw NUL bytes"}')
                return
            if not large_body and not _http_json_within_budget(body):
                status = 413
                await self._respond(send, status, b'{"error":"request JSON structure exceeds limit"}')
                return
            action_list = ["large_request"] if large_body else _audit_action(body, headers)
            actions = ",".join(action_list)

            incoming: asyncio.Queue = asyncio.Queue(maxsize=1)

            async def wait_disconnect():
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        if incoming.full():
                            incoming.get_nowait()
                        incoming.put_nowait(message)
                        return
                    await incoming.put(message)
                    await asyncio.sleep(0)

            disconnected = asyncio.create_task(wait_disconnect())

            async def dispatch():
                nonlocal acquired, status, body, action_list, actions
                if large_body or "create_chart" in action_list:
                    if self.waiting >= SETTINGS.queue_size + int(not self.chart_lock.locked()):
                        status = 429
                        await self._respond(send, status, b'{"error":"render queue full; retry later"}')
                        return
                    self.waiting += 1
                    try:
                        try:
                            await asyncio.wait_for(self.chart_lock.acquire(), SETTINGS.queue_seconds)
                            acquired = True
                        except TimeoutError:
                            status = 503
                            await self._respond(send, status, b'{"error":"render queue deadline exceeded; retry later"}')
                            return
                    finally:
                        self.waiting -= 1
                if large_body:
                    if not _http_json_encoding_valid(body):
                        status = 400
                        await self._respond(send, status, b'{"error":"request body must be UTF-8 JSON without raw NUL bytes"}')
                        return
                    if not _http_json_within_budget(body):
                        status = 413
                        await self._respond(send, status, b'{"error":"request JSON structure exceeds limit"}')
                        return
                    action_list = _audit_action(body, headers)
                    actions = ",".join(action_list)

                async def logging_send(message):
                    nonlocal status
                    if message["type"] == "http.response.start":
                        status = int(message["status"])
                    await send(message)

                delivered = False

                async def replay_receive():
                    nonlocal delivered, body
                    if not delivered:
                        delivered = True
                        payload, body = body, b""
                        return {"type": "http.request", "body": payload, "more_body": False}
                    return await incoming.get()

                await self.app(scope, replay_receive, logging_send)

            task = asyncio.create_task(dispatch())
            try:
                done, _ = await asyncio.wait({task, disconnected}, return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    await task
                else:
                    status = 499
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            if disconnected is not None:
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)
            if acquired:
                self.chart_lock.release()
            self.buffered_bytes -= reserved
            self.inflight -= 1
            _audit(scope, actions, status, started)

    @staticmethod
    async def _respond(send: Any, status: int, body: bytes) -> None:
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        if status in {429, 503}:
            headers.append((b"retry-after", b"1"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def main() -> None:
    app = ApiKeyAndBodyLimit(mcp.streamable_http_app(), API_KEY, rendering.LIMITS.request_bytes)
    import uvicorn

    uvicorn.run(app, host=_MCP_HOST, port=_MCP_PORT, access_log=False, log_level=os.environ.get("LOG_LEVEL", "warning"))


if __name__ == "__main__":
    main()
