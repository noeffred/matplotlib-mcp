# Matplotlib MCP Server

A stateless MCP server that renders Matplotlib charts from data supplied with each call. It exposes exactly two tools: `list_chart_types` and `create_chart`.

## Install and run

Requires Python 3.11 or newer. Install with `pip install .`, then set an API key and run:

```sh
export MCP_API_KEY='replace-with-a-long-random-secret'
matplotlib-mcp
```

The server listens on `http://127.0.0.1:8000/mcp` by default. Set `MCP_HOST` and `MCP_PORT` to change the bind address and port. Set `MCP_ALLOWED_HOSTS` to a comma-separated list of accepted HTTP `Host` header values when clients connect through another hostname or reverse proxy. Put it behind HTTPS for remote clients. The proxy must forward `Authorization` and should disable request-body logs and disk buffering. Disable swap and core dumps where the deployment handles sensitive data. The server itself does not write chart data or images to disk and disables Uvicorn access logs.

Application access logs follow a FastAPI/Uvicorn style: UTC timestamp and level, logger name, client address, HTTP method/path/version, status, tool/protocol action, and duration. They do not include request bodies, chart labels, data, or API keys. Behind a reverse proxy, the client address is the proxy unless it terminates the connection directly at the application host.

Every HTTP request must include `Authorization: Bearer <MCP_API_KEY>`. Keep the key at least 16 characters long. `LOG_LEVEL` controls Uvicorn's non-access logs.

`sh start.sh` uses the same environment settings and refuses to start without a key. Keep local secrets in environment variables; `.env` files are ignored by Git but are not automatically loaded.

## Internal MCP clients

Use a client that supports **Streamable HTTP** and MCP image content. Configure the `/mcp` URL reachable from the client backend and the bearer token there. A container's `localhost` points to that container; use the service's network hostname when the client runs in another container, and add that hostname and port to `MCP_ALLOWED_HOSTS`.

One MCP process supervises one renderer subprocess. Rendering is serialized, while protocol requests remain responsive. A renderer deadline, crash, or memory failure affects that chart; the supervisor reaps the process and starts a replacement for subsequent charts. Waiting HTTP charts retain serialized request bodies and are bounded by both count and aggregate buffered bytes. The shared API key authenticates the client service; there are no per-user accounts or quotas. Run the sample client from the same network as your client backend to check connectivity, authentication, and PNG output before connecting your chat interface.

## Docker

The included [Compose configuration](compose.yaml) provides defaults to get you
up and running quickly. From the repository root, generate a local API key and
start the service:

```sh
export MCP_API_KEY="$(openssl rand -hex 32)"
docker compose up -d --build
```

The supplied defaults are:

| Setting | Compose default |
| --- | --- |
| Image | Built from the local repository (`build: .`) |
| Restart policy | `unless-stopped`: restart automatically unless explicitly stopped |
| Process management | `init: true` enables a small init process to forward signals and reap child processes |
| Host port | `127.0.0.1:8000:8000`, accessible only through the host's loopback interface |
| Server bind address | `MCP_HOST=0.0.0.0` and `MCP_PORT=8000` inside the container |
| API key | Required through `MCP_API_KEY`; no default secret is supplied |
| Allowed HTTP hosts | `localhost:8000,127.0.0.1:8000,matplotlib-mcp:8000` |
| Memory | 512 MB (512,000,000 bytes) for the server and renderer together, with no additional swap |
| Core dumps | Disabled (`ulimits.core: 0`) |
| Logs | Docker `json-file` driver, rotating at `10m` with up to 3 files |

These are starting defaults; adjust `compose.yaml` for your deployment. The
service uses one application process and the default chart and renderer limits
documented below. The endpoint is `http://localhost:8000/mcp`. Configure your
client with the generated key as its bearer token.

