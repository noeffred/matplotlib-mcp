import json
import asyncio
import logging
import time
import struct
from concurrent.futures import ThreadPoolExecutor

import pytest

from matplotlib_mcp import server
from matplotlib_mcp.registry import PLOT_METHODS


DATA = {"format": "columns", "values": {"x": [1, 2, 3], "y": [2, 4, 3]}}


def simple_config(method="plot", args=None, kwargs=None):
    return {
        "figure": {"figsize": [3, 2], "dpi": 80},
        "axes": [{"subplot": [1, 1, 1], "calls": [
            {"method": method, "args": args or [{"$data": "x"}, {"$data": "y"}], "kwargs": kwargs or {}},
        ]}],
    }


def test_renders_png_with_dimensions_and_mcp_image():
    result = server.create_chart(
        simple_config(),
        {"format": "columns", "values": {"x": [1, 2, 3], "y": [2, 4, 3]}},
    )
    assert result[0]._mime_type == "image/png"
    assert result[0].data.startswith(b"\x89PNG\r\n\x1a\n")
    metadata = json.loads(result[1].text)
    assert metadata["width"] == 240
    assert metadata["height"] == 160


@pytest.mark.parametrize("data", [
    {"format": "columns", "values": {"x": [1, 2], "y": [2, 3]}},
    {"format": "rows", "rows": [{"x": 1, "y": 2}, {"x": 2, "y": 3}]},
    {"format": "csv", "csv": "x,y\n1,2\n2,3\n"},
])
def test_accepts_each_fresh_data_format(data):
    png, width, height = server._render(simple_config(), server._prepare_data(data)[0])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert (width, height) == (240, 160)


def test_catalog_is_backed_by_registry():
    catalog = server._catalog()
    assert set(catalog["plot_methods"]) == set(PLOT_METHODS)
    assert all(item["data_required"] and item["example"] for item in catalog["plot_methods"].values())


def test_unknown_method_is_rejected_before_dispatch():
    cfg = simple_config()
    cfg["axes"][0]["calls"][0]["method"] = "__import__"
    with pytest.raises(server.InputError, match="method is not supported"):
        server._validate_config(cfg)


def test_missing_data_reference_is_rejected():
    cfg = simple_config()
    with pytest.raises(server.InputError, match="missing column"):
        server._render(cfg, {"x": [1, 2]})


def test_unsupported_method_kwargs_are_rejected():
    with pytest.raises(server.InputError, match="unsupported option"):
        server._validate_kwargs("plot", {"exec": "print(1)"})


@pytest.mark.parametrize("csv_text", [
    "x,x\n1,2\n", "x,y\n1\n", "x,y\n1,NaN\n", "x,y\n1,2,3\n", "x,y\n1,1e9999\n",
])
def test_rejects_bad_csv(csv_text):
    with pytest.raises(server.InputError):
        server._prepare_data({"format": "csv", "csv": csv_text})


def test_subplot_and_polar_composition_renders():
    config = {
        "figure": {"figsize": [4, 2], "dpi": 80, "tight_layout": True},
        "axes": [
            {"subplot": [1, 2, 1], "calls": [{"method": "plot", "args": [{"$data": "theta"}, {"$data": "r"}]}]},
            {"subplot": [1, 2, 2], "projection": "polar", "calls": [{"method": "scatter", "args": [{"$data": "theta"}, {"$data": "r"}]}]},
        ],
    }
    png, _, _ = server._render(config, {"theta": [0.1, 0.5, 0.8], "r": [1, 2, 3]})
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("method,args", [
    ("plot", ["x", "y"]), ("scatter", ["x", "y"]), ("bar", ["x", "y"]),
    ("barh", ["x", "y"]), ("step", ["x", "y"]), ("stem", ["x", "y"]),
    ("stairs", ["y", "edges"]), ("fill", ["x", "y"]),
    ("fill_between", ["x", "y", "y2"]), ("fill_betweenx", ["y", "x", "x2"]),
    ("stackplot", ["x", "y", "y2"]), ("hist", ["y"]), ("boxplot", [["y"]]),
    ("violinplot", [["y"]]), ("eventplot", [["y"]]), ("ecdf", ["y"]),
    ("errorbar", ["x", "y"]), ("hist2d", ["x", "y"]), ("hexbin", ["x", "y"]),
    ("pie", ["y"]), ("imshow", ["z"]), ("pcolormesh", ["xg", "yg", "z"]),
    ("contour", ["xg", "yg", "z"]), ("contourf", ["xg", "yg", "z"]),
    ("quiver", ["xg", "yg", "u", "v"]), ("barbs", ["xg", "yg", "u", "v"]),
    ("streamplot", ["xshort", "yshort", "u", "v"]),
    ("triplot", ["xt", "yt"]), ("tripcolor", ["xt", "yt", "zt"]),
    ("tricontour", ["xt", "yt", "zt"]), ("tricontourf", ["xt", "yt", "zt"]),
])
def test_every_registered_chart_method_renders(method, args):
    data = {
        "x": [0, 1, 2, 3], "x2": [1, 2, 3, 4], "y": [1, 3, 2, 4],
        "y2": [2, 4, 3, 5], "edges": [0, 1, 2, 3, 4],
        "xshort": [0, 1, 2], "yshort": [0, 1, 2],
        "xg": [[0, 1, 2], [0, 1, 2], [0, 1, 2]],
        "yg": [[0, 0, 0], [1, 1, 1], [2, 2, 2]],
        "z": [[1, 2, 3], [2, 3, 4], [3, 4, 5]],
        "u": [[1, 1, 1], [1, 1, 1], [1, 1, 1]],
        "v": [[0, 0, 0], [1, 1, 1], [0, 0, 0]],
        "xt": [0, 1, 0, 1], "yt": [0, 0, 1, 1], "zt": [1, 2, 3, 4],
    }

    def reference(value):
        if isinstance(value, list):
            return [reference(item) for item in value]
        return {"$data": value}

    config = {
        "figure": {"figsize": [2, 1.5], "dpi": 60},
        "axes": [{"calls": [{"method": method, "args": [reference(item) for item in args]}]}],
    }
    result = server.create_chart(config, {"format": "columns", "values": data})
    assert result[0].data.startswith(b"\x89PNG\r\n\x1a\n")


