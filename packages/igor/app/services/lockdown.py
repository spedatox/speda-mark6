# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
LOCKDOWN PROTOCOL — automatic inbound containment, and its own way back out.

Engaging drops external traffic to the host's exposed inbound ports; standing
down removes exactly the rules it added. Both run as plain service calls, not as
something an agent has to reason its way through with shell commands — the whole
point is that containment happens the moment the flag flips.

THE ONE RULE THIS MODULE IS BUILT AROUND
----------------------------------------
**A seal and its exemptions must land in a SINGLE host command.**

The bridge this module gives its orders over (services/host_bridge.py) is SSH to
the very port lockdown seals. So the obvious shape — insert the DROP, then insert
the ACCEPT exemptions above it — cannot work: each `run()` is a NEW TCP
connection, and by the time the second one is dialled the port it dials is being
dropped. It times out, and so does the rollback that was supposed to undo it. The
host is left sealed with no exemption for anything — unreachable from the
container, from CI, and from the owner's laptop alike — while `engage()` returns
"the host is NOT sealed", because no individual step ever reported success. Flag
off, host bricked, recovery only through the provider's console.

That was the original implementation's behaviour on every run. Everything below
follows from not doing it again: the rules are built inside a chain that nothing
jumps to yet, and go live in the same command that finishes building them.

HOW IT IS SEALED
----------------
One chain, `SPEDA_LOCKDOWN`, hooked at position 1 of two places:

  * `INPUT` — for host processes. sshd lives here.
  * `DOCKER-USER` — for Docker-published ports. Docker installs its own
    NAT/forward rules that bypass INPUT entirely; DOCKER-USER is the supported
    hook it leaves under our control. A rule in the wrong chain silently does
    nothing, which is precisely the kind of "contained" that isn't.

The chain reads top to bottom: every exemption RETURNs first, then the per-port
DROPs. Building it in that order means a build that dies halfway fails OPEN, not
closed — the failure that locks the owner out of their own server is the one
worth engineering against.

THE EXEMPTION THAT CANNOT BE WRONG
----------------------------------
The first RETURN in the chain is for `$SSH_CLIENT` — the source address the
bridge's own live connection is arriving from, read on the host at the moment the
rules are written. Enumerated Docker subnets are added too, but they are
belt-and-braces: they are a guess about which address the container will appear
as, and this is the one place a wrong guess costs the host. When the bridge is
remote and that address cannot be read, the script exits 9 and NOTHING is hooked.