Compose also loads `MCP_API_KEY` from a local, Git-ignored `.env` file if you
prefer to retain it across shell sessions; keep it at least 16 characters long.
`MCP_ALLOWED_HOSTS` can be overridden through the environment or `.env`; it
defaults to `localhost:8000,127.0.0.1:8000,matplotlib-mcp:8000`. Clients on the
Compose network can use `http://matplotlib-mcp:8000/mcp`. For remote access, use
an HTTPS reverse proxy and add its public hostname to `MCP_ALLOWED_HOSTS`.
Use `docker compose logs -f` to view logs and `docker compose down` to stop and
remove the service.

Alternatively, run the image directly:

```sh
docker build -t matplotlib-mcp .
export MCP_API_KEY='replace-with-a-long-random-secret'
docker run --rm -p 127.0.0.1:8000:8000 -e MCP_API_KEY matplotlib-mcp
```

Connect to `http://localhost:8000/mcp` with the same bearer token. The image
runs as a non-root user and listens on `0.0.0.0:8000` inside the container.
Pass other settings with `-e`, including `MCP_ALLOWED_HOSTS` when accessing
the server through a different hostname or reverse proxy. Supply the API key
at runtime; do not include it in the image.

## Corporate themes and fonts

The server optionally loads `/etc/matplotlib-mcp/theme.yaml` once at startup.
Set `MCP_THEME_PATH` to select another YAML file. An absent default file keeps
the existing appearance; an explicitly selected missing file or an invalid
theme prevents startup. Theme and font changes require a container restart
(or a server restart when running locally). Keep mounted theme files and fonts
unchanged while the service runs: replacement renderer processes read them again.

The [example theme](examples/theme/theme.yaml) uses DejaVu Sans, which ships
with the installed Matplotlib package in its `mpl-data/fonts/ttf/` directory.
Those default fonts are already available locally and in the Docker image;
you do not need to copy them into this repository.

Custom corporate fonts are not included. Supply your own TTF/OTF files beside
the theme YAML, using this layout on the host (create `examples/theme/fonts/`
if needed):

```text
examples/theme/
├── theme.yaml
└── fonts/
    ├── CorporateSans-Regular.ttf
    └── CorporateSans-Bold.ttf
```

Mount that entire theme directory read-only:

```sh
docker run --rm -p 127.0.0.1:8000:8000 -e MCP_API_KEY \
  --mount type=bind,src="$(pwd)/examples/theme",dst=/etc/matplotlib-mcp,readonly \
  matplotlib-mcp
```

With this mount, `examples/theme/fonts/CorporateSans-Regular.ttf` on the host
is available at `/etc/matplotlib-mcp/fonts/CorporateSans-Regular.ttf` inside
the container. In `theme.yaml`, list paths relative to that YAML file and set
the font's internal family name (which can differ from its filename):

```yaml
colors:
  palette: ["#003B71", "#00A6A6", "#F4B400"]
  figure_background: "#FFFFFF"
  axes_background: "#FFFFFF"
  text: "#243746"
  axes: "#64748B"
  grid: "#E2E8F0"
font:
  family: "Corporate Sans"
  size: 11
  weight: normal
  style: normal
  files:
    - fonts/CorporateSans-Regular.ttf
    - fonts/CorporateSans-Bold.ttf
```

For a local server without Docker, the same layout works: set
`MCP_THEME_PATH=examples/theme/theme.yaml` before starting `matplotlib-mcp`.
Font paths still resolve relative to the YAML file's directory.

All settings are optional; omitted settings retain Matplotlib defaults.
`palette` must be a non-empty list of Matplotlib color strings; quote hex colors
in YAML. It supplies the default series color cycle, leaving scalar-data
colormaps unchanged. Surface colors style figure/axes backgrounds; `text` styles
text, axis labels, and ticks; `axes` styles borders; `grid` styles grid lines
when enabled by chart calls. Legends and colorbars inherit the corresponding defaults.

