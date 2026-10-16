"""Regression tests for bounded rendering and recovery after worker failures."""

import asyncio
from dataclasses import replace
import json
import sys

import pytest

from matplotlib_mcp import rendering, server
from matplotlib_mcp.complexity import Budget, validate_autopct
from matplotlib_mcp.supervisor import Renderer, RenderError, RenderSettings
from test_server import DATA, simple_config


@pytest.mark.parametrize("method,args,kwargs", [
    ("barbs", [[0], [0], [50_000_000_000], [0]], {}),
    ("barbs", [[0], [0], [1], [0]], {"barb_increments": {"flag": 1e-100}}),
    ("barbs", [[0], [0], [1], [0]], {"barb_increments": {"half": 0}}),
    ("barbs", [[0], [0], [1], [0]], {"barb_increments": {"full": -1}}),
    ("barbs", [[0], [0], [1], [0]], {"barb_increments": {"full": float("inf")}}),
    ("pie", [[1]], {"autopct": "%1000000000.1f"}),
    ("pie", [[1], None, None, None, "%1000000000.1f"], {}),
    ("hexbin", [[0, 1], [0, 1], None, 200], {}),
    ("pie", [[1]], {"autopct": "%.1000000000f"}),
    ("pie", [[1]], {"autopct": "%s"}),
    ("pie", [[1]], {"autopct": "%f%f"}),
    ("violinplot", [list(range(10_000))], {"points": 250_000}),
    ("violinplot", [[list(range(1000)), list(range(2000))]], {"points": 2000}),
])
def test_dangerous_calls_rejected_before_figure_creation(method, args, kwargs, monkeypatch):
    def no_figure(*args, **kwargs):
        pytest.fail("validation must finish before creating a figure")
    monkeypatch.setattr(rendering, "Figure", no_figure)
    with pytest.raises(server.InputError):
        rendering.create_chart(simple_config(method, args, kwargs), DATA)


@pytest.mark.parametrize("rows", [100, 10_000])
def test_references_are_charged_across_calls_and_axes(monkeypatch, rows):
    call = {"method": "bar", "args": [{"$data": "x"}, {"$data": "y"}]}
    cfg = {"axes": [{"subplot": [1, 2, i], "calls": [call] * 64} for i in (1, 2)]}
    data = {"format": "columns", "values": {"x": list(range(rows)), "y": [1] * rows}}
    monkeypatch.setattr(rendering, "Figure", lambda **kwargs: pytest.fail("created figure"))
    with pytest.raises(server.InputError, match="chart artists"):
        rendering.create_chart(cfg, data)


def test_reference_cache_reuses_arrays():
    result = rendering._resolve([{ "$data": "x"}, {"$data": "x"}], {"x": [1, 2]})
    assert result[0] is result[1]


def test_broadcast_bars_and_budget_boundaries():
    budget = Budget(replace(rendering.LIMITS, artists=6, geometry=24))
    budget.charge("bar", [[1, 2], 1], {})
    assert budget.artists == 6 and budget.geometry == 24
    with pytest.raises(server.InputError, match="artists"):
        budget.charge("bar", [[1], [1]], {})


def test_violin_ragged_groups_and_barbs_valid():
    Budget(rendering.LIMITS).charge("violinplot", [[[1, 2], [1, 2, 3]]], {"points": 100})
    Budget(rendering.LIMITS).charge("barbs", [[0], [0], [50], [0]], {"barb_increments": {"flag": 25}})
    Budget(rendering.LIMITS).charge("barbs", [[50], [0]], {})
    validate_autopct("%1.1f%%", 500)
    validate_autopct("%499.1f!", 500)
    with pytest.raises(server.InputError):
        validate_autopct("%500.1f!", 500)


def test_pixel_budget():
    cfg = simple_config()
    cfg["figure"] = {"figsize": [16, 16], "dpi": 100}
    with pytest.raises(server.InputError, match="pixels"):
        rendering._validate_config(cfg)
    cfg["figure"]["figsize"] = [16, 6.25]
    assert rendering._validate_config(cfg)[1:3] == (1600, 625)


@pytest.mark.parametrize("field", RenderSettings.__dataclass_fields__)
def test_supervision_settings_cannot_be_disabled(field, monkeypatch):
    monkeypatch.setenv("RENDER_TIMEOUT_SECONDS" if field == "render_seconds" else "RENDER_" + field.upper(), "0")
    with pytest.raises(RuntimeError, match="positive"):
        RenderSettings.from_env()


