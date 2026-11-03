# These digests were pulled and verified on the deployment host, 2026-11-03.
FROM node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS node
FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

FROM node AS solus
WORKDIR /build/solus
COPY packages/solus/package.json packages/solus/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY packages/solus/ ./
RUN npm run build -- --outDir /build/static/solus

FROM python:3.11.17-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    SPEDA_DATA_DIR=/data \
    DATABASE_URL=sqlite+aiosqlite:////data/speda.db

# PDF rendering needs native Pango/Harfbuzz; git and node support native tools
# and operator-pinned MCP packages. Browser engines stay in isolated sidecars.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git openssh-client \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY pyproject.toml uv.lock LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app/ app/
COPY forge/ forge/
COPY scripts/ scripts/
COPY --from=solus /build/static/solus/ app/static/solus/
RUN uv sync --frozen --no-dev --no-editable \
    && mkdir -p /tmp/speda_outputs /data
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('HTTP_PORT','8000')+'/health/ready',timeout=3)"
CMD ["python", "-m", "app.supervisor"]

# The verification image uses the exact runtime libraries and application.
FROM runtime AS test
RUN uv sync --frozen --extra dev --no-editable
COPY tests/ tests/
COPY docs/ docs/
HEALTHCHECK NONE
CMD ["python", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "--basetemp", "/tmp/speda-tests"]

FROM runtime AS production
