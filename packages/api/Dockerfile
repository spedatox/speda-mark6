# Build from the REPO ROOT context (uv workspace lives there):
#   docker build -f packages/api/Dockerfile .
# docker-compose sets context: . and dockerfile: packages/api/Dockerfile.
FROM node:20-slim AS node_bin

FROM python:3.11-slim

# openssh-client: the system_ops host bridge — the containerized backend SSHes
# to the real host (host.docker.internal) for Orion/Optimus server maintenance.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Node.js + npm/npx — every Tier-2 stdio MCP server (tavily, exa, notion,
# brave_search, fetch, github, cve_intelligence — see app/mcp/servers.py) is an
# npm package launched via `npx -y <pkg>`. Without this, every one of those
# fails silently at registration with "No such file or directory" REGARDLESS
# of whether its API key is configured — copied from the official node image
# rather than apt/NodeSource, so no extra GPG key management.
COPY --from=node_bin /usr/local/bin/node /usr/local/bin/node
COPY --from=node_bin /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

WORKDIR /app

# uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Workspace manifests + lock + the member's pyproject (for a frozen, reproducible
# install). Copy source too — uv installs the workspace member editable.
COPY pyproject.toml uv.lock ./
COPY packages/api/pyproject.toml ./packages/api/pyproject.toml
COPY packages/api/app ./packages/api/app
COPY packages/api/scripts ./packages/api/scripts

RUN uv sync --frozen --no-dev

RUN mkdir -p /tmp/speda_outputs

ENV PATH="/app/.venv/bin:$PATH"

# Run from the API package so `app.main:app` resolves.
WORKDIR /app/packages/api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
