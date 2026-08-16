"""
LOCKDOWN PROTOCOL — automatic inbound containment, and its own way back out.

Engaging drops external traffic to the host's exposed inbound ports; standing
down removes exactly the rules it added. Both run as plain service calls, not as
something an agent has to reason its way through with shell commands — the whole
point is that containment happens the moment the flag flips.

IT STOPS DEPLOYS. THAT IS THE POINT, BUT KNOW IT
------------------------------------------------
Sealing inbound SSH seals the channel CI deploys over
(.github/workflows/deploy.yml SSHes in and runs deploy.sh). While engaged, every
push to main fails at the SSH step after ~30s — the action's connect timeout —
and the server is never touched, so nothing half-deploys. That is correct
behaviour: containment that politely exempted a remote-code-execution path would
not be containment. But it fails in a place that says nothing about lockdown, so:
if deploys are timing out, check `GET /agents/lockdown` before anything else. It
reports the real firewall rules alongside the flag, precisely because those two
can disagree. Stand down to deploy; re-engage after.

WHAT STAYS OPEN, AND WHY IT MUST
--------------------------------
Only INBOUND host SSH (`system_ops_ssh_port`, plus 22 when that differs) and
8000 (the app's raw published port) are dropped. Left alone, deliberately:

  * 443/80 — Caddy, and therefore the app itself. This is the channel the
    desktop client is built against and the one that carries the disengage call.
    Blocking it would make the protocol unliftable from the app.
  * The Docker bridge subnet and loopback — every rule carries an exemption for
    them, which is what keeps the host bridge (services/host_bridge.py) alive
    while engaged. Orion can still operate the host; lockdown can still be
    lifted from inside.
  * All outbound traffic — the model APIs, Google, Telegram. A contained SPEDA
    is still a working SPEDA.

WHY TWO CHAINS
--------------
The two ports are exposed by different machinery. Port 22 is a host process, so
a plain INPUT rule catches it. Port 8000 is published by Docker, which installs
its own NAT/forward rules that bypass INPUT entirely — the supported hook for
that traffic is the DOCKER-USER chain, which Docker leaves under our control.
One rule in the wrong chain silently does nothing, which is precisely the kind
of "contained" that isn't.

WHY THREE RULES PER PORT INSTEAD OF ONE
---------------------------------------
The natural spec — "drop everything except the bridge and loopback" — cannot be
written as a single rule: iptables refuses more than one -s flag ("multiple -s
flags not allowed"). So each port is sealed with ACCEPT rules for the exempt
sources sitting above a blanket DROP. They are INSERTED in reverse (DROP first,
exemptions after) because -I always inserts at position 1, which leaves the
final order as ACCEPT…, ACCEPT, DROP.

Every Docker network is exempted, not just the default bridge. The app container
runs on the COMPOSE network (172.18.x here), not docker0's 172.17.x — exempting
only the default bridge would seal the container out of the host and make the
protocol unliftable from inside, which is the one failure this must never have.

Rules are inserted with -I and removed by the identical spec with -D, so
disengage never flushes a chain or disturbs anything else on the host firewall.
"""

import logging

from app.config import settings
from app.core.runtime_state import get_lockdown, set_lockdown
from app.services.host_bridge import run

logger = logging.getLogger(__name__)

def _sealed() -> tuple[tuple[str, int, str], ...]:
    """Inbound ports sealed while engaged: (chain, port, what it is).

    443/80 are absent by design — see the module docstring.

    The SSH entry is derived from `system_ops_ssh_port` rather than hardcoded to
    22, because a hardcoded 22 gets containment exactly backwards on any host
    that moved sshd: it seals a port nothing listens on while the real SSH stays
    wide open, and you are told you are contained. The host bridge already reads
    that setting to reach the host (services/host_bridge.py), so it is the same
    number by construction.

    22 is still sealed when the real port differs, because it is an inbound
    surface either way and sealing it costs nothing when it is closed.
    """
    ssh_port = int(settings.system_ops_ssh_port or 22)
    ports = [("INPUT", ssh_port, f"host SSH ({ssh_port})")]
    if ssh_port != 22:
        ports.append(("INPUT", 22, "host SSH (22)"))
    ports.append(("DOCKER-USER", 8000, "app raw port"))
    return tuple(ports)

# Always exempt, on top of every Docker subnet found at engage time.
_LOOPBACK = "127.0.0.0/8"


