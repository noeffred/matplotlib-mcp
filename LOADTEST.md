# Container load and failure test

> Historical baseline: see [Post-fix verification](#post-fix-verification) below for results after the fixes.

Tested on 2026-10-16, approximately 10:50–11:05 Europe/Vienna.

## Assessment

**The expected daily volume looks manageable for ordinary charts in 500 MB, but the current defaults are not robust enough for production.** Normal traffic passed 1,879 chart requests without errors. However, ten concurrent sessions requesting permitted maximum-resolution charts repeatedly killed the container. Three small, accepted inputs also caused out-of-memory kills, and another accepted input monopolized rendering after its client timed out.

No application code, tests, dependencies, or deployment files were changed. This report is the only repository file added by the load test. All requests went to a disposable local container, using a disposable API key and synthetic data. Test containers were removed afterward; the test image remains available.

## Workload and environment

The supplied usage estimate means:

| Assumption | Low estimate | High estimate |
| --- | ---: | ---: |
| Users per day | 500 | 500 |
| Sessions per user per day | 5 | 10 |
| Charts per session | 10 | 10 |
| Charts per day | 25,000 | 50,000 |
| Average charts/second over 24 hours | 0.29 | 0.58 |
| Average charts/second over 8 hours | 0.87 | 1.74 |

The high end of each average was tested, along with simultaneous sessions and accelerated repeated use. This was a short load/failure test, **not a full-day soak or a complete 50,000-chart simulation**.

- Built the repository's existing Dockerfile, including its current theme support. No theme file or custom fonts were mounted.
- Docker Engine 28.4.0, Linux cgroup v2.
- Container memory: **500,000,000 bytes**, approximately 476.8 MiB. The effective page-rounded cgroup limit was 499,998,720 bytes.
- `--memory-swap=500000000`: additional swap disabled; verified `memory.swap.max=0` and no swap usage.
- One application process; no CPU quota. Host: Intel Core i5-8365U, 4 physical cores / 8 logical CPUs, approximately 7.42 GB RAM reported to Docker.
- Container: Python 3.12.15, Matplotlib 3.11.2, NumPy 2.5.4, MCP SDK 1.30.0, Uvicorn 0.54.0.
- Default application resource limits. Explicit allowed host for the mapped local port. Restart policy `no`, matching Docker's default, so crashes remained visible.
- Local HTTP load generator outside the container's memory budget; no reverse proxy, TLS, or external network latency.

Normal charts used a repeatable ten-chart mix: three line charts, two bar charts, and one each of scatter, histogram, image, filled contour, and pie. Lines/scatter/histograms used 500 values, bars 30 values, image/contour grids 40 × 40, and pies five slices. All charts included a title. Default output was 800 × 500 pixels. High-resolution tests used the same mix at 1600 × 1600 pixels.

Rate tests scheduled chart arrivals independently of completion. Session tests initialized each logical session, then requested charts sequentially with no think time; different sessions ran concurrently. This deliberately makes session bursts more demanding than users pausing between charts. There were no automatic retries. Ordinary chart requests had 60-second client timeouts.

Each successful chart response was checked for MCP success, valid base64, PNG signature, and agreement between PNG dimensions and reported metadata. Output hashes were also compared across repeated normal requests. Memory and CPU counters were read from the container's host cgroup approximately every 0.5 seconds; Docker state confirmed OOM kills. Memory figures include cgroup-charged memory, not just Python RSS.

## Ordinary traffic results

These phases reused the same container, preserving accumulated process state. The first two phases used approximately 120-second arrival windows. The remaining phases ran as quickly as requests could complete.

| Scenario | Charts succeeded / attempted | Elapsed | Charts/sec completed | Median latency | p95 latency | Max latency | Highest sampled memory |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| High 24-hour average: 0.58 arrivals/sec | 70 / 70 | 119.3 s | 0.59 | 174 ms | 285 ms | 319 ms | 118.4 MB |
| High 8-hour average: 1.74 arrivals/sec | 209 / 209 | 119.6 s | 1.75 | 163 ms | 221 ms | 359 ms | 119.3 MB |
| 10 concurrent sessions, 10 charts each | 100 / 100 | 12.1 s | 8.24 | 1.22 s | 1.48 s | 1.55 s | 171.5 MB |
| 50 concurrent sessions, 10 charts each | 500 / 500 | 66.7 s | 7.50 | 6.22 s | 8.48 s | 9.12 s | 209.0 MB |
| Repeated use: 10 concurrent sessions, 100 charts each | 1,000 / 1,000 | 136.5 s | 7.33 | 1.38 s | 1.69 s | 2.11 s | 209.3 MB |

The kernel-recorded memory high-water mark across these normal phases was **212.5 MB**. After the repeated-use phase, current memory was 195.1 MB. There was no continuing memory growth evident in that phase, although this short run cannot rule out a long-term leak.

The busy eight-hour-rate phase averaged about **0.28 CPU cores** of container CPU time on this host. Saturated session phases used approximately one CPU core and completed roughly 7–8 charts/second for this particular mix. These results support the expected average volume, but they are not a capacity guarantee for more complicated charts or a different production CPU.

No cross-request output contamination was observed: each of the seven chart types retained its expected deterministic image hash across the normal phases. This does not prove the absence of every race condition.

## Confirmed production issues

### 1. High: permitted high-resolution charts crash under modest concurrency

The same ordinary chart mix was requested at **1600 × 1600**, within both default pixel limits. Ten concurrent sessions each requested ten charts.

| Run | Succeeded / attempted | Failed | Result |
| --- | ---: | ---: | --- |
| After the ordinary traffic phases | 63 / 100 | 37 | OOM kill; exit 137, `OOMKilled=true` |
| Repeat on a fresh container | 69 / 100 | 31 | OOM kill; exit 137, `OOMKilled=true` |
| Serial control on another fresh container | 100 / 100 | 0 | Passed; kernel peak 349.6 MB |

The concurrent runs reached a recorded memory high-water mark of approximately 500 MB before dying, after about 15.5 and 18.1 seconds respectively. Clients saw disconnected responses, read errors, or connection failures. The serial control completed in 24.9 seconds, with p95 latency 433 ms.

**Impact:** ten users requesting supported chart sizes can kill the whole service without malformed data or extreme plot options. The fresh-container repeat rules out dependence on the preceding ordinary workload. Serialization of Matplotlib calls alone does not keep process memory within the container budget under concurrent requests.

Relevant code: `src/matplotlib_mcp/server.py`, `_validate_config`, `_render`, `_render_figure`, `create_chart`, and `_create_chart_tool`. Requests use the default executor's multiple worker threads and then serialize on the render lock. The test establishes the concurrency-related memory failure; it does not isolate the precise contribution of allocator arenas, figure lifetimes, or native caches.

**Priority:** establish a bounded rendering worker/queue and verify its memory behavior at the supported maximum dimensions. Reconsider the default image limits for a 500 MB deployment. Merely increasing the memory limit would not address the other unbounded allocation paths below.

### 2. High: a single wind vector can OOM-kill the container

This accepted tool-argument payload is just **151 bytes** in compact JSON:

```json
{"config":{"axes":[{"calls":[{"method":"barbs","args":[[0],[0],[50000000000],[0]],"kwargs":{}}]}]},"data":{"format":"columns","values":{"unused":[1]}}}
```

With default barb increments, the vector requests one billion flags. The application does not bound that generated geometry.

**Observed:** the attack request disconnected after 2.68 seconds. Docker reported exit 137 and `OOMKilled=true`. An unrelated ordinary chart also lost its connection. A ping shortly before the kill still succeeded.

Relevant code: `registry.py` permits `barbs` and custom `barb_increments`; `_validate_call_resources` has no barb geometry budget.

**Priority:** bound generated flags/vertices, accounting for both vector magnitudes and configured increments. Row counts and finite-number validation do not constrain this expansion.

### 3. High: pie label formatting bypasses the string-length limit and kills the process

This accepted tool-argument payload is **153 bytes**:

```json
{"config":{"axes":[{"calls":[{"method":"pie","args":[[1]],"kwargs":{"autopct":"%1000000000.1f"}}]}]},"data":{"format":"columns","values":{"unused":[1]}}}
```

The format string is short, but its field width requests a billion-character percentage label. The existing 500-character limit checks the input string, not its expansion.

**Observed:** the request disconnected after approximately **204 ms**. Docker reported exit 137 and `OOMKilled=true`; a subsequent ping and ordinary chart could not connect. This kill was faster than the memory sampling interval, so the last sampled memory value is not a measurement of peak memory at failure.

Relevant code: `registry.py` permits pie `autopct`; `server.py` `_resolve` limits the supplied string's length but does not inspect formatting width or precision.

**Priority:** restrict formatting width/precision and bound generated label size before allocation.

### 4. High: data references let a small request generate far more work than the data limits suggest

A **77,422-byte** tool-argument payload supplied two 10,000-value columns and reused them in 128 `bar` calls. Every individual limit was respected. The combined request asks Matplotlib to construct **1.28 million bars**.

Reproduction recipe:

```python
data = {"format": "columns", "values": {"x": list(range(10000)), "y": [1] * 10000}}
config = {"axes": [{"calls": [
    {"method": "bar", "args": [{"$data": "x"}, {"$data": "y"}], "kwargs": {}}
    for _ in range(128)
]}]}
```

**Observed:** the attack request disconnected after 26.92 seconds, with Docker reporting an OOM kill and exit 137. Memory reached approximately 500 MB. An unrelated chart timed out after five seconds. Even a protocol ping took 4.86 seconds during this workload.

Relevant code: `_prepare_data` bounds supplied data and `_validate_config` bounds call count, but their product and the number of generated artists are not budgeted. `$data` references are resolved separately for each call.

**Priority:** budget total chart complexity, including repeated references, artist counts, and method-specific expansion. The current independent limits do not provide an effective per-chart memory/work bound.

### 5. High: an accepted computation blocks rendering after the client has gone away

A 69,042-byte payload used `violinplot`, 10,000 sample values, and `points=250000`. The sampling-points value is permitted by the default array-element limit. Density estimation work depends on both the number of samples and evaluation points—approximately 2.5 billion sample-point combinations here.

Reproduction recipe:

```python
data = {"format": "columns", "values": {"y": [i / 10000 for i in range(10000)]}}
config = {"axes": [{"calls": [{
    "method": "violinplot",
    "args": [{"$data": "y"}],
    "kwargs": {"points": 250000}
}]}]}
```

**Observed:**

- The expensive request exceeded its 15-second client timeout.
- An unrelated ordinary chart exceeded its five-second timeout.
- A later ordinary chart still exceeded a three-second timeout after the expensive client had already disconnected.
- MCP pings continued to succeed: about 13 ms during the original request and 63 ms during the follow-up probe.
- Across approximately 43.9 seconds of observation, the container consumed **319.3 CPU-seconds**, averaging about 7.3 logical CPU cores on this eight-CPU host. In the 28.3 seconds after the original client timeout was recorded, it consumed another 205.2 CPU-seconds.
- Memory stayed around 133 MB; this was a CPU/availability failure, not an OOM. The container was forcibly removed to terminate the remaining work. Completion time was not measured.

Relevant code: `_validate_call_resources` caps violin evaluation points independently of dataset size; `_create_chart_tool` uses `asyncio.to_thread`; `create_chart` holds the global render lock. Cancelling an async wait does not terminate an executing Matplotlib/NumPy call in its worker thread.

**Impact:** one request can deny chart service to other users while protocol-level health checks continue to report success. Client/proxy timeouts alone do not recover rendering capacity. There is also no explicit admission limit on queued requests; the finite executor thread count is not a bound on its pending queue.

**Priority:** bound computational work and admission, and provide a render deadline that can actually terminate the computation, such as a supervised renderer process. Test recovery of real chart rendering, not only MCP ping responses.

## Queue latency and checks that passed

The ordinary 50-session burst produced no errors, but p95 latency increased to 8.48 seconds and the maximum was 9.12 seconds. That is a relevant client timeout consideration. Increasing concurrent request count did not increase rendering throughput; it primarily increased waiting time. No product latency target was supplied, so this is a measured capacity observation rather than an independent correctness failure.

While ordinary load was running:

- Invalid bearer token returned HTTP 401.
- A body one byte over 4 MiB returned HTTP 413.
- An unregistered plotting method returned an MCP tool error.
- An excessive histogram bin count returned an MCP tool error.
- A valid chart immediately afterward succeeded.

Rejected tool inputs correctly used MCP error results even when HTTP status was 200; the harness checked both layers. The confirmed crash inputs above were accepted past those existing safeguards.

All observed OOM kills stopped the application. Automatic recovery was deliberately disabled, as with Docker's default restart policy. A production restart policy could restore the process, but would not preserve in-flight work or prevent repeated triggering of these inputs.

## Reproduction and evidence

The image was built and run from the existing repository using the equivalent of:

```sh
docker build -t visu-loadtest:20261016 .
docker run -d --name visu-loadtest-20261016 \
  --memory=500000000 --memory-swap=500000000 --restart=no \
  -p 127.0.0.1:18973:8000 \
  -e MCP_API_KEY \
  -e MCP_ALLOWED_HOSTS=127.0.0.1:18973 \
  visu-loadtest:20261016
```

Set a disposable `MCP_API_KEY` before reproducing. Chart payloads above are the `arguments` of a JSON-RPC `tools/call` request with `name="create_chart"`, sent to `/mcp` with bearer authentication, `Accept: application/json, text/event-stream`, and `MCP-Protocol-Version: 2025-03-26`. The harness also performed MCP initialization. These failure probes intentionally crash or occupy the service; use a disposable container.

Test image ID:

```text
sha256:88117ae9a864872460d1622b33b9720953e342b27d8ea94eb4894a9baf5f90aa
```

Source hashes were unchanged between the start and end of this test:

| File | SHA-256 |
| --- | --- |
| `Dockerfile` | `b44878e83d5211d97a25eff6b4915e23ba22c54cd09aba5ba7484394ddfa1a78` |
| `pyproject.toml` | `99e0587e81549db50d1d38b86548626ce3cb033689b52237a02db19aee09de1d` |
| `requirements.lock` | `b1f1ac297caf79fa8a6856c830d6ee658f637188df2c5a6dc3c9404d72a3b26c` |
| `src/matplotlib_mcp/server.py` | `c1cd11ed131f0397d36b685dfb121b9dd04dcb62bded4b17661986873119d5a1` |
| `src/matplotlib_mcp/registry.py` | `d773436b945c085edef93d46613753a5152ec2b2ef799c03d3a25dd3d8b9f0c7` |
| `src/matplotlib_mcp/theme.py` | `26a9e8aa5fee4c0fbd714c5ff0eaa30ba9cdae3989c1e63680daebaacd67bb8c` |

Temporary harnesses, request payloads, per-request timings, PNG hashes, cgroup samples, and container logs are retained locally in `/tmp/visu-loadtest-20261016/`. They are not additional repository changes and may disappear when temporary files are cleaned. The tables and failure details above preserve the findings independently of those temporary files.

The report establishes the failures observed under this memory limit and workload. It does not claim that all methods, all chart shapes, external client behavior, or a full production day have been exhaustively tested. No fixes were implemented.


## Post-fix verification

Verified on 2026-10-16 12:15 CEST. This section describes the implemented fixes; the preceding report is the unchanged historical baseline.

**Acceptance passed: the final implementation completed all 1,879 ordinary workload charts and all 100 charts at the new maximum pixel budget. Neither container was OOM-killed or restarted. The highest kernel-recorded per-container memory peak across workload, failure, and buffering probes was 278.6 MB, below the 450 MB acceptance target.** Unsafe charts returned errors, and queued charts survived forced renderer failures.

### Implementation under test

- One persistent renderer subprocess, with a 256 MiB `RLIMIT_AS` cap applied before imports, one numerical-library thread, a 10-second deadline, and recycling every 50 completed requests. Figure/canvas cycles are collected between requests.
- Up to 50 queued chart/large-body requests, a 30-second queue deadline, and 32 MiB aggregate buffered request bodies. Inputs remain serialized while waiting. Bodies over 8 KiB queue before audit or MCP JSON parsing; total in-flight requests and partial-body readers are also bounded. Small ping/catalog requests bypass the rendering queue.
- Whole-chart preflight limits: 10,000 estimated artists, 250,000 geometry elements, 5 million work units, and 1 million output pixels. Repeated references share arrays but are charged on each use. Barb expansion, pie formatting width/precision, violin density work, grids, groups, and broadcasting receive method-specific checks.
- A mandatory pre-decode scan limits HTTP JSON structure to 250,000 units and 64 nesting levels, preventing dense-object expansion from exhausting the MCP parent. Oversized structure returns HTTP 413.
- Overflow and expired queues return HTTP 429/503 with `Retry-After`. Renderer failures return safe MCP tool errors. Authentication, allowlists, theme support, PNG metadata, and in-memory rendering remain enabled.

The final image was `sha256:5646e0f41879ef51ebc6e0cd601ea19a63fbd46c760d9987d8e5795c214f6d26` (`visu-loadtest:fixes`). No dependency versions or container memory allowance were increased.

Two fresh disposable containers ran the final image, each with **500,000,000 bytes, no swap, no automatic restart, one MCP process, and one supervised renderer**. Rate phases reused one container; burst, repeated-use, maximum-pixel, and historical-failure phases reused the other. The groups ran concurrently on the same host, so these timings are availability measurements rather than a controlled performance comparison. Buffering and forced-worker-failure probes ran in the rate container before its rate phases. Both cgroups reported `memory.max=499998720`, `memory.swap.max=0`, `oom=0`, and `oom_kill=0`; Docker reported `RestartCount=0`.

### Main workloads

| Scenario | Charts succeeded / attempted | Elapsed | Charts/sec completed | Median latency | p95 latency | Maximum latency | Highest sampled memory |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| High 24-hour average, 0.58 arrivals/sec | 70 / 70 | 119.1 s | 0.59 | 191 ms | 251 ms | 2156 ms | 241.8 MB |
| High 8-hour average, 1.74 arrivals/sec | 209 / 209 | 119.7 s | 1.75 | 195 ms | 1504 ms | 2399 ms | 236.3 MB |
| 10 sessions × 10 charts | 100 / 100 | 21.8 s | 4.59 | 1908 ms | 3783 ms | 3820 ms | 203.6 MB |
| 50 sessions × 10 charts | 500 / 500 | 109.4 s | 4.57 | 10880 ms | 12123 ms | 12222 ms | 210.6 MB |
| 10 sessions × 100 charts | 1000 / 1000 | 220.3 s | 4.54 | 1909 ms | 3914 ms | 4111 ms | 212.1 MB |
| 10 sessions × 10 charts, 1600 × 625 | 100 / 100 | 25.2 s | 3.96 | 2213 ms | 4172 ms | 4311 ms | 212.5 MB |

Every successful response was checked for MCP success, valid base64, a PNG signature, and dimensions matching its metadata. Saturated throughput is lower than the baseline (approximately 4–5 charts/sec for this mix), with additional IPC, collection, and periodic cold-start overhead; the concurrent test groups also limit direct performance comparisons. The expected average arrival rates still passed. Configure clients to allow the queue-plus-render interval; a 50-session burst can wait more than ten seconds.

The previous 1600 × 1600 workload was also repeated: **100 / 100 requests were rejected as MCP tool errors** by `MAX_IMAGE_PIXELS`, with no disconnected responses or container failures. The supported 1600 × 625 workload above uses exactly 1 million pixels.

### Historical failure payloads

| Payload | Result | Rejection latency | Following ordinary chart |
| --- | --- | ---: | --- |
| barbs | MCP tool error | 1842 ms | Passed |
| pie | MCP tool error | 49 ms | Passed |
| repeated-bars | MCP tool error | 81 ms | Passed |
| violin | MCP tool error | 80 ms | Passed |

Protocol pings also succeeded after every payload. The reusable repository client's `--mode probes` independently verified the four rejections, four recovery pings, and four recovery PNGs with consistent hashes.

### Recovery and buffering

The rate container's renderer was deliberately frozen with `SIGSTOP`. For the deadline case it remained frozen until the supervisor terminated it. For the exit case it was killed during the active request. For disconnect, the client timed out while its active renderer was frozen. In each case nine additional chart requests were submitted, and the replacement worker completed all nine. The renderer PID changed and exactly one renderer remained; the MCP parent stayed alive.

| Injected failure | Affected request duration | Queued charts succeeded | Ping latency while affected |
| --- | ---: | ---: | ---: |
| deadline | 10.02 s | 9 / 9 | 22 ms |
| exit | 0.43 s | 9 / 9 | 33 ms |
| disconnect | 0.05 s | 9 / 9 | 24 ms |

A separate buffering probe submitted **50 concurrent control requests of 4,194,292 bytes each**, containing hundreds of thousands of small JSON objects per body. This exercised the pre-decode guard as well as raw-body buffering. Results: `{"413": 9, "429": 41}`. The subsequent ping returned HTTP 200; no container OOM or restart occurred. These rejected requests are intentional admission failures, separate from the successful ordinary workload counts. A companion probe sent 50 bodies of 349,360 bytes just below the structural budget: all 50 returned HTTP 200. Another sent 50 near-4-MiB string-heavy bodies within the structural budget: results were `{"200": 9, "429": 41}`. Both were followed by successful recovery pings.

### Automated checks and reproduction

`.venv/bin/pytest -q`: **172 passed**. Coverage includes every registered plotting method, themes, all reported payloads, positional/resource options, aggregate budgets across axes, reference caching, enforced process limits, real subprocess allocation failure, exit/deadline/cancellation, queued-request survival, process recycling and shutdown, byte/count admission, partial bodies, expiration, authentication, and privacy-preserving logs.

Build and run a disposable container with the same memory settings as the original reproduction, substituting image `visu-loadtest:fixes`. Set `MCP_API_KEY` in the client environment and use the repository harness:

```sh
python examples/load_test.py --url http://127.0.0.1:18973/mcp --mode rate --rate 0.58 --duration 120
python examples/load_test.py --url http://127.0.0.1:18973/mcp --mode rate --rate 1.74 --duration 120
python examples/load_test.py --url http://127.0.0.1:18973/mcp --sessions 50 --charts 10
python examples/load_test.py --url http://127.0.0.1:18973/mcp --sessions 10 --charts 100
python examples/load_test.py --url http://127.0.0.1:18973/mcp --width 1600 --height 625
python examples/load_test.py --url http://127.0.0.1:18973/mcp --mode probes
```

Use `--output /tmp/load-results.json` to save individual timings. Inspect cgroup `memory.peak` and `memory.events` independently; the HTTP client does not measure container memory. The exact phase outputs, cgroup samples, buffering probe, and renderer fault-injection script for this run remain under `/tmp/visu-loadtest-20261016/` with `final-` prefixes. Temporary files may be cleaned; the measurements above preserve the findings.

These tests establish recovery for the measured inputs and limits, not an exhaustive proof against every possible Matplotlib workload or a full-day soak. Keep the allocation cap, deadlines, and admission limits enabled and retest after changing the deployment profile.

After collecting the measurements, both disposable containers shut down normally with exit code 0, `OOMKilled=false`, and `RestartCount=0`, and were removed. The final test image remains available.