Font size must be finite and positive. Weight accepts standard Matplotlib names
(such as `normal`, `medium`, or `bold`) or a number from 0 to 1000; style accepts
`normal`, `italic`, or `oblique`. Font files resolve relative to the YAML file
and must be readable by container UID 10001. Supply separate files for desired
weights/styles. Without `files`, `family` must identify an installed font or a
Matplotlib generic family such as `sans-serif`. Unknown settings, invalid colors,
unreadable fonts, or an unavailable family prevent startup. Arbitrary rcParams
are not accepted.

Every chart uses the installed theme by default. Set `"theme": false` inside
`config` to use the standard appearance. Explicit options such as
`figure.facecolor`, `kwargs.color`, `kwargs.colors`, `kwargs.cmap`, or a label's
`fontdict` override their corresponding defaults. Use native options supported
by the particular method; for example:

```json
{"method": "errorbar", "args": [[1, 2], [3, 4]], "kwargs": {"color": "#003B71"}}
```

`list_chart_types` reports the active theme's colors/font settings, opt-out
instructions, and each method's color options. It does not expose theme or font
file paths. Chart requests cannot load font files or supply a theme file.

## Tools

`list_chart_types` returns every exposed Matplotlib plotting method, its expected input shape, accepted keyword options, an example, the shared configuration format, all setup methods, and effective resource limits. Pass `method="scatter"` to inspect one chart method.

`create_chart` accepts a `config` and one `data` object. Config supports optional `theme` (boolean, default `true`), `figure`, and required `axes`. Its configuration uses Matplotlib method names and their `args`/`kwargs`:

```json
{
  "config": {
    "figure": {"figsize": [8, 5], "dpi": 100, "suptitle": "Quarterly sales"},
    "axes": [{
      "subplot": [1, 1, 1],
      "calls": [
        {"method": "bar", "args": [{"$data": "quarter"}, {"$data": "sales"}], "kwargs": {"color": "steelblue", "label": "Sales"}},
        {"method": "set_title", "args": ["Sales by quarter"]},
        {"method": "set_ylabel", "args": ["Revenue"]},
        {"method": "legend"}
      ]
    }]
  },
  "data": {"format": "columns", "values": {"quarter": ["Q1", "Q2", "Q3"], "sales": [12, 18, 9]}}
}
```

The example creates a 20.32 × 12.7 cm figure. To specify a size in centimetres, divide each dimension by 2.54 for Matplotlib's `figsize` values.

Data may be named vectors/matrices (`{"format":"columns","values":...}`), JSON objects (`{"format":"rows","rows":[...]}`), or CSV text (`{"format":"csv","csv":"..."}`). Use an exact `{"$data":"column"}` object anywhere in call args or kwargs to refer to a named column. Matrices are useful for `imshow`, contour, and grid based methods. Every call supplies fresh data; the server keeps no chart or dataset state.

Named columns may have different lengths, for example histogram edges and values or independent grid coordinates. Plot arguments must still have compatible shapes for the selected Matplotlib method. The returned `rows` metadata is the largest first dimension among supplied columns. Setup methods accept required arguments by position or by their documented keyword, such as `{"method":"set_title","kwargs":{"label":"Sales"}}`.

For example, a stairs chart uses one more edge than value:

```json
{
  "config": {"axes": [{"calls": [{"method": "stairs", "args": [{"$data": "values"}, {"$data": "edges"}]}]}]},
  "data": {"format": "columns", "values": {"values": [3, 2], "edges": [0, 1, 2]}}
}
```

The renderer dispatches only registered 2D `Axes` methods and setup methods. It accepts ordinary JSON values only; it does not evaluate Python, import modules, resolve object attributes, or accept arbitrary Matplotlib method names. TeX and math text are disabled. Nested `usetex`, `parse_math`, `fontproperties`, and `fname` options are rejected; use `fontdict` with ordinary family, size, weight, and color settings instead. Callable options and artist objects cannot be supplied through JSON. PNG bytes are returned as MCP image content, with dimensions and request metadata in a companion text content block.

## Limits