def test_api_key_and_request_size_middleware():
    async def run_case(authorization, body, max_bytes):
        invoked = False
        sent = []

        async def inner(scope, receive, send):
            nonlocal invoked
            invoked = True
            message = await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": message["body"]})

        middleware = server.ApiKeyAndBodyLimit(inner, "testing-key-that-is-long-enough", max_bytes)
        messages = iter([{"type": "http.request", "body": body, "more_body": False}])

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        await middleware({"type": "http", "headers": [(b"authorization", authorization)]}, receive, send)
        return invoked, sent

    invoked, sent = asyncio.run(run_case(b"Bearer wrong", b"{}", 100))
    assert not invoked and sent[0]["status"] == 401
    invoked, sent = asyncio.run(run_case(b"Bearer testing-key-that-is-long-enough", b"12345", 4))
    assert not invoked and sent[0]["status"] == 413
    invoked, sent = asyncio.run(run_case(b"Bearer testing-key-that-is-long-enough", b"{}", 0))
    assert invoked and sent[0]["status"] == 200


def test_zero_disables_configured_limits(monkeypatch):
    monkeypatch.setattr(server, "LIMITS", server.Limits(rows=0, columns=0, array_elements=0, data_bytes=0))
    columns, rows, _ = server._prepare_data({"format": "columns", "values": {"x": [1, 2], "y": [3, 4]}})
    assert rows == 2 and set(columns) == {"x", "y"}


def test_audit_log_contains_time_caller_and_action_without_payload():
    class Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    capture = Capture()
    server._audit_logger.addHandler(capture)
    try:
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "create_chart", "arguments": {"secret_label": "do not log this"}},
        }).encode()
        actions = server._audit_action(body, {})
        server._audit({"client": ("192.0.2.4", 4567), "method": "POST", "path": "/mcp", "http_version": "1.1"}, actions[0], 200, time.monotonic())
    finally:
        server._audit_logger.removeHandler(capture)

    message = server._audit_logger.handlers[0].formatter.format(capture.records[0])
    assert "INFO matplotlib_mcp.access:" in message
    assert '192.0.2.4:4567 - "POST /mcp HTTP/1.1" 200' in message
    assert "action=create_chart" in message
    assert "do not log this" not in message
    assert " UTC INFO " in message


def test_concurrent_tool_calls_return_isolated_previews():
    configs = [simple_config("bar") if index % 2 else simple_config() for index in range(4)]
    inputs = [
        (config, {"format": "columns", "values": {"x": [1, 2], "y": [index + 1, index + 2]}})
        for index, config in enumerate(configs)
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda pair: server.create_chart(*pair), inputs))
    assert len(results) == 4
    assert all(result[0].data.startswith(b"\x89PNG\r\n\x1a\n") for result in results)


def test_column_vectors_can_have_different_lengths():
    data = {"format": "columns", "values": {"values": [3, 2], "edges": [0, 1, 2]}}
    config = simple_config("stairs", [{"$data": "values"}, {"$data": "edges"}])
    result = server.create_chart(config, data)
    assert json.loads(result[1].text)["rows"] == 3


