# Build from the REPO ROOT context (uv workspace lives there):
#   docker build -f packages/api/Dockerfile .
# docker-compose sets context: . and dockerfile: packages/api/Dockerfile.
FROM python:3.11-slim

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
