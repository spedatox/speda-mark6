# Scourge's Cell image — a HEADLESS Kali toolbox (no GUI, no X11) baked at BUILD
# time so a live Cell pays nothing at job time for the routine toolchain. Cells are
# throwaway per job; Scourge runs as root WITH network in-cell, so anything not
# baked here can be installed via `apt-get`, `pipx`, or `go install` during the job.
#
# Crucially, this image is equipped with strict OPSEC & Anonymity controls (Tor,
# Proxychains with DNS leak prevention, OpenVPN, WireGuard, and privacy DNS)
# so the host server's real IP and DNS identity are never exposed to target systems.
#
# forge/agents/scourge/profile.toml [cell].image points at the tag this builds.
# The per-agent image wins over the global FORGE_CELL_IMAGE. Under the subprocess
# backend (Docker-less host) the image is ignored entirely.
#
# Build (on the host, context is deploy/ to keep the .venv out of the daemon):
#   docker build -f packages/forge/deploy/cell-scourge.Dockerfile \
#     -t forge-cell-scourge:latest packages/forge/deploy/
FROM kalilinux/kali-rolling

LABEL org.opencontainers.image.title="forge-cell-scourge" \
      org.opencontainers.image.description="Headless Kali toolchain with OPSEC, Tor, Proxychains, VPN and extensibility baked for Forge's Scourge agent" \
      maintainer="spedatox"

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    GOPATH=/root/go \
    PATH="/root/go/bin:/root/.local/bin:${PATH}" \
    PIP_NO_CACHE_DIR=1

# 1) Pin apt to official Kali mirrors only.
#    - Overwrite sources.list with the two official mirrors so Docker build never
#      picks a broken third-party mirror (e.g. kalimirror.velden.media with bad SSL).
#    - Delete /etc/apt/sources.list.d/kali.sources which the base image ships: it
#      defines the same kali-rolling repo and causes "configured multiple times"
#      warnings that make apt exit non-zero in strict mode.
RUN rm -f /etc/apt/sources.list.d/kali.sources \
 && printf 'deb http://http.kali.org/kali kali-rolling main contrib non-free non-free-firmware\ndeb http://kali.download/kali kali-rolling main contrib non-free non-free-firmware\n' \
        > /etc/apt/sources.list

# 2) Essential security toolset and networking utilities.
#    Ultra-lean: uses precompiled binaries exclusively. Excludes heavy C/C++
#    compiler suites (build-essential, gcc, g++, cmake) and Go compiler runtime
#    to prevent memory exhaustion (OOM 137) during package extraction.
RUN apt-get update \
 && apt-get -y install --no-install-recommends \
        nmap masscan nikto sqlmap \
        nuclei ffuf gobuster dirsearch \
        subfinder httpx-toolkit \
        wafw00f whatweb \
        socat netcat-traditional tcpdump \
        iproute2 iputils-ping dnsutils whois \
        iptables nftables \
        python3-pip python3-venv pipx \
        git curl wget jq unzip zip tar file procps ca-certificates \
        tor proxychains4 openvpn wireguard-tools \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# 3) Ensure directories exist
RUN mkdir -p /root/.local/bin /workspace

# 4) Web pentest — Python tools via pipx
RUN pipx install arjun

# 5) OPSEC & Anonymity Configuration
# - Configure proxychains4: enable proxy_dns (prevents DNS leak), quiet_mode, and point to local Tor SOCKS5
RUN cat << 'EOF' > /etc/proxychains4.conf
# proxychains.conf - Pre-configured for Scourge OPSEC
dynamic_chain
proxy_dns
quiet_mode
tcp_read_time_out 15000
tcp_connect_time_out 8000

[ProxyList]
# Local Tor SOCKS5 proxy
socks5  127.0.0.1 9050
EOF

# - Fallback Privacy DNS configuration (Quad9 & Cloudflare)
RUN cat << 'EOF' > /etc/resolv.conf.privacy
nameserver 9.9.9.9
nameserver 1.1.1.1
nameserver 149.112.112.112
EOF

# 6) Built-in OPSEC Helper Scripts in /usr/local/bin
# - tor-start: Starts Tor service and verifies SOCKS port 9050 readiness
RUN cat << 'EOF' > /usr/local/bin/tor-start && chmod +x /usr/local/bin/tor-start
#!/bin/sh
set -e
if ! pgrep -x tor >/dev/null 2>&1; then
    echo "[OPSEC] Starting Tor daemon..."
    service tor start >/dev/null 2>&1 || /usr/bin/tor --runasdaemon 1
fi

for i in $(seq 1 20); do
    if nc -z 127.0.0.1 9050 >/dev/null 2>&1; then
        echo "[OPSEC] Tor SOCKS5 circuit ready on 127.0.0.1:9050."
        exit 0
    fi
    sleep 1
