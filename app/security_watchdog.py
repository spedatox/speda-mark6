"""Linux host monitor. Only structured observations cross into Igor."""

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from uuid import NAMESPACE_URL, uuid5


WINDOW = 60
INTERVAL = 10
LOG_BYTES = 1024 * 1024
JOURNAL_LINES = 4096
INBOX_SIZE = 512
MAX_OBSERVATION_BYTES = 1024
SSH_PATTERN = r"^(Failed (password|publickey|keyboard-interactive(/pam)?|none) for |Invalid user )"
SSH_SOURCE = re.compile(r"([0-9a-fA-F:.]+) port [0-9]+(?: ssh2(?:: [^\n]*)?| \[preauth\])?")
DEFAULTS = {
    "ssh_per_ip": 12, "ssh_total": 60,
    "http_auth_per_ip": 20, "http_auth_total": 100,
    "http_per_ip": 600, "http_total": 3000,
    "ingress_bps": 200_000_000, "ingress_pps": 50_000,
    "cooldown_seconds": 900,
}
SIGNALS = {
    "ssh_brute_force": ("SSH brute-force indicators", "SSH authentication failures or invalid-user probes"),
    "http_brute_force": ("HTTP brute-force indicators", "HTTP 401 responses"),
    "http_flood": ("Possible HTTP flood", "HTTP responses"),
    "ingress_bps": ("Unusually high network ingress", "incoming bits/s"),
    "ingress_pps": ("Unusually high packet rate", "incoming packets/s"),
    "ssh_visibility": ("SSH monitoring has limited visibility", "SSH journal reads unavailable or capped"),
    "http_visibility": ("HTTP monitoring has limited visibility", "Caddy access log reads unavailable or capped"),
    "network_visibility": ("Network monitoring has limited visibility", "interface counter reads unavailable"),
}
logger = logging.getLogger("speda-security-watchdog")


