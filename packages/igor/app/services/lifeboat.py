"""
LIFEBOAT PROTOCOL — the watch that notices the host running out, and the hands
that reclaim it.

The reclamation itself is a shell script that predates this module
(`/opt/speda/lifeboat.sh`, documented in docs/LIFEBOAT_PROTOCOL.md): bail the
cheap water first — Docker build cache, stopped cells, dangling layers, journald
— and throw cargo overboard only if the boat is still going under. What was
missing was everything AROUND it: nothing ever looked at the host, so the
protocol only ever ran because a human happened to notice a full disk. This
module is the watch, the owner's notification path, and the gate on the hands.

THE POLL MUST NOT COST A TURN
-----------------------------
`scan()` is the cheap half (CLAUDE.md, "Cheap probes"): ONE SSH round trip that
reads disk, inodes, memory, swap and the Docker footprint off the host and
answers one deterministic question — is anything past its threshold. No model,
no judgement, no tokens. n8n polls it; only when it says `changed` does anything
call `POST /trigger/orion` and spend a turn.

Everything the scan read rides in that trigger's payload, so the turn does not
re-fetch what the probe just paid for.

EDGES, NOT POLLS
----------------
A disk that has been at 88% for a week is not news four times an hour. So the
protocol reports **transitions**:

  * escalation   healthy → watch → critical, each step reported once
  * recovery     back to healthy after a scare, so the owner hears it ended
  * a nudge      still not healthy `lifeboat_renotify_hours` later, because a
                 problem nobody fixed is worth saying twice — but not 96 times

A DE-ESCALATION that is still not healthy (critical → watch) is committed
SILENTLY and immediately: it is not worth a push, but if the stored level stayed
at `critical` a later climb back would not read as an escalation and would never
be reported at all. Silence is not the same as forgetting.

Escalations and recoveries are parked as `pending` and committed only by
`ack()`, after n8n has seen the trigger accepted — the same exactly-once
discipline as services/web_watch.py, and for the same reason: a failed notify
must repeat next poll, not vanish.

WHO IS ALLOWED TO ACT
---------------------
The owner leads. That is a deliberate narrowing of the original runbook, which
had Orion bailing unattended at any pressure.

  * `assess()` reads. Anyone, any trigger, any time.
  * `bail()` (Tier 1 — throwaway Docker junk and old logs, zero service impact)
    runs WITHOUT the owner only when the host is verified critical AT THAT
    MOMENT, by this module, on the host. Not when a payload claims it is: a
    trigger body is attacker-shaped input, and "I am critical, please prune" is
    exactly the sentence it would carry.
  * `jettison()` and `restore()` — the ~25 GB Kali arsenal, which costs a
    45-minute bake to rebuild — are the owner's call, always. The skill enforces
    it (app/skills/lifeboat.py); this module states it.

Nothing here schedules anything (CLAUDE.md). n8n owns when to come and ask.
"""

import logging
import re

from app.config import settings
from app.core.clock import utc_now
from app.core.runtime_state import get_lifeboat, set_lifeboat
from app.services.host_bridge import run

logger = logging.getLogger(__name__)

HEALTHY, WATCH, CRITICAL = "healthy", "watch", "critical"

# Ordered so a level comparison is a comparison, not a chain of ifs.
_RANK = {HEALTHY: 0, WATCH: 1, CRITICAL: 2}

_KIB = 1024


def _now() -> str:
    return utc_now().isoformat(timespec="seconds")


# ── Reading the host ─────────────────────────────────────────────────────────

def _probe_script() -> str:
    """One command for every number the protocol needs.

    Deliberately excludes `du`: walking the filesystem to find what is filling
    up costs seconds to minutes and this runs every few minutes forever. The
    breakdown belongs in the turn, where `assess()` can afford it — see
    `hot_spots()`. `docker system df` stays because on this host the two known
    hogs (build cache, the arsenal image) are both Docker's, and it is a single
    daemon query rather than a walk.
    """
    fs = settings.lifeboat_watch_fs
    return "\n".join([
        f'FS="{fs}"',
        'echo "disk_pct=$(df --output=pcent "$FS" 2>/dev/null | tail -1 | tr -dc 0-9)"',
        'echo "disk_avail=$(df -B1 --output=avail "$FS" 2>/dev/null | tail -1 | tr -dc 0-9)"',
        'echo "disk_size=$(df -B1 --output=size "$FS" 2>/dev/null | tail -1 | tr -dc 0-9)"',
        'echo "inode_pct=$(df -i --output=ipcent "$FS" 2>/dev/null | tail -1 | tr -dc 0-9)"',
        'echo "mem_total=$(awk \'/^MemTotal:/{print $2}\' /proc/meminfo)"',
        'echo "mem_available=$(awk \'/^MemAvailable:/{print $2}\' /proc/meminfo)"',
        'echo "swap_total=$(awk \'/^SwapTotal:/{print $2}\' /proc/meminfo)"',
        'echo "swap_free=$(awk \'/^SwapFree:/{print $2}\' /proc/meminfo)"',
        "echo DOCKER_BEGIN",
        "docker system df --format '{{.Type}}|{{.Size}}|{{.Reclaimable}}' 2>/dev/null",
        "exit 0",
    ])