def test_process_recycling_and_real_chart_recovery():
    async def run():
        r = Renderer(replace(RenderSettings(), recycle_charts=2))
        try:
            png, _ = await r.render(simple_config(), DATA)
            assert png.startswith(b"\x89PNG")
            first = r.process
            await r.render(simple_config(), DATA)
            assert r.process is None and first.returncode is not None
            await r.render(simple_config(), DATA)
            assert r.process.pid != first.pid
        finally:
            process = r.process
            await r.close()
            assert process is None or process.returncode is not None
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["exit", "memory", "deadline", "cancel"])
def test_real_process_failure_releases_capacity(mode):
    async def run():
        r = Renderer()
        original = r._start
        spawned = []

        async def faulty_start():
            # Real child processes, with the same OS allocation cap as production.
            code = 'import sys, resource, time; resource.setrlimit(resource.RLIMIT_AS, (268435456,)*2); sys.stdin.readline(); '
            if mode == "exit":
                code += 'sys.exit(7)'
            elif mode == "memory":
                code += '\ntry: x=bytearray(1024**3)\nexcept MemoryError: print(\'{"error":"memory limit","fatal":true}\', flush=True)'
            else:
                code += 'time.sleep(60)'
            r.process = await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", code,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            spawned.append(r.process)
            r._start = original

        r._start = faulty_start
        if mode == "deadline":
            r.settings = replace(r.settings, render_seconds=0.15)
        try:
            if mode == "cancel":
                task = asyncio.create_task(r.render(simple_config(), DATA))
                while not spawned:
                    await asyncio.sleep(.01)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(RenderError):
                    await r.render(simple_config(), DATA)
            assert spawned[0].returncode is not None
            assert not r.lock.locked()
            r.settings = RenderSettings()
            png, _ = await r.render(simple_config(), DATA)
            assert png.startswith(b"\x89PNG")
        finally:
            await r.close()
    asyncio.run(run())



@pytest.mark.parametrize("api_key", [None, "short"])
def test_worker_renders_without_application_configuration(monkeypatch, api_key):
    # These would prevent server.py from importing in a fresh interpreter.
    if api_key is None:
        monkeypatch.delenv("MCP_API_KEY", raising=False)
    else:
        monkeypatch.setenv("MCP_API_KEY", api_key)
    monkeypatch.setenv("MCP_PORT", "invalid")

    async def run():
        r = Renderer()
        try:
            png, metadata = await r.render(simple_config(), DATA)
            assert png.startswith(b"\x89PNG")
            assert (metadata["width"], metadata["height"]) == (240, 160)
        finally:
            await r.close()
    asyncio.run(run())


def test_rendering_import_does_not_initialize_application(monkeypatch):
    import os
    import subprocess

    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_PORT", "invalid")
    code = """
import logging
import sys
from matplotlib_mcp import rendering

assert "matplotlib_mcp.server" not in sys.modules
assert "matplotlib_mcp.supervisor" not in sys.modules
assert "mcp.server.fastmcp" not in sys.modules
assert not logging.getLogger("matplotlib_mcp.access").handlers
"""
    subprocess.run([sys.executable, "-c", code], env=os.environ.copy(),
                   check=True, capture_output=True, timeout=30)


def test_worker_starts_with_enforced_limit_and_single_blas_thread():
    async def run():
        r = Renderer()
        try:
            await r.render(simple_config(), DATA)
            from pathlib import Path
            limits = Path(f"/proc/{r.process.pid}/limits").read_text()
            assert str(r.settings.memory_bytes) in next(line for line in limits.splitlines() if "Max address space" in line)
            env = Path(f"/proc/{r.process.pid}/environ").read_bytes().split(b"\0")
            assert b"OPENBLAS_NUM_THREADS=1" in env
        finally:
            await r.close()
    asyncio.run(run())


async def _request(middleware, body, disconnect=None):
    sent = []
    delivered = False
    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        if disconnect is None:
            await asyncio.Future()
        await disconnect.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        sent.append(message)
    await middleware({"type": "http", "headers": [(b"authorization", b"Bearer " + server.API_KEY.encode())]}, receive, send)
    return sent