def test_row_limit_checks_every_column(monkeypatch):
    monkeypatch.setattr(server, "LIMITS", server.Limits(rows=2))
    with pytest.raises(server.InputError, match="row count"):
        server._prepare_data({"format": "columns", "values": {"a": [1], "b": [1, 2, 3]}})


def test_reported_dimensions_match_png_for_fractional_pixels():
    config = simple_config()
    config["figure"] = {"figsize": [3.009, 2.009], "dpi": 100}
    result = server.create_chart(config, DATA)
    metadata = json.loads(result[1].text)
    assert (metadata["width"], metadata["height"]) == struct.unpack(">II", result[0].data[16:24])


@pytest.mark.parametrize("figure", [
    {"figsize": [1e308, 1], "dpi": 100},
    {"figsize": [10 ** 400, 1]},
    {"dpi": True},
    {"figsize": [True, 1]},
])
def test_rejects_invalid_figure_dimensions(figure):
    config = simple_config()
    config["figure"] = figure
    with pytest.raises(server.InputError):
        server._validate_config(config)


def test_rejects_oversized_subplot_grid():
    config = simple_config()
    config["axes"][0]["subplot"] = [1_000_000, 1_000_000, 1]
    with pytest.raises(server.InputError, match="subplot grid cells"):
        server._validate_config(config)


@pytest.mark.parametrize("method,args,kwargs", [
    ("hist", [[1, 2], 201], {}),
    ("hist", [[1, 2]], {"bins": list(range(202))}),
    ("hist", [[1, 2]], {"bins": "auto"}),
    ("hist2d", [[1, 2], [1, 2], [201, 2]], {}),
    ("hist2d", [[1, 2], [1, 2]], {"bins": [2, list(range(202))]}),
    ("hexbin", [[1, 2], [1, 2]], {"gridsize": 100_000}),
    ("hexbin", [[1, 2], [1, 2]], {"bins": 1_000_000}),
    ("contour", [[[1, 2], [2, 3]], 201], {}),
    ("contourf", [[[1, 2], [2, 3]]], {"levels": list(range(201))}),
    ("tricontour", [[0, 1, 0], [0, 0, 1], [1, 2, 3], 201], {}),
])
def test_allocation_limits_cover_positional_and_vector_options(method, args, kwargs):
    with pytest.raises(server.InputError):
        server.create_chart(simple_config(method, args, kwargs), DATA)


def test_bin_limit_applies_after_resolving_data():
    config = simple_config("hist", [[1, 2]], {"bins": {"$data": "edges"}})
    with pytest.raises(server.InputError, match="histogram bins"):
        server.create_chart(config, {"format": "columns", "values": {"edges": list(range(202))}})


def test_zero_disables_bin_limit(monkeypatch):
    monkeypatch.setattr(server, "LIMITS", server.Limits(bins=0))
    result = server.create_chart(simple_config("hist", [[1, 2]], {"bins": "auto"}), DATA)
    assert result[0].data.startswith(b"\x89PNG")


@pytest.mark.parametrize("method,kwargs", [
    ("streamplot", {"density": 1_000_000}),
    ("violinplot", {"points": 1_000_000}),
])
def test_generated_sampling_sizes_are_bounded(method, kwargs):
    with pytest.raises(server.InputError, match="configured limit"):
        server.create_chart(simple_config(method, [[1, 2]], kwargs), DATA)


def test_positional_streamplot_density_is_bounded():
    config = simple_config("streamplot", [[0, 1], [0, 1], [[1, 1], [1, 1]], [[0, 0], [0, 0]], 1_000_000])
    with pytest.raises(server.InputError, match="density grid"):
        server.create_chart(config, DATA)


def test_inline_configuration_arrays_are_bounded(monkeypatch):
    monkeypatch.setattr(server, "LIMITS", server.Limits(array_elements=20))
    with pytest.raises(server.InputError, match="configuration element count"):
        server.create_chart(simple_config("plot", [list(range(21))]), DATA)


@pytest.mark.parametrize("properties", [
    {"usetex": True}, {"parse_math": True},
    {"fontproperties": {"fname": "/tmp/font.ttf"}},
    {"fontproperties": "sans:file=/tmp/font.ttf"},
])
def test_nested_text_properties_cannot_enable_tex_or_font_files(properties):
    config = simple_config("set_title", ["Private title"], {"fontdict": properties})
    with pytest.raises(server.InputError, match="not supported"):
        server.create_chart(config, DATA)


@pytest.mark.parametrize("method,kwargs", [
    ("set_title", {"label": "Chart"}),
    ("set_xlabel", {"xlabel": "X"}),
    ("set_xticks", {"ticks": [1, 2]}),
    ("axhspan", {"ymin": 1, "ymax": 2}),
])
def test_setup_methods_accept_required_keyword_arguments(method, kwargs):
    config = {"axes": [{"calls": [{"method": method, "kwargs": kwargs}]}]}
    assert server.create_chart(config, DATA)[0].data.startswith(b"\x89PNG")


