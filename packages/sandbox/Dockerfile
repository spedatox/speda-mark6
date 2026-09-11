# SPEDA Sandbox — the isolated cyber-development lab SPEDA runs commands in.
#
# Built on a headless Kali base (the same kalilinux/kali-rolling the Forge
# security Cells bake into forge-cell-scourge). `kali-linux-headless` brings the
# complete headless Kali toolset — network recon (nmap, masscan, amass,
# theHarvester), web (nikto, sqlmap, nuclei, ffuf, gobuster, feroxbuster, wpscan),
# exploitation (metasploit, searchsploit), password (hydra, john, hashcat),
# forensics/reversing — plus a real dev toolchain (build-essential, python3/pip,
# Go, pipx, git) so it is a lab you develop in, not just a box you scan from.
#
# Isolation contract is unchanged from before: no host mounts (only its own
# workspace + home volumes), no secrets, resource-limited, reachable only on the
# internal Docker network. The stdlib exec server (server.py) is untouched.
ARG KALI_IMAGE=kalilinux/kali-rolling
FROM ${KALI_IMAGE}

# Full headless toolset + dev toolchain + everyday CLI in one cached layer.
# DEBIAN_FRONTEND=noninteractive so debconf (wireshark's capture prompt,
# metasploit's postgres, tzdata) never blocks the build.
#
# NOTE: this is a large bake (~10 GB, several hundred packages) — the first build
# on the server is slow. Docker caches the layer, so later deploys are quick.
# Pin KALI_IMAGE (e.g. kalilinux/kali-rolling:2025.4) for a reproducible build.
RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update \
    && apt-get install -y \
        # The lab itself — everything Kali ships for headless use.
        kali-linux-headless \
        # Dev toolchain + everyday CLI the exec server and agents rely on.
        ca-certificates curl wget git jq unzip build-essential \
        python3 python3-pip python3-venv python-is-python3 pipx golang-go \
        vim tmux \
        # Python data/document libs (kept from the pre-Kali image so document
        # generation still needs no install). Installed via apt, not pip, so we
        # never fight Debian's PEP-668 externally-managed marker.
        python3-requests python3-pandas python3-numpy python3-matplotlib \
        python3-openpyxl python3-reportlab python3-docx python3-pptx \
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