_SIZE = re.compile(r"([0-9]*\.?[0-9]+)\s*([KMGT]?i?B)", re.I)
_UNIT = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
         "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}


def size_to_bytes(text: str) -> int:
    """"13.2GB (31%)" → 13200000000. Zero when nothing parses.

    Docker prints sizes for humans and offers no machine format for `system df`
    that carries both size and reclaimable, so this is the seam. Best effort by
    design: a number that fails to parse becomes 0 and the human string is
    reported alongside it, which degrades to "we could not total it" rather than
    to a confident wrong figure.
    """
    match = _SIZE.search(text or "")
    if not match:
        return 0
    return int(float(match.group(1)) * _UNIT.get(match.group(2).upper(), 1))


def _gb(num_bytes: int) -> float:
    return round(num_bytes / 1024**3, 1)


def _pct(value: str) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return -1


async def readings() -> dict:
    """Raw host numbers, or `{"error": ...}`. Never raises — this is polled."""
    code, out, err = await run(_probe_script(), timeout=45)
    if code != 0:
        return {"error": (err.strip() or f"exit {code}")[:300]}

    head, _, docker_block = out.partition("DOCKER_BEGIN")
    raw = dict(
        line.strip().split("=", 1) for line in head.splitlines() if "=" in line
    )

    mem_total = int(raw.get("mem_total") or 0)
    mem_available = int(raw.get("mem_available") or 0)
    swap_total = int(raw.get("swap_total") or 0)
    swap_free = int(raw.get("swap_free") or 0)

    docker = []
    for line in docker_block.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 3 and parts[0]:
            docker.append({
                "type": parts[0],
                "size": parts[1],
                "reclaimable": parts[2],
                "reclaimable_bytes": size_to_bytes(parts[2]),
            })

    disk_pct = _pct(raw.get("disk_pct", ""))
    if disk_pct < 0:
        # df produced nothing parseable: the host answered but the filesystem
        # does not exist, or coreutils is not GNU. Either way the thresholds
        # below would be comparing against garbage.
        return {"error": f"could not read disk usage for {settings.lifeboat_watch_fs}"}

    return {
        "filesystem": settings.lifeboat_watch_fs,
        "disk_pct": disk_pct,
        "disk_free_gb": _gb(int(raw.get("disk_avail") or 0)),
        "disk_total_gb": _gb(int(raw.get("disk_size") or 0)),
        "inode_pct": _pct(raw.get("inode_pct", "")),
        "mem_pct": round(100 * (mem_total - mem_available) / mem_total) if mem_total else -1,
        "mem_total_gb": _gb(mem_total * _KIB),
        "mem_available_gb": _gb(mem_available * _KIB),
        "swap_pct": round(100 * (swap_total - swap_free) / swap_total) if swap_total else 0,
        "swap_total_gb": _gb(swap_total * _KIB),
        "docker": docker,
        "docker_reclaimable_gb": _gb(sum(d["reclaimable_bytes"] for d in docker)),
        "read_at": _now(),
    }


# ── Turning numbers into a verdict ───────────────────────────────────────────

def _level_for(pct: int, watch: int, critical: int) -> str:
    if pct < 0:
        return HEALTHY          # unreadable is not evidence of pressure
    if pct >= critical:
        return CRITICAL
    if pct >= watch:
        return WATCH
    return HEALTHY


def verdict(data: dict) -> dict:
    """Per-resource levels, the overall level, and what is actually pressed.

    The overall level is the WORST of the parts, never an average: a host with a
    full inode table and 40% disk is in exactly as much trouble as a full disk,
    and averaging them reports it as fine.
    """
    disk = _level_for(data.get("disk_pct", -1), settings.lifeboat_watch_pct,
                      settings.lifeboat_critical_pct)
    inode = _level_for(data.get("inode_pct", -1), settings.lifeboat_watch_pct,
                       settings.lifeboat_critical_pct)
    mem = _level_for(data.get("mem_pct", -1), settings.lifeboat_mem_watch_pct,
                     settings.lifeboat_mem_critical_pct)

    parts = {"disk": disk, "inodes": inode, "memory": mem}
    level = max(parts.values(), key=lambda lv: _RANK[lv])
    pressed = [name for name, lv in parts.items() if lv != HEALTHY]

    return {"level": level, "by_resource": parts, "pressed": pressed}