Each limit is configured by the corresponding environment variable below. Positive values enforce the limit, `0` disables that one limit, and negative or non-integer values prevent startup. Structural validation and authentication always remain enabled.

| Environment variable | Default |
| --- | ---: |
| `MAX_REQUEST_BYTES` | 4194304 |
| `MAX_DATA_BYTES` | 1048576 |
| `MAX_ROWS` | 10000 |
| `MAX_COLUMNS` | 50 |
| `MAX_ARRAY_ELEMENTS` | 250000 |
| `MAX_LABEL_CHARS` | 500 |
| `MAX_IMAGE_WIDTH` / `MAX_IMAGE_HEIGHT` | 1600 each |
| `MAX_IMAGE_PIXELS` | 1000000 |
| `MAX_ARTISTS` | 10000 |
| `MAX_GEOMETRY` | 250000 |
| `MAX_WORK` | 5000000 |
| `MAX_AXES` | 16 |
| `MAX_CALLS` | 128 |
| `MAX_BINS` / `MAX_CONTOUR_LEVELS` | 200 each |

Figure defaults are 20.32 × 12.7 cm / 8 x 5 inch at 100 DPI. Dimensions must remain finite and positive, and configured pixel limits apply before rendering.

`MAX_AXES` also bounds the total subplot grid cells, including unused cells. `MAX_ARRAY_ELEMENTS` bounds data and configuration elements separately, streamplot density grid cells, and violin sampling points. `MAX_BINS` applies to histogram counts, explicit bin edges, each `hist2d` axis, and hexbin grid dimensions. With that limit enabled, histogram bins must be an explicit count or edge vector; automatic strategies such as `"auto"` are rejected. `MAX_CONTOUR_LEVELS` applies to counts and explicit level vectors. These checks also cover positional arguments and resolved `$data` references.

Charts are preflighted before creating figures or artists. Repeated references share one resolved array, but each call is charged against the chart-wide budgets. Estimates conservatively cover broadcasting, artist counts, geometry, contour levels, histogram grids, vector fields, and violin sample/evaluation combinations. These are admission estimates, not exact measurements of Matplotlib internals. A chart within the estimates may still fail its enforced renderer allocation limit or deadline.

Barb increments must be finite and positive; vector magnitudes and increments contribute to generated geometry. Pie `autopct` accepts one numeric percent-format conversion (`d`, `i`, `u`, `e`, `E`, `f`, `F`, `g`, or `G`) and escaped percent signs. Field width, precision, and estimated expanded length are checked before formatting. The formatting safety ceiling remains 500 characters when `MAX_LABEL_CHARS=0`.

The total pixel budget applies in addition to width and height. Default 800 × 500 charts fit; 1600 × 1600 charts are rejected. A 1600 × 625 chart fits the default pixel budget, although complex content may still exceed other limits.

### Renderer supervision

Production requires Linux with `RLIMIT_AS`. These settings must be **positive integers**; zero cannot disable supervision. Their effective values are included in `list_chart_types` under `rendering`.

| Environment variable | Default | Meaning |
| --- | ---: | --- |
| `RENDER_QUEUE_SIZE` | 50 | Maximum waiting chart/large-body requests (plus one active request) |
| `RENDER_QUEUE_BYTES` | 33554432 | Aggregate retained HTTP request-body bytes, including partial bodies and active requests |
| `RENDER_QUEUE_SECONDS` | 30 | Maximum wait before admission |
| `RENDER_BODY_SECONDS` | 10 | Total deadline to receive the HTTP body after authentication and reader admission; partial chunks do not reset it |
| `RENDER_TIMEOUT_SECONDS` | 10 | Deadline including renderer startup, validation, rendering, and IPC |
| `RENDER_MEMORY_BYTES` | 268435456 | Renderer virtual address-space cap, applied before importing plotting libraries |
| `RENDER_RECYCLE_CHARTS` | 50 | Completed requests before replacing the renderer |
| `RENDER_JSON_TOKENS` | 250000 | HTTP JSON structural-token budget, checked before decoding |
| `RENDER_JSON_DEPTH` | 64 | HTTP JSON nesting limit, checked before decoding |

