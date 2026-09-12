# SPEDA Sandbox — the isolated Kali development lab SPEDA runs commands in.
#
# Keep the Kali base but deliberately avoid kali-linux-headless: the sandbox is
# for everyday coding, data, and document work, while Forge's dedicated security
# Cell owns the large security toolchain. A job can install a specialist package
# inside this isolated container when it actually needs one.
#
# Isolation contract is unchanged from before: no host mounts (only its own
# workspace + home volumes), no secrets, resource-limited, reachable only on the
# internal Docker network. The stdlib exec server (server.py) is untouched.
ARG KALI_IMAGE=kalilinux/kali-rolling
FROM ${KALI_IMAGE}

# Compact development baseline. It intentionally excludes desktop/X11 software,
# scanners, password tools, Metasploit, and language runtimes agents can add only
# when needed. Python data/document libraries remain preinstalled so established
# report and file-generation work keeps working without a network install.
#
# Pin KALI_IMAGE (e.g. kalilinux/kali-rolling:2025.4) for a reproducible build.
RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        # Everyday shell and source-control tools.
        ca-certificates curl wget git openssh-client jq ripgrep fd-find \
        file less nano vim-tiny unzip zip \
        # Build, Python, JavaScript, Go, and local-data essentials.
        build-essential pkg-config make cmake \
        python3 python3-pip python3-venv python-is-python3 pipx golang-go \
        nodejs npm sqlite3 \
        # Python data/document libs (kept from the pre-Kali image so document
        # generation still needs no install). Installed via apt, not pip, so we
        # never fight Debian's PEP-668 externally-managed marker.
        python3-requests python3-pandas python3-numpy python3-matplotlib \
        python3-openpyxl python3-reportlab python3-docx python3-pptx \
    && ln -sf "$(command -v fdfind)" /usr/local/bin/fd \
    && rm -rf /var/lib/apt/lists/*

# pip stays usable at runtime: this is an isolated throwaway container, so
# allowing global `pip install` (which PEP-668 would otherwise refuse) is exactly
# the "install whatever you need" behavior a lab wants.
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace
ENV SANDBOX_WORKSPACE=/workspace SANDBOX_PORT=9000

COPY server.py /srv/server.py

EXPOSE 9000
CMD ["python3", "/srv/server.py"]