def test_matplotlib_warnings_do_not_expose_chart_labels(capsys, caplog):
    config = simple_config("set_title", ["\U0010ffff"], {"fontdict": {"family": "private-font-family"}})
    server.create_chart(config, DATA)
    assert "private-font-family" not in caplog.text
    assert "Glyph" not in capsys.readouterr().err


def test_catalog_csv_example_is_usable():
    columns, rows, _ = server._prepare_data(server._catalog()["data_formats"]["csv"])
    assert rows == 2 and columns == {"x": [1, 2], "y": [3, 4]}


def test_middleware_replays_body_then_waits_for_real_disconnect():
    async def run():
        delivered = []
        messages = iter([
            {"type": "http.request", "body": b"first", "more_body": True},
            {"type": "http.request", "body": b"second", "more_body": False},
            {"type": "http.request", "body": b"", "more_body": False},
            {"type": "http.disconnect"},
        ])

        async def receive():
            return next(messages)

        async def send(message):
            pass

        async def inner(scope, receive, send):
            delivered.extend([await receive(), await receive(), await receive()])

        middleware = server.ApiKeyAndBodyLimit(inner, "testing-key-that-is-long-enough", 100)
        scope = {"type": "http", "headers": [(b"authorization", b"Bearer testing-key-that-is-long-enough")]}
        await middleware(scope, receive, send)
        assert delivered[0] == {"type": "http.request", "body": b"firstsecond", "more_body": False}
        assert delivered[1]["type"] == "http.request"
        assert delivered[2]["type"] == "http.disconnect"

    asyncio.run(run())


def test_middleware_does_not_dispatch_incomplete_body():
    async def run():
        async def inner(scope, receive, send):
            pytest.fail("incomplete request must not reach MCP")

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            pytest.fail("cannot respond after disconnect")

        middleware = server.ApiKeyAndBodyLimit(inner, "testing-key-that-is-long-enough", 100)
        await middleware({"type": "http", "headers": [(b"authorization", b"Bearer testing-key-that-is-long-enough")]}, receive, send)

    asyncio.run(run())


def test_chart_tool_keeps_event_loop_responsive(monkeypatch):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class FakeRenderer:
            async def render(self, config, data):
                started.set()
                await release.wait()
                return b"png", {"width": 1}

        monkeypatch.setattr(server, "_renderer", lambda: FakeRenderer())
        task = asyncio.create_task(server._create_chart_tool({}, {}))
        await asyncio.wait_for(started.wait(), 2)
        assert not task.done()
        release.set()
        assert (await task)[0].data == b"png"

    asyncio.run(run())


def test_mcp_http_initialize_list_and_render():
    import httpx

    http_app = server.mcp.streamable_http_app()
    app = server.ApiKeyAndBodyLimit(http_app, server.API_KEY, server.LIMITS.request_bytes)
    headers = {
        "Authorization": f"Bearer {server.API_KEY}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-03-26",
    }
    async def run():
        async with http_app.router.lifespan_context(http_app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000", headers=headers,
        ) as client:
            async def request(method, params=None):
                response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
                assert response.status_code == 200, response.text
                return response.json()["result"]

            initialized = await request("initialize", {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            })
            assert initialized["serverInfo"]["name"] == "Matplotlib Charts"
            assert {tool["name"] for tool in (await request("tools/list"))["tools"]} == {"create_chart", "list_chart_types"}
            result = await request("tools/call", {"name": "create_chart", "arguments": {"config": simple_config(), "data": DATA}})
            assert not result.get("isError")
            assert result["content"][0]["type"] == "image"
            assert result["content"][0]["mimeType"] == "image/png"
            process = server._renderer().process
            for _ in range(2):
                await request("ping")
                result = await request("tools/call", {"name": "create_chart", "arguments": {"config": simple_config(), "data": DATA}})
                assert not result.get("isError")
                assert server._renderer().process is process
            await server._renderer().close()

    asyncio.run(run())


def test_deeply_nested_audit_payload_does_not_crash():
    assert server._audit_action(b"[" * 2000 + b"]" * 2000, {}) == ["request"]


def test_unknown_paths_do_not_leak_secrets_to_access_logs():
    class Capture(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    messages = []
    handler = Capture()
    server._audit_logger.addHandler(handler)
    try:
        server._audit({"path": "/private-token", "method": "GET"}, "request", 404, time.monotonic())
    finally:
        server._audit_logger.removeHandler(handler)
    assert "private-token" not in messages[0]