def test_http_queue_capacity_expiration_and_ping(monkeypatch):
    async def run():
        monkeypatch.setattr(server, "SETTINGS", replace(server.SETTINGS, queue_size=1, queue_seconds=.1))
        started, release = asyncio.Event(), asyncio.Event()
        async def app(scope, receive, send):
            body = (await receive())["body"]
            if b"create_chart" in body:
                started.set()
                await release.wait()
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"{}"})
        middleware = server.ApiKeyAndBodyLimit(app, server.API_KEY, 1000)
        body = json.dumps({"method": "tools/call", "params": {"name": "create_chart"}}).encode()
        active = asyncio.create_task(_request(middleware, body))
        await started.wait()
        queued = asyncio.create_task(_request(middleware, body))
        while middleware.waiting != 1:
            await asyncio.sleep(0)
        busy = await _request(middleware, body)
        assert busy[0]["status"] == 429 and (b"retry-after", b"1") in busy[0]["headers"]
        assert (await _request(middleware, b'{"method":"ping"}'))[0]["status"] == 200
        assert (await queued)[0]["status"] == 503
        release.set()
        assert (await active)[0]["status"] == 200
        assert middleware.waiting == middleware.buffered_bytes == 0
    asyncio.run(run())


def test_http_byte_capacity_and_queued_disconnect(monkeypatch):
    async def run():
        body = b'{"method":"tools/call","params":{"name":"create_chart"}}'
        monkeypatch.setattr(server, "SETTINGS", replace(server.SETTINGS, queue_bytes=len(body) * 2))
        started, release, disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def app(scope, receive, send):
            await receive()
            started.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 200})
        middleware = server.ApiKeyAndBodyLimit(app, server.API_KEY, 1000)
        active = asyncio.create_task(_request(middleware, body))
        await started.wait()
        queued = asyncio.create_task(_request(middleware, body, disconnected))
        while middleware.waiting != 1:
            await asyncio.sleep(0)
        assert (await _request(middleware, body))[0]["status"] == 429
        disconnected.set()
        assert await queued == []
        assert middleware.waiting == 0 and middleware.buffered_bytes == len(body)
        release.set()
        await active
        assert middleware.buffered_bytes == 0
    asyncio.run(run())


def test_http_application_lifespan_reaps_renderer():
    async def run():
        r = server._renderer()
        await r.render(simple_config(), DATA)
        process = r.process
        messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        async def receive():
            return next(messages)
        async def send(message):
            pass
        async def app(scope, receive, send):
            assert (await receive())["type"] == "lifespan.startup"
            await send({"type": "lifespan.startup.complete"})
            assert (await receive())["type"] == "lifespan.shutdown"
            await send({"type": "lifespan.shutdown.complete"})
        middleware = server.ApiKeyAndBodyLimit(app, server.API_KEY, 1000)
        await middleware({"type": "lifespan"}, receive, send)
        assert process.returncode is not None
        assert asyncio.get_running_loop() not in server._RENDERERS
    asyncio.run(run())


def test_simultaneous_admission_does_not_overshoot_queue(monkeypatch):
    async def run():
        monkeypatch.setattr(server, "SETTINGS", replace(server.SETTINGS, queue_size=2))
        started, release = asyncio.Event(), asyncio.Event()
        async def app(scope, receive, send):
            await receive()
            started.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 200})
        middleware = server.ApiKeyAndBodyLimit(app, server.API_KEY, 1000)
        body = b'{"method":"tools/call","params":{"name":"create_chart"}}'
        tasks = [asyncio.create_task(_request(middleware, body)) for _ in range(10)]
        await started.wait()
        async with asyncio.timeout(2):
            while sum(task.done() for task in tasks) < 7:
                await asyncio.sleep(0)
        assert middleware.waiting == 2
        release.set()
        replies = await asyncio.gather(*tasks)
        assert sum(reply[0]["status"] == 429 for reply in replies) == 7
        assert middleware.buffered_bytes == 0
    asyncio.run(run())


