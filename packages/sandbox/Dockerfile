# SPEDA Sandbox — the "capable computer" SPEDA runs commands in.
# Python + common CLI tooling, isolated from the API and its secrets.
FROM python:3.12-slim

# Common tools a capable assistant might reach for. Keep lean but useful.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl wget git jq unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# A few ubiquitous Python libs preinstalled so simple tasks need no install step.
RUN pip install --no-cache-dir requests pandas numpy

WORKDIR /workspace
ENV SANDBOX_WORKSPACE=/workspace SANDBOX_PORT=9000

COPY server.py /srv/server.py

EXPOSE 9000
CMD ["python", "/srv/server.py"]