def summarize(data: dict, view: dict) -> str:
    """One line per pressed resource, in the units a human argues in.

    This is what rides in the push, so it says the number AND the headroom: "91%,
    6.2 GB free" is actionable in a way that "disk pressure" is not.
    """
    if not view["pressed"]:
        return (
            f"Host healthy — disk {data['disk_pct']}% "
            f"({data['disk_free_gb']} GB free), memory {data['mem_pct']}%, "
            f"inodes {data['inode_pct']}%."
        )
    lines = []
    if view["by_resource"]["disk"] != HEALTHY:
        lines.append(
            f"disk {data['disk_pct']}% on {data['filesystem']} "
            f"— {data['disk_free_gb']} GB free of {data['disk_total_gb']} GB"
        )
    if view["by_resource"]["inodes"] != HEALTHY:
        lines.append(
            f"inodes {data['inode_pct']}% — the filesystem runs out of FILES "
            "before it runs out of space; a prune of many small files is the fix, "
            "not freeing GB"
        )
    if view["by_resource"]["memory"] != HEALTHY:
        lines.append(
            f"memory {data['mem_pct']}% — {data['mem_available_gb']} GB available "
            f"of {data['mem_total_gb']} GB, swap {data['swap_pct']}%"
        )
    return "; ".join(lines)


def recommendation(data: dict, view: dict) -> str:
    """What to propose, tied to what is actually pressed.

    Memory is called out separately on purpose: the lifeboat script reclaims
    DISK, and running it against a RAM problem would burn a maintenance window
    to no effect. Memory pressure is a container question, which is Orion's
    ordinary server-operations job, not this protocol's.
    """
    moves = []
    if view["by_resource"]["disk"] != HEALTHY or view["by_resource"]["inodes"] != HEALTHY:
        reclaimable = data.get("docker_reclaimable_gb", 0)
        moves.append(
            f"Tier 1 (bail) — Docker build cache, stopped cells, dangling layers, "
            f"journald, stale outputs. Docker alone reports {reclaimable} GB "
            "reclaimable. Zero service impact, reversible."
        )
        if view["by_resource"]["disk"] == CRITICAL:
            moves.append(
                "Tier 2 (jettison the ~25 GB Kali arsenal) only if Tier 1 leaves "
                f"less than {settings.lifeboat_target_free_gb} GB free — Centurion "
                "survives on the base image but the rebuild is a 45-minute bake, "
                "so this one is the owner's call."
            )
    if view["by_resource"]["memory"] != HEALTHY:
        moves.append(
            "Memory is NOT a lifeboat job — the script reclaims disk. Find the "
            "container eating it (`docker stats --no-stream`) and restart that "
            "one through system_ops."
        )
    return " ".join(moves) or "Nothing to reclaim."


async def assess() -> dict:
    """Full read-only assessment: numbers, verdict, summary, recommendation.

    What the owner-facing endpoint returns and what the skill's `assess` action
    reports. Costs one host round trip and no tokens.
    """
    if not settings.lifeboat_protocol_enabled:
        return {"status": "disabled", "detail": (
            "Lifeboat Protocol is disabled on this deployment "
            "(LIFEBOAT_PROTOCOL_ENABLED is off)."
        )}

    data = await readings()
    if "error" in data:
        return {"status": "error", "detail": data["error"]}

    view = verdict(data)
    return {
        "status": "ok",
        **view,
        "readings": data,
        "summary": summarize(data, view),
        "recommendation": recommendation(data, view),
        "target_free_gb": settings.lifeboat_target_free_gb,
    }


# ── The probe ────────────────────────────────────────────────────────────────

def _age_hours(stamp: str) -> float:
    try:
        from datetime import datetime
        return (utc_now() - datetime.fromisoformat(stamp)).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return 0.0