@dataclass(frozen=True)
class Observation:
    signal: str
    observed_at: int
    count: int
    threshold: int
    seconds: int
    address: str | None = None

    @classmethod
    def parse(cls, raw):
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError("Invalid observation")
        observation = cls(**row)
        if not isinstance(observation.signal, str) or observation.signal not in SIGNALS:
            raise ValueError("Unknown signal")
        for value, maximum in ((observation.observed_at, 4_102_444_800), (observation.count, 10**15),
                               (observation.threshold, 10**12), (observation.seconds, 300)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Observation value out of range")
        if observation.count < observation.threshold:
            raise ValueError("Threshold was not reached")
        if observation.address is not None and normalize_ip(observation.address) != observation.address:
            raise ValueError("Invalid source address")
        return observation

    def facts(self):
        title, unit = SIGNALS[self.signal]
        source = f" from {self.address}" if self.address else ""
        details = f"{self.count:,} {unit}{source} over {self.seconds}s (threshold {self.threshold:,})."
        summary = title + "." if self.signal.endswith("_visibility") else f"{title}. {details}"
        return {
            "event": "security_" + self.signal, "component": "security_watchdog",
            "severity": "CRITICAL" if self.signal in {"http_flood", "ingress_bps", "ingress_pps"} else "WARNING",
            "summary": summary, "signal": self.signal, "count": self.count, "threshold": self.threshold,
            "window_seconds": self.seconds, "source_ip": self.address,
            "observed_at": datetime.fromtimestamp(self.observed_at, timezone.utc).isoformat(timespec="seconds"),
        }


def normalize_ip(value):
    if not isinstance(value, str) or len(value) > 45 or "%" in value:
        return None
    try:
        address = ip_address(value)
    except ValueError:
        return None
    return str(getattr(address, "ipv4_mapped", None) or address)


def object_row(line):
    try:
        row = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return row if isinstance(row, dict) else None


def ssh_counts(unit, now):
    result = subprocess.run(
        ["/usr/bin/journalctl", "--quiet", "--no-pager", "--unit", unit,
         "--since", f"@{now - WINDOW}", "--until", f"@{now}",
         "--grep", SSH_PATTERN, "--lines", str(JOURNAL_LINES), "--output=json", "--output-fields=MESSAGE"],
        capture_output=True, timeout=5,
    )
    # journalctl --grep exits 1 for a successful query with no matches.
    if result.returncode != 1 or result.stderr or result.stdout:
        result.check_returncode()
    failures, probes = Counter(), Counter()
    lines = result.stdout.splitlines()
    for line in lines:
        row = object_row(line)
        message = row.get("MESSAGE") if row else None
        if not isinstance(message, str):
            continue
        _, separator, peer = message.rpartition(" from ")
        if separator and (match := SSH_SOURCE.fullmatch(peer)) and (address := normalize_ip(match[1])):
            counts = probes if message.startswith("Invalid user ") else failures
            counts[address] += 1
    # A failed attempt against an invalid user can appear in both streams.
    return failures | probes, len(lines) >= JOURNAL_LINES


def caddy_tail(path, now):
    """Read newest records within a fixed byte budget, including across a roll."""
    rotated = sorted(path.parent.glob(f"{path.stem}-*{path.suffix}"), reverse=True)
    remaining = LOG_BYTES
    seen = set()
    for candidate in (path, *rotated):
        try:
            with candidate.open("rb") as stream:
                info = os.fstat(stream.fileno())
                identity = (info.st_dev, info.st_ino)
                if identity in seen:
                    continue
                seen.add(identity)
                if info.st_mtime < now - WINDOW:
                    continue
                size = min(remaining, info.st_size)
                stream.seek(info.st_size - size)
                block = stream.read(size)
        except FileNotFoundError:
            continue
        remaining -= len(block)
        lines = block.split(b"\n")[:-1]
        if size < info.st_size:
            lines = lines[1:]
        yield lines, remaining == 0
        if not remaining:
            break
    if not seen:
        raise FileNotFoundError(path)


def http_counts(path, now):
    responses, denied = Counter(), Counter()
    oldest = now
    clipped = False
    malformed = False
    for lines, clipped in caddy_tail(path, now):
        for line in lines:
            row = object_row(line)
            if row is None or type(row.get("ts")) not in (int, float):
                malformed = True
                continue
            ts = row["ts"]
            oldest = min(ts, oldest)
            if not now - WINDOW <= ts <= now:
                continue
            request = row.get("request")
            address = normalize_ip(request.get("remote_ip")) if isinstance(request, dict) else None
            status = row.get("status")
            if not address or type(status) is not int:
                malformed = True
                continue
            responses[address] += 1
            if status == 401:
                denied[address] += 1
    return responses, denied, malformed or (clipped and oldest > now - WINDOW)


def burst(signal, counts, now, per_ip, total):
    if counts.total() >= total:
        return Observation(signal, now, counts.total(), total, WINDOW)
    if counts:
        address, count = counts.most_common(1)[0]
        if count >= per_ip:
            return Observation(signal, now, count, per_ip, WINDOW, address)
    return None


def ingress_sample(interface):
    device = Path("/sys/class/net") / interface
    return (time.monotonic(), int((device / "ifindex").read_text()),
            int((device / "statistics/rx_bytes").read_text()),
            int((device / "statistics/rx_packets").read_text()))


class Ingress:
    def __init__(self):
        self.previous = None
        self.exceeded = set()

    def observe(self, sample, now, limits):
        previous, self.previous = self.previous, sample
        exceeded, self.exceeded = self.exceeded, set()
        if previous is None or sample[1] != previous[1]:
            return []
        elapsed = sample[0] - previous[0]
        if not 1 <= elapsed <= INTERVAL * 3 or any(a < b for a, b in zip(sample[2:], previous[2:])):
            return []
        rates = {"ingress_bps": int((sample[2] - previous[2]) * 8 / elapsed),
                 "ingress_pps": int((sample[3] - previous[3]) / elapsed)}
        self.exceeded = {signal for signal, rate in rates.items() if rate >= limits[signal]}
        return [Observation(signal, now, rate, limits[signal], round(elapsed))
                for signal, rate in rates.items() if signal in exceeded & self.exceeded]


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".watchdog-", delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(json.dumps(data).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    if hasattr(os, "O_DIRECTORY"):
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


class Outbox:
    def __init__(self, inbox, checkpoint, cooldown):
        self.inbox = inbox
        self.checkpoint = checkpoint
        self.cooldown = cooldown
        try:
            self.last = json.loads(checkpoint.read_bytes())
        except FileNotFoundError:
            self.last = {}
        if not isinstance(self.last, dict) or any(type(value) is not int for value in self.last.values()):
            raise ValueError("Invalid watchdog cooldown checkpoint")

    def publish(self, observation):
        signal, now = observation.signal, observation.observed_at
        if now - self.last.get(signal, 0) < self.cooldown:
            return
        identity = uuid5(NAMESPACE_URL, f"speda/watchdog/{signal}/{now // self.cooldown}").hex
        path = self.inbox / f"{identity}.json"
        if not path.exists():
            if sum(1 for _ in self.inbox.glob("*.json")) >= INBOX_SIZE:
                raise OSError("Security inbox is full")
            atomic_write(path, asdict(observation))
        updated = {**self.last, signal: now}
        atomic_write(self.checkpoint, updated)
        self.last = updated
        logger.info("Queued %s (%s)", signal, identity)


def collect(args, limits, ingress, now):
    try:
        ssh, clipped = ssh_counts(args.ssh_unit, now)
        yield burst("ssh_brute_force", ssh, now, limits["ssh_per_ip"], limits["ssh_total"])
    except (OSError, subprocess.SubprocessError):
        clipped = True
    if clipped:
        yield Observation("ssh_visibility", now, 1, 1, WINDOW)

    try:
        http, denied, clipped = http_counts(args.caddy_log, now)
        yield burst("http_flood", http, now, limits["http_per_ip"], limits["http_total"])
        yield burst("http_brute_force", denied, now, limits["http_auth_per_ip"], limits["http_auth_total"])
    except OSError:
        clipped = True
    if clipped:
        yield Observation("http_visibility", now, 1, 1, WINDOW)

    try:
        yield from ingress.observe(ingress_sample(args.interface), now, limits)
    except (OSError, ValueError):
        ingress.previous = None
        yield Observation("network_visibility", now, 1, 1, INTERVAL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--caddy-log", required=True, type=Path)
    parser.add_argument("--inbox", required=True, type=Path)
    parser.add_argument("--state", type=Path, default=Path("/var/lib/speda-security-watchdog"))
    parser.add_argument("--ssh-unit", default="ssh.service")
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--check", action="store_true", help="Check input sources without queuing alerts.")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,14}", args.interface):
        parser.error("Invalid Linux interface name")
    limits = DEFAULTS.copy()
    if args.thresholds:
        overrides = json.loads(args.thresholds.read_bytes())
        if (not isinstance(overrides, dict) or overrides.keys() - limits.keys()
                or any(type(value) is not int or not 1 <= value <= 10**12 for value in overrides.values())):
            parser.error("Invalid watchdog thresholds")
        limits.update(overrides)
    if not 300 <= limits["cooldown_seconds"] <= 86400:
        parser.error("Cooldown must be between 300 and 86400 seconds")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ingress = Ingress()
    if args.check:
        gaps = [item.signal for item in collect(args, limits, ingress, int(time.time()))
                if item and item.signal.endswith("_visibility")]
        if gaps:
            logger.error("Input checks failed: %s", ", ".join(gaps))
        else:
            logger.info("SSH journal, Caddy access log, and interface counters are readable")
        return int(bool(gaps))
    outbox = Outbox(args.inbox, args.state / "cooldown.json", limits["cooldown_seconds"])
    logger.info("Watching %s, %s, and %s", args.ssh_unit, args.caddy_log, args.interface)
    while True:
        start = time.monotonic()
        for observation in collect(args, limits, ingress, int(time.time())):
            if observation:
                try:
                    outbox.publish(observation)
                except OSError as exc:
                    logger.error("Could not queue %s: %s", observation.signal, exc)
        time.sleep(max(1, INTERVAL - (time.monotonic() - start)))


if __name__ == "__main__":
    raise SystemExit(main())
