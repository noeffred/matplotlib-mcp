# Repository Guidelines

## Project Structure & Module Organization

This Python 3.11+ project exposes a stateless Matplotlib service through FastMCP.
- `src/matplotlib_mcp/server.py`: validation, authentication, resource limits, rendering, logging, and the CLI entry point.
- `src/matplotlib_mcp/registry.py`: allowed plotting methods, options, and catalog examples.
- `tests/test_server.py`: rendering, validation, middleware, logging, and concurrency tests.
- `examples/test_client.py`: live MCP client that saves a PNG preview.
- `pyproject.toml`: dependencies, Hatchling packaging, CLI registration, and pytest settings. `requirements.lock` constrains Docker dependencies.
- `Dockerfile`: non-root container deployment. Charts are generated in memory; no static asset directory is required.

## Build, Test, and Development Commands

Run from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'          # Editable install and test dependencies
pytest                         # Full suite; quiet output is configured
pytest tests/test_server.py -k middleware  # Focused tests
export MCP_API_KEY='replace-with-a-long-random-secret'
matplotlib-mcp                 # Serve http://127.0.0.1:8000/mcp
python examples/test_client.py # Smoke test against the running server
docker build -t matplotlib-mcp . # Build deployment image
```

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_CASE` for constants. Follow existing type annotations, dataclasses, and short module docstrings. Prefix internal helpers with `_`. Keep dispatch allowlists in `registry.py`; update validation and catalog examples alongside new options. No formatter or linter is configured.
Prefer local readability over structural elegance. Code should be understandable primarily by reading it sequentially, without navigating through unnecessary helper functions or layers of abstraction. Do not optimize for short functions, low line counts, or adherence to design patterns. Extract logic only when doing so provides a clear, tangible benefit to understanding, reuse, testing, or maintenance.

## Testing Guidelines

Use pytest with `test_*.py` files and descriptive `test_*` functions. Parameterize input formats and chart methods. Cover PNG signatures and dimensions, invalid inputs, authentication, limits, privacy-preserving logs, and request isolation when affected. Extend registered-method rendering cases for new chart types. No numeric coverage threshold is configured; run the full suite before submitting changes.

## Commit & Pull Request Guidelines

The repository has no commits yet. Use concise, imperative subjects such as `Add scatter validation tests`. Keep changes focused. PRs should explain behavior changes, link relevant issues, and record test commands and results. Include sample configurations or preview images for rendering changes. Update `README.md` for public API or configuration changes.

## Security & Configuration

Supply API keys through the environment; never commit credentials. Require keys of at least 16 characters. Preserve bearer authentication, method allowlists, JSON-only inputs, and in-memory rendering. Keep chart data and secrets out of logs. Document new environment settings and limits; use HTTPS for remote deployments.
