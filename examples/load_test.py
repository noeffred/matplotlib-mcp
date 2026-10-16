"""Exercise a disposable MCP container; requires the dev dependency httpx.

Use --mode probes only against a disposable service: these payloads crashed the
pre-fix implementation. Supply MCP_API_KEY through the environment.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import time

import httpx


KINDS = ["plot", "plot", "scatter", "bar", "bar", "hist", "imshow", "contourf", "pie", "plot"]


def chart(kind, width=800, height=500):
    x = list(range(500))
    y = [math.sin(i / 30) + i / 1000 for i in x]
    data, args, kwargs = {"x": x, "y": y}, [{"$data": "x"}, {"$data": "y"}], {}
    if kind == "bar":
        data = {"x": list(range(30)), "y": list(range(1, 31))}
    elif kind == "hist":
        args, kwargs = [{"$data": "y"}], {"bins": 30}
    elif kind in {"imshow", "contourf"}:
        data = {"z": [[math.sin(i / 5) * math.cos(j / 5) for j in range(40)] for i in range(40)]}
        args = [{"$data": "z"}]
        if kind == "contourf":
            kwargs = {"levels": 15}
    elif kind == "pie":
        data, args, kwargs = {"y": [10, 15, 20, 25, 30]}, [{"$data": "y"}], {"autopct": "%1.1f%%"}
    return {"config": {"figure": {"figsize": [width / 100, height / 100], "dpi": 100},
                       "axes": [{"calls": [{"method": kind, "args": args, "kwargs": kwargs},
                                            {"method": "set_title", "args": ["Load test"]}]}]},
            "data": {"format": "columns", "values": data}}


def probes():
    unused = {"format": "columns", "values": {"unused": [1]}}
    for name, call in [
        ("barbs", {"method": "barbs", "args": [[0], [0], [50_000_000_000], [0]]}),
        ("pie", {"method": "pie", "args": [[1]], "kwargs": {"autopct": "%1000000000.1f"}}),
    ]:
        yield name, {"config": {"axes": [{"calls": [call]}]}, "data": unused}
    call = {"method": "bar", "args": [{"$data": "x"}, {"$data": "y"}]}
    yield "repeated-bars", {"config": {"axes": [{"calls": [call] * 128}]},
                            "data": {"format": "columns", "values": {"x": list(range(10_000)), "y": [1] * 10_000}}}
    yield "violin", {"config": {"axes": [{"calls": [{"method": "violinplot", "args": [{"$data": "y"}], "kwargs": {"points": 250_000}}]}]},
                     "data": {"format": "columns", "values": {"y": [i / 10000 for i in range(10_000)]}}}


async def run(options):
    rows = []
    headers = {"Authorization": "Bearer " + os.environ["MCP_API_KEY"],
               "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-03-26"}
    async with httpx.AsyncClient(headers=headers, trust_env=False,
            limits=httpx.Limits(max_connections=300, max_keepalive_connections=100)) as client:
        async def rpc(method, params, label, reject=False):
            started = time.perf_counter()
            row = {"case": label}
            try:
                response = await client.post(options.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=45)
                row["status"] = response.status_code
                response.raise_for_status()
                payload = response.json()
                if "error" in payload:
                    raise ValueError("JSON-RPC error")
                result = payload["result"]
                if bool(result.get("isError")) != reject:
                    raise ValueError("unexpected MCP tool result")
                if method == "tools/call" and not reject:
                    png = base64.b64decode(next(item["data"] for item in result["content"] if item["type"] == "image"), validate=True)
                    metadata = json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))
                    if not png.startswith(b"\x89PNG\r\n\x1a\n") or struct.unpack(">II", png[16:24]) != (metadata["width"], metadata["height"]):
                        raise ValueError("invalid PNG or dimensions")
                    row["sha256"] = hashlib.sha256(png).hexdigest()
                row["ok"] = True
            except Exception as exc:
                row.update(ok=False, error=type(exc).__name__ + ": " + str(exc)[:200])
            row["seconds"] = time.perf_counter() - started
            rows.append(row)
            return row["ok"]

        async def initialize():
            if not await rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "loadtest", "version": "1"}}, "initialize"):
                raise RuntimeError("MCP initialization failed")

        async def render(index):
            kind = KINDS[index % len(KINDS)]
            return await rpc("tools/call", {"name": "create_chart", "arguments": chart(kind, options.width, options.height)}, kind)

        await initialize()
        started = time.perf_counter()
        if options.mode == "sessions":
            async def session(index):
                await initialize()
                for n in range(options.charts):
                    await render(index * options.charts + n)
            await asyncio.gather(*(session(i) for i in range(options.sessions)))
        elif options.mode == "rate":
            tasks = []
            for i in range(round(options.rate * options.duration)):
                await asyncio.sleep(max(0, started + i / options.rate - time.perf_counter()))
                tasks.append(asyncio.create_task(render(i)))
            await asyncio.gather(*tasks)
        else:
            for name, arguments in probes():
                await rpc("tools/call", {"name": "create_chart", "arguments": arguments}, name, reject=True)
                await rpc("ping", {}, "recovery-ping")
                await render(0)
        elapsed = time.perf_counter() - started
    requests = [row for row in rows if row["case"] != "initialize"]
    latencies = sorted(row["seconds"] for row in requests if row["ok"])
    hashes = {}
    for row in requests:
        if "sha256" in row:
            hashes.setdefault(row["case"], set()).add(row["sha256"])
    summary = {"mode": options.mode, "requests": len(requests), "ok": sum(row["ok"] for row in requests),
               "elapsed_seconds": elapsed, "completed_per_second": len(requests) / elapsed,
               "p50": latencies[len(latencies) // 2] if latencies else None,
               "p95": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else None,
               "max": max(latencies, default=None), "consistent_hashes": all(len(v) == 1 for v in hashes.values())}
    if options.output:
        options.output.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(json.dumps(summary))
    return all(row["ok"] for row in rows) and summary["consistent_hashes"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--mode", choices=["sessions", "rate", "probes"], default="sessions")
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--charts", type=int, default=10)
    parser.add_argument("--rate", type=float, default=1.74)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--width", type=int, default=800)
    parser.add_argument("--height", type=int, default=500)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    if min(options.sessions, options.charts, options.rate, options.duration, options.width, options.height) <= 0:
        parser.error("workload values must be positive")
    raise SystemExit(0 if asyncio.run(run(options)) else 1)