async def _exempt_sources() -> list[str] | None:
    """Sources that keep reaching the sealed ports while engaged: every Docker
    network subnet, plus loopback.

    None when the Docker subnets cannot be read — the caller must then refuse to
    engage rather than seal with an incomplete exemption list and strand the
    host bridge on the wrong side of its own firewall rule.
    """
    code, out, _ = await run(
        "docker network ls -q | xargs -r docker network inspect "
        "--format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'",
        timeout=25,
    )
    subnets = [
        line.strip() for line in out.splitlines()
        if code == 0 and line.strip() and "/" in line
    ]
    if not subnets:
        # Docker unreachable — try the kernel's view of docker0. It only covers
        # the default bridge, so it is a floor, not a substitute.
        code, out, _ = await run("ip -4 -o addr show docker0 | awk '{print $4}'", timeout=15)
        subnets = [
            line.strip() for line in out.splitlines()
            if code == 0 and line.strip() and "/" in line
        ]
    if not subnets:
        return None
    # Deduplicate, keep order stable so inserts and deletes pair up predictably.
    return list(dict.fromkeys(subnets)) + [_LOOPBACK]


def _drop_rule(chain: str, port: int, verb: str) -> str:
    return f"iptables {verb} {chain} -p tcp --dport {port} -j DROP"


def _accept_rule(chain: str, port: int, source: str, verb: str) -> str:
    return f"iptables {verb} {chain} -p tcp --dport {port} -s {source} -j ACCEPT"


async def _present(rule_check: str) -> bool:
    code, _, _ = await run(rule_check, timeout=15)
    return code == 0


async def engage() -> tuple[bool, str]:
    """Seal the inbound ports and persist the flag. Returns (ok, report).

    Idempotent: a rule already present is left alone rather than stacked, so
    repeated engages cannot pile up duplicates that a single disengage would
    fail to clear.
    """
    if not settings.lockdown_protocol_enabled:
        return False, (
            "Lockdown Protocol is disabled on this deployment "
            "(LOCKDOWN_PROTOCOL_ENABLED is off). Nothing was changed."
        )

    exempt = await _exempt_sources()
    if exempt is None:
        logger.error("lockdown_engage_no_subnets")
        return False, (
            "REFUSED — could not enumerate the Docker network subnets, so the "
            "rules would have had no exemption for the host bridge and lockdown "
            "could not be lifted from inside. Nothing was changed."
        )

    applied, already, failed = [], [], []
    flagged = False

    def _mark_contained() -> None:
        """Persist the flag the moment the FIRST seal is real.

        The flag still follows the firewall — it is never set before a rule is
        actually in place — but it no longer waits for the WHOLE loop. That wait
        was minutes wide: every rule costs several SSH round trips to the host at
        up to 25s each, and if the caller gave up in the middle (a browser fetch
        timing out is enough to cancel the request), the ports stayed sealed with
        the flag still reading off. That drift is the worst state this module can
        produce, because `reconcile_on_startup` skips a clear flag and the UI
        offers no way down from a lockdown it does not believe is on.
        """
        nonlocal flagged
        if not flagged:
            set_lockdown(True)
            flagged = True

    for chain, port, label in _sealed():
        where = f"{label} ({chain}:{port})"
        if await _present(_drop_rule(chain, port, "-C")):
            already.append(where)
            # Already sealed with the flag clear IS the drift state — recording
            # it here is what lets a re-engage repair a previous half-run.
            _mark_contained()
            continue

        # Exemptions go in AFTER the drop so they land above it (-I inserts at
        # position 1). Order here is the reverse of the order in the chain.
        code, _, err = await run(_drop_rule(chain, port, "-I"), timeout=20)
        if code != 0:
            failed.append(f"{where}: {err.strip() or f'exit {code}'}")
            continue

        stranded = []
        for source in exempt:
            if await _present(_accept_rule(chain, port, source, "-C")):
                continue
            code, _, err = await run(_accept_rule(chain, port, source, "-I"), timeout=20)
            if code != 0:
                stranded.append(f"{source}: {err.strip() or f'exit {code}'}")

        if stranded:
            # An exemption that did not apply is how the host bridge ends up on
            # the wrong side of the DROP. Roll THIS port back rather than leave a
            # seal nobody can reach through.
            await run(_drop_rule(chain, port, "-D"), timeout=20)
            for source in exempt:
                await run(_accept_rule(chain, port, source, "-D"), timeout=20)
            failed.append(f"{where}: exemptions failed ({'; '.join(stranded)}) — rolled back")
            continue

        applied.append(where)
        _mark_contained()

    # The flag follows the firewall, never leads it: a half-applied lockdown that
    # reported success is how someone ends up believing they are contained.
    if failed and not applied and not already:
        logger.error("lockdown_engage_failed", extra={"failed": failed})
        return False, (
            "REFUSED — no containment rule could be applied, so the host is NOT "
            "sealed and the protocol stays down: " + "; ".join(failed)
        )

    set_lockdown(True)
    logger.warning(
        "lockdown_engaged",
        extra={"exempt": exempt, "applied": applied, "already": already, "failed": failed},
    )

    lines = ["LOCKDOWN PROTOCOL ENGAGED — inbound containment active."]
    if applied:
        lines.append("Sealed: " + ", ".join(applied))
    if already:
        lines.append("Already sealed: " + ", ".join(already))
    if failed:
        lines.append("PARTIAL — these did NOT seal: " + "; ".join(failed))
    lines.append(
        "Still open by design: HTTPS 443/80 (the app and this protocol's own "
        f"disengage path), {', '.join(exempt)}, and all outbound."
    )
    return True, "\n".join(lines)