done

echo "[OPSEC] Error: Tor started but SOCKS port 127.0.0.1:9050 did not respond in 20s." >&2
exit 1
EOF

# - tor-stop: Stops Tor daemon
RUN cat << 'EOF' > /usr/local/bin/tor-stop && chmod +x /usr/local/bin/tor-stop
#!/bin/sh
service tor stop >/dev/null 2>&1 || pkill -x tor >/dev/null 2>&1 || true
echo "[OPSEC] Tor daemon stopped."
EOF

# - opsec-status: Checks direct egress vs proxychains egress vs VPN, verifying server masking
RUN cat << 'EOF' > /usr/local/bin/opsec-status && chmod +x /usr/local/bin/opsec-status
#!/bin/bash
echo "=========================================="
echo "         SCOURGE OPSEC STATUS CHECK        "
echo "=========================================="

# 1. Tor status
if nc -z 127.0.0.1 9050 2>/dev/null; then
    echo "[+] Tor Daemon: ACTIVE (127.0.0.1:9050)"
else
    echo "[-] Tor Daemon: INACTIVE (Run 'tor-start' to activate)"
fi

# 2. VPN Tunnel status
vpn_dev=""
if ip link show tun0 >/dev/null 2>&1; then
    vpn_dev="tun0 (OpenVPN)"
elif ip link show wg0 >/dev/null 2>&1; then
    vpn_dev="wg0 (WireGuard)"
fi

if [ -n "$vpn_dev" ]; then
    echo "[+] VPN Tunnel: ACTIVE ($vpn_dev)"
else
    echo "[-] VPN Tunnel: NONE"
fi

# 3. Direct IP Check
echo -n "[*] Direct Container Egress IP: "
direct_ip=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "BLOCKED/OFFLINE")
echo "$direct_ip"

# 4. Proxychains Egress IP Check
echo -n "[*] Proxychains (Tor/Proxy) IP: "
proxy_ip=$(proxychains4 -q curl -s --max-time 10 https://api.ipify.org 2>/dev/null || echo "UNAVAILABLE")
echo "$proxy_ip"

# 5. Masking Analysis
if [ "$proxy_ip" != "UNAVAILABLE" ] && [ "$direct_ip" != "BLOCKED/OFFLINE" ]; then
    if [ "$proxy_ip" = "$direct_ip" ]; then
        echo "[!] DANGER: Proxy IP matches direct IP! Anonymity NOT active!"
    else
        echo "[+] VERIFIED: Proxychains traffic is masked through: $proxy_ip"
    fi
fi

# 6. Active DNS Resolvers
echo -n "[*] Configured DNS Resolvers: "
grep "^nameserver" /etc/resolv.conf 2>/dev/null | awk '{print $2}' | tr '\n' ' '
echo ""
echo "=========================================="
EOF

# - opsec-killswitch: Strict egress firewall dropping non-VPN / non-Tor target egress
RUN cat << 'EOF' > /usr/local/bin/opsec-killswitch && chmod +x /usr/local/bin/opsec-killswitch
#!/bin/bash
set -e
echo "[OPSEC] Applying strict egress killswitch..."

# Flush output rules
iptables -F OUTPUT 2>/dev/null || true

# 1. Allow loopback (localhost)
iptables -A OUTPUT -o lo -j ACCEPT

# 2. Allow local Docker internal networks (RFC1918)
iptables -A OUTPUT -d 172.16.0.0/12 -j ACCEPT
iptables -A OUTPUT -d 192.168.0.0/16 -j ACCEPT
iptables -A OUTPUT -d 10.0.0.0/8 -j ACCEPT

# 3. Allow VPN interfaces
iptables -A OUTPUT -o tun+ -j ACCEPT
iptables -A OUTPUT -o wg+ -j ACCEPT

# 4. Allow Tor daemon system user to reach the outside world to build circuits
iptables -A OUTPUT -m owner --uid-owner debian-tor -j ACCEPT

# 5. Drop any direct unproxied egress to external targets
iptables -A OUTPUT -j REJECT --reject-with icmp-net-unreachable

echo "[OPSEC] Killswitch engaged: direct target egress blocked; only Tor, VPN, and local RFC1918 allowed."
EOF

# - use-privacy-dns: Applies Quad9/Cloudflare DNS to /etc/resolv.conf
RUN cat << 'EOF' > /usr/local/bin/use-privacy-dns && chmod +x /usr/local/bin/use-privacy-dns
#!/bin/sh
if [ -f /etc/resolv.conf.privacy ]; then
    cp /etc/resolv.conf.privacy /etc/resolv.conf
    echo "[OPSEC] Replaced /etc/resolv.conf with privacy DNS (Quad9 / Cloudflare)."
fi
EOF

WORKDIR /workspace
CMD ["sleep", "infinity"]