def test_partial_bodies_share_byte_budget(monkeypatch):
    async def run():
        monkeypatch.setattr(server, "SETTINGS", replace(server.SETTINGS, queue_bytes=100))
        partial, disconnect = asyncio.Event(), asyncio.Event()
        async def app(scope, receive, send):
            pytest.fail("partial/rejected bodies must not reach MCP")
        middleware = server.ApiKeyAndBodyLimit(app, server.API_KEY, 1000)
        first = True
        async def receive():
            nonlocal first
            if first:
                first = False
                partial.set()
                return {"type": "http.request", "body": b"x" * 70, "more_body": True}
            await disconnect.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            pytest.fail("cannot reply to disconnected request")
        scope = {"type": "http", "headers": [(b"authorization", b"Bearer " + server.API_KEY.encode())]}
        task = asyncio.create_task(middleware(scope, receive, send))
        await partial.wait()
        assert middleware.buffered_bytes == 70
        assert (await _request(middleware, b"y" * 50))[0]["status"] == 429
        disconnect.set()
        await task
        assert middleware.buffered_bytes == middleware.reading == 0
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["stalled", "partial", "trickle"])
def test_body_deadline_releases_saturated_capacity(monkeypatch, mode):
    async def run():
        monkeypatch.setattr(server, "SETTINGS", replace(
            server.SETTINGS, queue_size=1, queue_bytes=100, body_seconds=.15,
        ))
        received = []
        async def app(scope, receive, send):
            received.append((await receive())["body"])
            await send({"type": "http.response.start", "status": 200})
        middleware = server.ApiKeyAndBodyLimit(app, server.API_KEY, 1000)
        scope = {"type": "http", "headers": [(b"authorization", b"Bearer " + server.API_KEY.encode())]}
        replies = [[] for _ in range(5)]
        chunks = [0] * 5
        cancelled = [False] * 5

        async def upload(index):
            async def receive():
                try:
                    if mode == "stalled" or (mode == "partial" and chunks[index]):
                        await asyncio.Future()
                    if chunks[index]:
                        await asyncio.sleep(.02)
                    chunks[index] += 1
                    return {"type": "http.request", "body": b"x", "more_body": True}
                except asyncio.CancelledError:
                    cancelled[index] = True
                    raise
            async def send(message):
                replies[index].append(message)
            await middleware(scope, receive, send)

        async with asyncio.timeout(2):
            tasks = [asyncio.create_task(upload(i)) for i in range(5)]
            try:
                while middleware.reading != 5:
                    await asyncio.sleep(0)
                assert middleware.inflight == 5
                if mode != "stalled":
                    assert middleware.buffered_bytes >= 5
                assert (await _request(middleware, b'{"method":"ping"}'))[0]["status"] == 429
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        assert all(reply[0]["status"] == 408 for reply in replies)
        assert all(json.loads(reply[1]["body"]) == {"error": "request body deadline exceeded"} for reply in replies)
        assert all(cancelled)
        if mode == "trickle":
            assert all(count > 1 for count in chunks)
        assert not received
        assert middleware.reading == middleware.inflight == middleware.waiting == middleware.buffered_bytes == 0
        assert not middleware.chart_lock.locked()
        assert (await _request(middleware, b'{"method":"ping"}'))[0]["status"] == 200
        assert received == [b'{"method":"ping"}']
    asyncio.run(run())


