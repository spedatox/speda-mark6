# SPEDA Sandbox — the "capable computer" SPEDA runs commands in.
# Python + common CLI tooling, isolated from the API and its secrets.
FROM python:3.12-slim

# Common tools a capable assistant might reach for. Keep lean but useful.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl wget git jq unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Preinstalled libs so common tasks (incl. document generation) need no install:
# data (requests/pandas/numpy/openpyxl), documents (reportlab/docx/pptx), plots.
RUN pip install --no-cache-dir \
    requests pandas numpy openpyxl \
    reportlab python-docx python-pptx \
    matplotlib

WORKDIR /workspace
ENV SANDBOX_WORKSPACE=/workspace SANDBOX_PORT=9000

COPY server.py /srv/server.py

EXPOSE 9000
CMD ["python", "/srv/server.py"]