OpenBLAS, OpenMP, MKL, and NumExpr thread counts are set to one in the renderer environment. Memory allocation failures, native process exits, and deadlines return safe MCP tool errors. Cancellation kills and reaps an active renderer; cancelled waiting requests release their queue capacity. Disconnect detection depends on transport delivery, so the deadline also bounds work after an undetected disconnect. Shutdown reaps the renderer. PNG responses have a separate 12 MiB encoded IPC ceiling.

Queue or body-buffer saturation returns HTTP **429**, and queue expiration returns HTTP **503**, each with `Retry-After: 1`. These failures occur before MCP tool dispatch. Clients must check HTTP errors as well as MCP `isError`, allow more than 40 seconds for the configured maximum queue-plus-render interval, and retry transient busy errors with backoff. Reducing chart complexity is appropriate for validation, memory, or render-deadline errors. Small ping and catalog calls bypass the chart queue. All bodies over 8 KiB share admission before audit or MCP JSON parsing, so large control requests cannot retain concurrent decoded trees. A linear scan bounds JSON containers, separators, string starts, and nesting before either decoder allocates objects; exceeding that structural budget returns HTTP 413. Quoted contents and escaped quotes are handled without constructing decoded strings. Total in-flight requests are bounded to `RENDER_QUEUE_SIZE + 8`, with at most `RENDER_QUEUE_SIZE + 4` simultaneous body readers.

Incomplete uploads return HTTP **408** when `RENDER_BODY_SECONDS` expires, releasing reader capacity and retained bytes before MCP dispatch. This total upload deadline also bounds clients that keep sending partial chunks. Budget client timeouts for upload time in addition to queue and rendering time. Configure equivalent request-body deadlines on a reverse proxy, if used.

HTTP request bodies must use valid UTF-8 JSON; a UTF-8 BOM is accepted. Invalid UTF-8, raw NUL bytes, and UTF-16/32 bodies (with or without a BOM) return HTTP **400** before structural scanning, audit parsing, or MCP decoding. Charset headers do not enable other encodings. Unicode strings and JSON-escaped NUL characters (`\u0000`) remain supported. Encoding validation follows the same admission rules as the structural scan, including queue admission for bodies over 8 KiB.

Use one application process with the 500 MB deployment profile. Increasing process count multiplies renderer memory and admission capacity. Changing memory, request-body, queue, or complexity settings requires repeating the container load test; `RLIMIT_AS` bounds the child, while the container limit includes the parent, queued bodies, shared libraries, and other charged memory. Keep the chart limits enabled.

## Development

```sh
pip install -e '.[dev]'
pytest
```

With the server running and `MCP_API_KEY` set to the same value in your client terminal, run the sample client:

```sh
python examples/test_client.py
```

Use `--url` to select another MCP endpoint or `--output` to choose where the client saves its PNG preview.

Use the repeatable load client against a disposable container with `MCP_API_KEY` set in your client environment:

```sh
python examples/load_test.py --url http://127.0.0.1:18973/mcp --sessions 50 --charts 10
python examples/load_test.py --url http://127.0.0.1:18973/mcp --mode rate --rate 1.74 --duration 120
python examples/load_test.py --url http://127.0.0.1:18973/mcp --width 1600 --height 625
python examples/load_test.py --url http://127.0.0.1:18973/mcp --mode probes
```

`--mode probes` submits all four historical failure payloads and checks for MCP rejection followed by working pings and charts. Other modes require every chart to succeed and verify PNG dimensions and repeatable hashes. Use `--output /tmp/load-results.json` to retain individual timings. The client does not measure container memory; inspect cgroup `memory.peak` and `memory.events` separately. See `LOADTEST.md` for the measured container results.
