FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

# Cache dependency installation separately from application changes. The lockfile
# constrains runtime dependencies without installing its development-only tools.
COPY pyproject.toml requirements.lock ./
RUN python -c 'import subprocess, tomllib; dependencies = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call(["pip", "install", "--constraint", "requirements.lock", *dependencies])'

COPY src/ ./src/
RUN pip install --no-deps . \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /etc/matplotlib-mcp

USER app

EXPOSE 8000

CMD ["matplotlib-mcp"]