async def disengage() -> tuple[bool, str]:
    """Remove the containment rules and clear the flag. Returns (ok, report).

    This is the escape hatch, so it is deliberately forgiving: it clears the flag
    even when a rule delete fails, and reports what remains. A stuck flag with a
    clean firewall would leave the UI claiming a containment that isn't there.
    """
    # Exemptions are removed by the same spec they went in with, so the current
    # subnet list is what matches. A subnet that has since disappeared leaves its
    # ACCEPT behind — harmless (it only ever widened access to a network that no
    # longer exists) and the DROP above it is what actually gets lifted.
    exempt = await _exempt_sources() or [_LOOPBACK]
    removed, failed = [], []

    for chain, port, label in _sealed():
        where = f"{label} ({chain}:{port})"
        cleared = False
        # Loop: a repeated engage against an older build may have stacked
        # duplicates, and -D removes one match per call.
        while await _present(_drop_rule(chain, port, "-C")):
            code, _, err = await run(_drop_rule(chain, port, "-D"), timeout=20)
            if code != 0:
                failed.append(f"{where}: {err.strip() or f'exit {code}'}")
                break
            cleared = True
        for source in exempt:
            while await _present(_accept_rule(chain, port, source, "-C")):
                code, _, _ = await run(_accept_rule(chain, port, source, "-D"), timeout=20)
                if code != 0:
                    break
        if cleared:
            removed.append(where)

    set_lockdown(False)
    logger.warning("lockdown_disengaged", extra={"removed": removed, "failed": failed})

    lines = ["LOCKDOWN PROTOCOL STOOD DOWN — normal access restored."]
    lines.append("Reopened: " + (", ".join(removed) if removed else "nothing was sealed"))
    if failed:
        lines.append(
            "WARNING — these rules could not be removed and may still be "
            "blocking traffic: " + "; ".join(failed)
        )
    return True, "\n".join(lines)


async def reconcile_on_startup() -> None:
    """Re-apply containment at boot when the flag says engaged.

    The rules live in the kernel, not on disk: a host reboot drops them unless
    iptables-persistent happens to be installed, which is not something to
    assume. Without this, a restart during an incident would quietly reopen the
    ports while every client still displayed LOCKDOWN ACTIVE. Never touches the
    firewall when the flag is clear.
    """
    if not get_lockdown():
        return
    if not settings.lockdown_protocol_enabled:
        logger.error("lockdown_reconcile_disabled")
        return
    ok, report = await engage()
    logger.warning("lockdown_reconciled", extra={"ok": ok, "report": report})


async def status() -> dict:
    """What the flag says AND what the firewall actually shows.

    Reported separately on purpose: a drift between them (flag engaged, rules
    gone) is the failure worth seeing, and collapsing them into one boolean is
    how it stays invisible.
    """
    engaged = get_lockdown()
    rules: dict[str, bool] = {}
    if settings.lockdown_protocol_enabled:
        for chain, port, label in _sealed():
            rules[label] = await _present(_drop_rule(chain, port, "-C"))
    return {
        "engaged": engaged,
        "enabled": settings.lockdown_protocol_enabled,
        "rules": rules,
    }