WHAT STAYS OPEN, AND WHY IT MUST
--------------------------------
Only inbound host SSH (`system_ops_ssh_port`, plus 22 when that differs) and 8000
(the app's raw published port) are dropped. Left alone, deliberately:

  * 443/80 — Caddy, and therefore the app itself. This is the channel the desktop
    client is built against and the one that carries the disengage call. Blocking
    it would make the protocol unliftable from the app.
  * The bridge's own source, every Docker subnet and loopback — which is what
    keeps the host bridge alive while engaged. Orion can still operate the host;
    lockdown can still be lifted from inside.
  * All outbound traffic — the model APIs, Google, Telegram. A contained Speda is
    still a working Speda.

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

STANDING DOWN ALSO REPAIRS THE OLD SCHEME
-----------------------------------------
`disengage()` removes the chain AND any flat per-port DROP left behind by the
pre-chain implementation, because a host carrying those is exactly the host whose
owner is now trying to get back in.
"""

import logging

from app.config import settings
from app.core.runtime_state import get_lockdown, set_lockdown
from app.services.host_bridge import remote_enabled, run

logger = logging.getLogger(__name__)

# The one chain every rule lives in. Nothing else on the host is touched: the
# hooks are single -I jumps, removed by the identical spec with -D.
CHAIN = "SPEDA_LOCKDOWN"

# Always exempt, on top of $SSH_CLIENT and every Docker subnet found at engage
# time.
_LOOPBACK = "127.0.0.0/8"

# Exit code the engage script uses for "I could not identify the bridge's own
# source address, so I refused to hook anything."
_NO_CLIENT = 9


def _sealed() -> tuple[tuple[str, int, str], ...]:
    """Inbound ports sealed while engaged: (primary hook, port, what it is).

    443/80 are absent by design — see the module docstring. The hook named here
    is only which one the port is *reported* against; both hooks jump to the same
    chain, so a DROP added for one is enforced from either. That is deliberate —
    a host process binding 8000 directly is sealed too, and a container exposing
    22 would be as well.

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


def _hooks() -> tuple[str, ...]:
    """The chains that jump into CHAIN, in a stable order with INPUT first."""
    return tuple(dict.fromkeys(hook for hook, _, _ in _sealed()))


def _ports() -> tuple[int, ...]:
    return tuple(dict.fromkeys(port for _, port, _ in _sealed()))


async def _docker_subnets() -> list[str]:
    """Every Docker network subnet, best effort.

    Best effort and no longer a precondition: these are a *guess* at which
    address the app container will appear as on the host, and the engage script
    does not depend on that guess being right — `$SSH_CLIENT` is read on the host
    and exempted first. They are still added because they cost nothing and cover
    the other containers.

    The app container runs on the COMPOSE network (172.18.x here), not docker0's
    172.17.x, so enumerating every network matters; docker0 alone is a floor.
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
        # Docker unreachable — fall back to the kernel's view of docker0. It only
        # covers the default bridge, so it is a floor, not a substitute.
        code, out, _ = await run("ip -4 -o addr show docker0 | awk '{print $4}'", timeout=15)
        subnets = [
            line.strip() for line in out.splitlines()
            if code == 0 and line.strip() and "/" in line
        ]
    # Deduplicate, keep order stable so inserts and deletes pair up predictably.
    return list(dict.fromkeys(subnets))


def _engage_script(subnets: list[str]) -> str:
    """The whole seal, as one shell script for one host command.

    Read it top to bottom — the ordering IS the safety property:

      1. the chain is created or emptied while nothing jumps to it,
      2. the bridge's own source address is exempted, or we abort,
      3. the remaining exemptions RETURN,
      4. the ports DROP,
      5. and only on the last line does INPUT start jumping into any of it.

    Steps 1–4 are inert. A failure anywhere in them leaves a chain nothing
    reaches, which is why `set -e` here means "abort harmlessly" rather than
    "abort halfway through sealing the host".

    Re-engaging flushes a chain that IS live, which opens a window of a few
    milliseconds before step 4 re-seals it. That direction is the acceptable one:
    it can let a packet through, it cannot lock anyone out.
    """
    lines = [
        "set -e",
        f"iptables -N {CHAIN} 2>/dev/null || iptables -F {CHAIN}",
        # The address the bridge is arriving from RIGHT NOW, straight from sshd,
        # rather than a subnet inferred from Docker and hoped to be the one.
        'CLIENT=$(echo "$SSH_CLIENT" | cut -d" " -f1)',
        'if [ -n "$CLIENT" ]; then',
        f'  iptables -A {CHAIN} -s "$CLIENT" -j RETURN',
        f'elif [ "{1 if remote_enabled() else 0}" = "1" ]; then',
        '  echo "refusing to seal: the bridge source address could not be read" >&2',
        f"  exit {_NO_CLIENT}",
        "fi",
    ]
    lines += [f"iptables -A {CHAIN} -s {source} -j RETURN" for source in [*subnets, _LOOPBACK]]
    lines += [f"iptables -A {CHAIN} -p tcp --dport {port} -j DROP" for port in _ports()]
    # Live only now, and only once.
    lines.append(f"iptables -C INPUT -j {CHAIN} 2>/dev/null || iptables -I INPUT 1 -j {CHAIN}")
    return "\n".join(lines)


def _hook_script(hook: str) -> str:
    """Install one more jump into an already-built chain. Safe as its own round
    trip precisely because the chain is complete before this ever runs."""
    return f"iptables -C {hook} -j {CHAIN} 2>/dev/null || iptables -I {hook} 1 -j {CHAIN}"


def _disengage_script(subnets: list[str]) -> str:
    """Unhook, delete the chain, and clear anything the pre-chain scheme left.

    `set +e` throughout: this is the escape hatch, so one failing delete must
    never stop the rest from running. The `|| break` inside each loop is what
    keeps a rule that refuses to delete from spinning forever.
    """
    lines = ["set +e"]
    for hook in _hooks():
        lines.append(
            f"while iptables -C {hook} -j {CHAIN} 2>/dev/null; do "
            f"iptables -D {hook} -j {CHAIN} || break; done"
        )
    lines.append(f"iptables -F {CHAIN} 2>/dev/null")
    lines.append(f"iptables -X {CHAIN} 2>/dev/null")

    # Legacy repair: the flat rules the first implementation inserted straight
    # into INPUT/DOCKER-USER. The bare DROP is the one that locks people out, so
    # it goes unconditionally; the old ACCEPTs are tidied where the subnet is
    # still known, and harmless where it is not (they only ever widened access to
    # a network that no longer exists).
    for hook in _hooks():
        for port in _ports():
            lines.append(
                f"while iptables -C {hook} -p tcp --dport {port} -j DROP 2>/dev/null; do "
                f"iptables -D {hook} -p tcp --dport {port} -j DROP || break; done"
            )
            for source in [*subnets, _LOOPBACK]:
                lines.append(
                    f"while iptables -C {hook} -p tcp --dport {port} -s {source} "
                    f"-j ACCEPT 2>/dev/null; do iptables -D {hook} -p tcp --dport {port} "
                    f"-s {source} -j ACCEPT || break; done"
                )
    lines.append("exit 0")
    return "\n".join(lines)


def _status_script() -> str:
    """One round trip for everything `status()` reports."""
    lines = []
    for hook in _hooks():
        lines.append(
            f'echo "hook {hook}=$(iptables -C {hook} -j {CHAIN} 2>/dev/null && echo 1 || echo 0)"'
        )
    for hook, port, _ in _sealed():
        lines.append(
            f'echo "legacy {hook}:{port}='
            f'$(iptables -C {hook} -p tcp --dport {port} -j DROP 2>/dev/null && echo 1 || echo 0)"'
        )
    lines.append("echo CHAIN_BEGIN")
    lines.append(f"iptables -S {CHAIN} 2>/dev/null")
    lines.append("exit 0")
    return "\n".join(lines)


async def engage() -> tuple[bool, str]:
    """Seal the inbound ports and persist the flag. Returns (ok, report).

    Idempotent: the chain is rebuilt from scratch and each hook is inserted only
    when absent, so repeated engages cannot pile up duplicates that a single
    disengage would fail to clear.
    """
    if not settings.lockdown_protocol_enabled:
        return False, (
            "Lockdown Protocol is disabled on this deployment "
            "(LOCKDOWN_PROTOCOL_ENABLED is off). Nothing was changed."
        )

    subnets = await _docker_subnets()

    # THE seal. One command, one connection — see the module docstring. The
    # generous timeout is because the final line cuts every other source off from
    # this port, and the in-flight connection may need a retransmit or two before
    # the exemption above it takes effect.
    code, _, err = await run(_engage_script(subnets), timeout=60)

    if code == _NO_CLIENT:
        logger.error("lockdown_engage_no_client")
        return False, (
            "REFUSED — the bridge's own source address could not be read on the "
            "host, so the containment rules would have had no exemption for it "
            "and lockdown could not have been lifted from inside. Nothing was "
            "changed."
        )
    if code != 0:
        logger.error("lockdown_engage_failed", extra={"code": code, "error": err[:500]})
        return False, (
            "REFUSED — the containment rules could not be applied, so the host is "
            "NOT sealed and the protocol stays down: "
            + (err.strip() or f"exit {code}")
            + "\n\nNothing went live: the rules are built inside a chain that is "
            "only hooked on the final line, so a failure before that point "
            "changes nothing on the host."
        )

    # The flag follows the firewall, never leads it — but it is set the moment the
    # FIRST hook is real, before the second is attempted. A caller that gives up
    # here (a browser fetch timing out is enough to cancel the request) must not
    # leave the host sealed with the flag reading off: that drift is the worst
    # state this module can produce, because `reconcile_on_startup` skips a clear
    # flag and the UI offers no way down from a lockdown it does not believe is on.
    set_lockdown(True)

    partial = []
    for hook in _hooks():
        if hook == "INPUT":
            continue  # hooked by the seal itself, in the same command
        code, _, err = await run(_hook_script(hook), timeout=25)
        if code != 0:
            partial.append(f"{hook}: {err.strip() or f'exit {code}'}")

    logger.warning("lockdown_engaged", extra={"subnets": subnets, "partial": partial})

    lines = ["LOCKDOWN PROTOCOL ENGAGED — inbound containment active."]
    lines.append("Sealed: " + ", ".join(label for _, _, label in _sealed()))
    if partial:
        lines.append(
            "PARTIAL — these hooks did NOT install, so traffic that reaches those "
            "ports by that path is still allowed: " + "; ".join(partial)
        )
    lines.append(
        "Still open by design: HTTPS 443/80 (the app and this protocol's own "
        "disengage path), the host bridge's own connection, "
        + ", ".join([*subnets, _LOOPBACK])
        + ", and all outbound."
    )
    return True, "\n".join(lines)


async def disengage() -> tuple[bool, str]:
    """Remove the containment rules and clear the flag. Returns (ok, report).

    This is the escape hatch, so it is deliberately forgiving: it clears the flag
    even when a delete fails, and reports what remains. A stuck flag with a clean
    firewall would leave the UI claiming a containment that isn't there.
    """
    subnets = await _docker_subnets()
    code, _, err = await run(_disengage_script(subnets), timeout=60)

    set_lockdown(False)

    if code != 0:
        # The script's last line is `exit 0`, so a non-zero code means the host
        # was never reached — which is the one case where "normal access
        # restored" would be a lie, and the likeliest case of all: a host sealed
        # against its own bridge is exactly the host that cannot be told to open.
        logger.error("lockdown_disengage_unreachable", extra={"code": code, "error": err[:500]})
        return True, (
            "The Lockdown flag is now CLEAR, but the firewall could NOT be "
            "reached, so containment may still be fully in place: "
            + (err.strip() or f"exit {code}")
            + "\n\nDo not tell the owner the server is open. If the host bridge "
            "is itself sealed, nothing on this side can lift it — it has to be "
            "cleared from the provider's console:\n"
            f"  iptables -D INPUT -j {CHAIN}; iptables -D DOCKER-USER -j {CHAIN}\n"
            f"  iptables -F {CHAIN}; iptables -X {CHAIN}"
        )

    # The host answered, so what it reports about its own rules can be trusted.
    still = [label for label, sealed in (await status())["rules"].items() if sealed]
    logger.warning("lockdown_disengaged", extra={"still_sealed": still})

    lines = ["LOCKDOWN PROTOCOL STOOD DOWN — normal access restored."]
    if still:
        lines.append(
            "WARNING — these are STILL sealed on the firewall and the host stays "
            "unreachable on them: " + ", ".join(still) + ". Clear them by hand: "
            f"iptables -D INPUT -j {CHAIN}"
        )
    else:
        lines.append("Reopened: " + ", ".join(label for _, _, label in _sealed()))
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
    how it stays invisible. A port counts as sealed when its hook is installed
    AND the chain carries its DROP — or when a flat rule from the pre-chain
    scheme is still sitting in the hook, which is drift of a different kind and
    just as worth surfacing.

    `rules` comes back EMPTY when the host could not be reached at all. That is
    not the same as "nothing is sealed" and must never be flattened into it: an
    unreachable host is, more often than not, unreachable *because* it is sealed.
    """
    engaged = get_lockdown()
    rules: dict[str, bool] = {}
    if settings.lockdown_protocol_enabled:
        code, out, _ = await run(_status_script(), timeout=30)
        if code == 0:
            head, _, chain = out.partition("CHAIN_BEGIN")
            flags = dict(
                line.strip().split("=", 1) for line in head.splitlines() if "=" in line
            )
            for hook, port, label in _sealed():
                hooked = flags.get(f"hook {hook}") == "1"
                in_chain = f"--dport {port} -j DROP" in chain
                legacy = flags.get(f"legacy {hook}:{port}") == "1"
                rules[label] = (hooked and in_chain) or legacy
    return {
        "engaged": engaged,
        "enabled": settings.lockdown_protocol_enabled,
        "rules": rules,
    }