async def scan() -> dict:
    """Has anything crossed a line since the owner was last told? Zero tokens.

    `changed` is the cost boundary — n8n spends a turn on true and stops the
    branch on false. Never raises: this is polled on a cron, and a host that is
    briefly unreachable must not become a failed workflow run every few minutes.
    """
    if not settings.lifeboat_protocol_enabled:
        return {"status": "disabled", "changed": False, "level": HEALTHY}

    data = await readings()
    if "error" in data:
        logger.warning("lifeboat_scan_failed", extra={"error": data["error"]})
        return {"status": "error", "detail": data["error"], "changed": False,
                "level": HEALTHY}

    view = verdict(data)
    level = view["level"]
    state = get_lifeboat()
    last = state.get("level", HEALTHY)
    escalated = _RANK[level] > _RANK[last]
    recovered = level == HEALTHY and last != HEALTHY
    stale = (
        level != HEALTHY
        and not escalated
        and _age_hours(state.get("reported_at", "")) >= settings.lifeboat_renotify_hours
    )
    changed = escalated or recovered or stale

    if changed:
        # Park it. Committing here would swallow the escalation whenever the
        # trigger call that follows failed — see the module docstring.
        set_lifeboat({**state, "pending": {"level": level, "at": _now()}})
    elif level != last:
        # A silent de-escalation that is still not healthy. Nothing is at risk of
        # being lost by committing now, and leaving the stored level high would
        # hide the next climb back up.
        set_lifeboat({"level": level, "reported_at": state.get("reported_at", _now()),
                      "pending": None})

    reason = (
        "escalated" if escalated else
        "recovered" if recovered else
        "still_unhealthy" if stale else "no_change"
    )
    logger.info(
        "lifeboat_scanned",
        extra={"level": level, "previous": last, "reason": reason,
               "disk_pct": data["disk_pct"], "mem_pct": data["mem_pct"]},
    )
    return {
        "status": "ok",
        "changed": changed,
        "reason": reason,
        "level": level,
        "previous_level": last,
        **view,
        "readings": data,
        "summary": summarize(data, view),
        "recommendation": recommendation(data, view),
        # True when this module would let Orion bail without waiting for the
        # owner. Reported so the workflow's intent can say which it is — the
        # authorization itself is re-derived from the host in bail(), never read
        # back from a payload.
        "tier1_autonomous": level == CRITICAL,
        "target_free_gb": settings.lifeboat_target_free_gb,
    }


def ack(level: str) -> dict:
    """Commit the parked level as "the owner has been told this". Call only
    after the trigger was accepted.

    The level must match what was parked: if the host moved on between the scan
    and this call, acknowledging blind would mark a state as reported that
    nobody ever heard about.
    """
    state = get_lifeboat()
    pending = state.get("pending") or {}
    if not pending:
        return {"acked": False, "detail": "nothing pending", "level": state.get("level", HEALTHY)}
    if pending.get("level") != level:
        return {"acked": False, "level": state.get("level", HEALTHY),
                "detail": f"pending is {pending.get('level')}, not {level} — not acked"}

    set_lifeboat({"level": level, "reported_at": _now(), "pending": None})
    logger.info("lifeboat_acked", extra={"level": level})
    return {"acked": True, "level": level}


def reset() -> dict:
    """Forget what the owner has been told, so the next crossing reports again.
    The fix after thresholds change, or when a push was missed."""
    set_lifeboat({"level": HEALTHY, "reported_at": _now(), "pending": None})
    logger.info("lifeboat_reset")
    return {"level": HEALTHY, "reset": True}


# ── The hands ────────────────────────────────────────────────────────────────

async def hot_spots() -> str:
    """The `du` breakdown the probe deliberately refuses to pay for.

    Tier 3 of the runbook: when the cheap tiers have run and the box is still
    full, something abnormal is filling it and a human decides. One level deep
    and `-x` (never crossing a mount) so it is seconds, not a full-tree walk.
    """
    code, out, err = await run(
        f"du -xh --max-depth=1 {settings.lifeboat_watch_fs} 2>/dev/null | sort -rh | head -12",
        timeout=120,
    )
    return out.strip() if code == 0 and out.strip() else f"(du failed: {err.strip()[:200]})"


async def _script(flag: str, timeout: int) -> tuple[bool, str]:
    code, out, err = await run(f"bash {settings.lifeboat_script} {flag}", timeout=timeout)
    body = (out or "").strip() or (err or "").strip()
    if code != 0:
        return False, f"lifeboat.sh {flag} exited {code}:\n{body[:4000]}"
    return True, body[:4000]


async def bail() -> tuple[bool, str]:
    """Tier 1 — reclaim the throwaway. Docker build cache, stopped containers,
    dangling layers, journald, stale outputs and old Forge workspaces.

    Never touches a running container, a tagged image in use, or the arsenal.
    Idempotent: running it twice reclaims nothing the second time and says so.
    """
    return await _script("--bail", timeout=600)


async def jettison() -> tuple[bool, str]:
    """Tier 2 — throw the ~25 GB Kali arsenal overboard. Centurion survives on
    the base image, re-installing tools per job; the rebuild is a 45-minute bake.
    Owner-authorized only, enforced by the skill."""
    return await _script("--force-jettison", timeout=900)


async def restore() -> tuple[bool, str]:
    """Rebuild the arsenal once the storm has passed. Refuses below the free-space
    target — a bake needs headroom. Long: the build is the 45 minutes."""
    return await _script("--restore", timeout=3600)