def test_queued_chart_survives_renderer_exit():
    async def run():
        r = Renderer()
        original = r._start
        started = asyncio.Event()
        async def failing_start():
            r.process = await asyncio.create_subprocess_exec(sys.executable, "-u", "-c",
                'import sys,time; sys.stdin.readline(); time.sleep(.2); sys.exit(7)',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            r._start = original
            started.set()
        r._start = failing_start
        first = asyncio.create_task(r.render(simple_config(), DATA))
        await started.wait()
        second = asyncio.create_task(r.render(simple_config(), DATA))
        try:
            with pytest.raises(RenderError):
                await first
            png, _ = await second
            assert png.startswith(b"\x89PNG") and r.waiting == 0
        finally:
            await r.close()
    asyncio.run(run())


def test_large_control_bodies_wait_before_json_parsing(monkeypatch):
    async def run():
        parsed = []
        validated = []
        original = server._audit_action
        original_encoding_check = server._http_json_encoding_valid
        def encoding_check(body):
            validated.append(body)
            return original_encoding_check(body)
        def audit(body, headers):
            parsed.append(body)
            return original(body, headers)
        monkeypatch.setattr(server, "_audit_action", audit)
        monkeypatch.setattr(server, "_http_json_encoding_valid", encoding_check)
        started, release = asyncio.Event(), asyncio.Event()
        async def app(scope, receive, send):
            body = (await receive())["body"]
            if b"create_chart" in body:
                started.set()
                await release.wait()
            await send({"type": "http.response.start", "status": 200})
        middleware = server.ApiKeyAndBodyLimit(app, server.API_KEY, 20000)
        active = asyncio.create_task(_request(middleware, b'{"method":"tools/call","params":{"name":"create_chart"}}'))
        await started.wait()
        large = json.dumps({"method": "ping", "params": {"padding": "x" * 9000}}).encode()
        queued = asyncio.create_task(_request(middleware, large))
        while middleware.waiting != 1:
            await asyncio.sleep(0)
        assert large not in parsed
        assert large not in validated
        assert (await _request(middleware, b'{"method":"ping"}'))[0]["status"] == 200
        release.set()
        await active
        assert (await queued)[0]["status"] == 200
        assert large in parsed and middleware.inflight == 0
        assert large in validated
    asyncio.run(run())


def test_small_control_request_count_is_bounded(monkeypatch):
    async def run():
        monkeypatch.setattr(server, "SETTINGS", replace(server.SETTINGS, queue_size=1))
        release = asyncio.Event()
        async def app(scope, receive, send):
            await receive()
            await release.wait()
            await send({"type": "http.response.start", "status": 200})
        middleware = server.ApiKeyAndBodyLimit(app, server.API_KEY, 1000)
        tasks = [asyncio.create_task(_request(middleware, b'{"method":"ping"}')) for _ in range(10)]
        async with asyncio.timeout(2):
            while not any(task.done() for task in tasks):
                await asyncio.sleep(0)
        assert middleware.inflight == 9
        release.set()
        replies = await asyncio.gather(*tasks)
        assert sum(reply[0]["status"] == 429 for reply in replies) == 1
        assert middleware.inflight == 0
    asyncio.run(run())


def test_http_json_scan_handles_strings_escapes_and_boundaries(monkeypatch):
    monkeypatch.setattr(server, "SETTINGS", replace(server.SETTINGS, json_tokens=7, json_depth=2))
    assert server._http_json_within_budget(b'{"x":[1,2]}')  # seven structural units
    assert not server._http_json_within_budget(b'{"x":[1,2,3]}')
    assert not server._http_json_within_budget(b'[[[]]]')
    assert server._http_json_within_budget(json.dumps('{[\\"x\":0]}' * 100).encode())
    assert server._http_json_within_budget(json.dumps('\\\\' * 100).encode())


def test_http_rejects_dense_json_before_decoder(monkeypatch):
    async def run():
        def no_parse(*args):
            pytest.fail("dense/deep JSON must be rejected before decoding")
        monkeypatch.setattr(server, "_audit_action", no_parse)
        async def app(scope, receive, send):
            pytest.fail("rejected JSON must not reach MCP")
        middleware = server.ApiKeyAndBodyLimit(app, server.API_KEY, 4 * 1024 * 1024)
        dense = b'[' + b'{"":0},' * 60_000 + b'{}]'
        assert (await _request(middleware, dense))[0]["status"] == 413
        assert (await _request(middleware, b'[' * 65 + b']' * 65))[0]["status"] == 413
        assert middleware.inflight == middleware.buffered_bytes == 0
    asyncio.run(run())


@pytest.mark.parametrize("invalid", [b"\xff", b"\xc0\xaf", b"\xe2\x82", b"\x00"])
@pytest.mark.parametrize("padding_size", [0, 9000])
def test_http_rejects_invalid_encoding_before_scan_or_decoders(monkeypatch, invalid, padding_size):
    async def run():
        def rejected(*args):
            pytest.fail("invalid encoding must be rejected before scanning or decoding")
        async def app(scope, receive, send):
            pytest.fail("invalid encoding must not reach MCP")
        middleware = server.ApiKeyAndBodyLimit(app, server.API_KEY, 20000)
        with monkeypatch.context() as patch:
            patch.setattr(server, "_http_json_within_budget", rejected)
            patch.setattr(server, "_audit_action", rejected)
            body = b'{"padding":"' + b"x" * padding_size + invalid + b'"}'
            reply = await _request(middleware, body)
            assert reply[0]["status"] == 400
            assert middleware.reading == middleware.inflight == middleware.waiting == middleware.buffered_bytes == 0
            assert not middleware.chart_lock.locked()
        async def healthy_app(scope, receive, send):
            await receive()
            await send({"type": "http.response.start", "status": 200})
        middleware.app = healthy_app
        assert (await _request(middleware, b'{"method":"ping"}'))[0]["status"] == 200
    asyncio.run(run())


def test_legacy_positional_pie_format_still_renders():
    result = rendering.create_chart(simple_config("pie", [[1], None, None, None, "%1.1f%%"]), DATA)
    assert result[0].startswith(b"\x89PNG")
