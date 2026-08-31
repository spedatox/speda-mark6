# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
DOORMAT PROTOCOL — changing the name on the front door without locking yourself
out of the house.

Moving Mark VI to a new domain is not one change. It is a certificate, a reverse
proxy, four settings inside Igor, a Telegram webhook per bot, three third-party
consoles nobody here can reach, and two clients that still point at the old
address. Done in one step, any of them can fail in a way that takes the app down
with no way back in — because the way back in was the thing that just moved.

THE RULE THIS PROTOCOL IS BUILT AROUND
--------------------------------------
**The old door stays open until the new one is proven.**

Caddy serves as many hostnames as it is given, so there is never a moment where
neither works. That single fact is what turns a risky migration into three
reversible steps:

  1. **stage**   — the new domain is served ALONGSIDE the old one, with a real
                   Let's Encrypt certificate, verified end to end. Nothing has
                   been taken away, and `abort` undoes it completely.
  2. **cutover** — Igor's own settings repoint to the new domain: the Telegram
                   webhook base and the three OAuth redirect URIs. Both hostnames
                   still serve. This is the step that needs the owner to have
                   updated the third-party consoles FIRST.
  3. **retire**  — the old hostname stops being served, the deployment file is
                   rewritten to match, and Caddy is recreated. Deliberate, last,
                   and only once the owner says the new address works everywhere.

Each phase is separately reversible for as long as it matters, and the risky
container recreate happens at the very end, when the new domain has already been
proven and the old one is being abandoned on purpose.

THE PRECONDITION THAT SAVES THE HOST
------------------------------------
Staging refuses unless the new domain's DNS already resolves to this server. A
Caddy site for a hostname that does not point here does not fail quietly: it
enters an ACME retry loop against Let's Encrypt, which has rate limits, and the
certificate the owner is waiting for never arrives. The check is one comparison
between what the domain resolves to and what addresses this host actually holds,
and it is worth more than everything else in this module.

`force` exists for exactly one legitimate case — a proxy in front (Cloudflare and
friends), where the A record correctly points somewhere else. It is not a way to
skip the check because it was inconvenient.

WHAT THIS MODULE CANNOT DO, AND SAYS SO
---------------------------------------
Nothing here can log into Google Cloud Console, Azure, or Notion. Those redirect
URIs are the owner's to change, and if they are not changed before `cutover`,
signing in breaks. So the protocol GENERATES the checklist — from what is
actually configured, with the exact strings to paste — and Orion walks the owner
through it. A checklist that lists integrations the deployment does not use is a
checklist that gets skimmed, so unconfigured providers are simply absent.

The standing instruction in every one of those steps is **add, do not replace**:
keep the old redirect URI alongside the new one until `retire`. Same principle as
the Caddy site, for the same reason.
"""

import asyncio
import logging
import re
import socket

from app.config import read_managed_env, settings, write_managed_env
from app.core.clock import utc_now
from app.core.runtime_state import get_doormat, set_doormat
from app.services.host_bridge import run

logger = logging.getLogger(__name__)

# The site file the protocol owns. It always holds "the OTHER door" — the new
# hostname while staging, nothing once retired — so the deployment's own
# {$DOMAIN} block and this file can never name the same host. Caddy refuses a
# Caddyfile with a duplicate site address, and a refused reload is a reload that
# silently did not happen.
SITE_FILE = "doormat.caddy"

# Where the site blocks are mounted inside the Caddy container (docker-compose.yml).
_SITES_MOUNT = "/etc/caddy/sites"

IDLE, STAGED, CUTOVER = "", "staged", "cutover"

# RFC 1123 hostname, lowercase, at least two labels. This is also the injection
# guard: the value is interpolated into shell commands and a Caddyfile, so
# nothing that fails this pattern is ever allowed near either.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)

# The Igor settings whose value is literally the domain. Repointed at cutover;
# every one of them is `requires_restart` in config_schema.py, which is why
# cutover ends by telling Orion to restart and `status()` reports the drift
# until it happens.
def _derived(domain: str) -> dict[str, str]:
    base = f"https://{domain}"
    return {
        "telegram_webhook_base": base,
        "google_oauth_redirect": f"{base}/oauth/google/callback",
        "microsoft_oauth_redirect": f"{base}/oauth/microsoft/callback",
        "notion_oauth_redirect": f"{base}/oauth/notion/callback",
    }


def _now() -> str:
    return utc_now().isoformat(timespec="seconds")


def restart_pending() -> list[str]:
    """Derived keys the managed file has moved but this PROCESS has not picked up.

    The managed override file is written at cutover; the `settings` object was
    built at boot. Comparing the two is what makes a skipped restart visible, and
    it works on any deployment — a check against one integration's value would
    read "pending" forever on a host that does not use that integration.
    """
    stored = read_managed_env()
    drifted = []
    for key in _derived("x"):
        want = stored.get(key.upper())
        if want is not None and str(getattr(settings, key, "")) != want:
            drifted.append(key)
    return drifted


def normalize(domain: str) -> str:
    """Trim what people actually type: a scheme, a trailing slash, a stray port,
    capitals. Returns "" when nothing usable is left."""
    text = (domain or "").strip().lower()
    text = re.sub(r"^[a-z]+://", "", text)
    text = text.split("/", 1)[0].split(":", 1)[0].strip(".")
    return text


def valid(domain: str) -> bool:
    return bool(_DOMAIN_RE.match(domain or ""))


# ── Finding the host's own layout ────────────────────────────────────────────

async def _layout() -> dict:
    """Where Caddy's site blocks live on the host, and what it is serving today.

    Discovered from Docker rather than configured, because the answer already
    exists and a second copy of it in settings is a second thing to be wrong. The
    site directory is a bind mount, so its source IS the repo checkout's
    caddy-sites/, and the repo root is its parent.
    """
    script = "\n".join([
        "CID=$(docker ps -q -f label=com.docker.compose.service=caddy | head -1)",
        'if [ -z "$CID" ]; then echo "no_caddy"; exit 0; fi',
        'docker inspect "$CID" --format '
        "'{{range .Mounts}}mount={{.Source}}|{{.Destination}}{{\"\\n\"}}{{end}}"
        "{{range .Config.Env}}env={{.}}{{\"\\n\"}}{{end}}'",
        'echo "cid=$CID"',
        "exit 0",
    ])
    code, out, err = await run(script, timeout=30)
    if code != 0:
        return {"error": (err.strip() or f"exit {code}")[:300]}
    if "no_caddy" in out:
        return {"error": (
            "Caddy is not running, so there is no domain to change. The Doormat "
            "Protocol moves an EXISTING domain; setting one up for the first time "
            "is a deploy concern (DOMAIN in packages/igor/.env, then ./deploy.sh)."
        )}

    sites_dir = ""
    current = ""
    cid = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("mount=") and "|" in line:
            source, _, dest = line[len("mount="):].partition("|")
            if dest.strip() == _SITES_MOUNT:
                sites_dir = source.strip()
        elif line.startswith("env=DOMAIN="):
            current = normalize(line[len("env=DOMAIN="):])
        elif line.startswith("cid="):
            cid = line[len("cid="):].strip()

    if not sites_dir:
        return {"error": (
            f"Caddy is running but has no bind mount at {_SITES_MOUNT}, so there is "
            "nowhere to write a site block. This deployment's compose file does not "
            "match the repo's."
        )}
    repo = sites_dir.rsplit("/", 1)[0] if "/" in sites_dir else sites_dir
    return {
        "sites_dir": sites_dir,
        "site_path": f"{sites_dir}/{SITE_FILE}",
        "repo": repo,
        "env_file": f"{repo}/packages/igor/.env",
        "current_domain": current,
        "container": cid,
    }


async def _host_addresses() -> set[str]:
    """Every globally-scoped IPv4/IPv6 address this host holds."""
    code, out, _ = await run(
        "ip -o addr show scope global | awk '{print $4}' | cut -d/ -f1", timeout=20
    )
    return {line.strip() for line in out.splitlines() if code == 0 and line.strip()}


async def _resolve(domain: str) -> set[str]:
    """What the domain resolves to right now, from this process's resolver."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
    except OSError:
        return set()
    return {info[4][0] for info in infos}


async def dns_check(domain: str) -> dict:
    """Does `domain` point at this server? The one check worth having."""
    resolved = await _resolve(domain)
    host = await _host_addresses()
    return {
        "resolved": sorted(resolved),
        "host": sorted(host),
        "points_here": bool(resolved & host),
        "resolves": bool(resolved),
    }


# ── The third-party half ─────────────────────────────────────────────────────

def checklist(domain: str, previous: str) -> list[dict]:
    """The console steps nobody on this side can perform, for THIS deployment.

    Generated from what is actually configured. An unused provider is absent
    rather than listed as "skip if you don't use it" — a checklist padded with
    steps that do not apply is a checklist that gets skimmed, and the one that
    mattered was in the middle of it.
    """
    items: list[dict] = []

    def add(key: str, where: str, field: str, value: str, note: str = "") -> None:
        items.append({"provider": key, "where": where, "field": field,
                      "value": value, "note": note})

    if settings.google_client_id:
        add("Google",
            "console.cloud.google.com → APIs & Services → Credentials → your OAuth 2.0 Client",
            "Authorised redirect URIs",
            f"https://{domain}/oauth/google/callback",
            "ADD it; leave the old URI in place until the domain is retired.")
    if settings.microsoft_client_id:
        add("Microsoft",
            "portal.azure.com → App registrations → your app → Authentication → Web",
            "Redirect URIs",
            f"https://{domain}/oauth/microsoft/callback",
            "ADD it. Azure matches the redirect exactly — a trailing slash is a "
            "different URI and fails with AADSTS50011.")
    if settings.notion_client_id:
        add("Notion",
            "notion.so/my-integrations → your integration → Authorization",
            "Redirect URIs",
            f"https://{domain}/oauth/notion/callback",
            "ADD it; remove the old one only after retire.")

    if settings.telegram_mode.strip().lower() == "webhook":
        items.append({
            "provider": "Telegram", "where": "nothing to do — automatic",
            "field": "webhook URL",
            "value": f"https://{domain}/telegram/webhook/{{agent_id}}",
            "note": "Re-registered for every bot when Igor restarts after cutover. "
                    "Telegram has no console step.",
        })

    items.append({
        "provider": "Desktop app (Heartbreaker)",
        "where": "the app → connection settings",
        "field": "API base", "value": f"https://{domain}",
        "note": "No rebuild needed — the address is editable in the app. Do this "
                "AFTER cutover; the old address keeps working until retire either way.",
    })
    items.append({
        "provider": "Speda GO", "where": "the phone app's connection settings",
        "field": "API base", "value": f"https://{domain}", "note": "",
    })

    # The n8n editor is its own hostname. It only moves if it was a child of the
    # domain being retired — and then it needs its own DNS record and a deploy,
    # which is outside this protocol.
    n8n_domain = normalize(read_managed_env().get("N8N_DOMAIN", ""))
    if previous and n8n_domain and n8n_domain.endswith("." + previous):
        items.append({
            "provider": "n8n editor", "where": "DNS, then packages/igor/.env → N8N_DOMAIN, then ./deploy.sh",
            "field": "N8N_DOMAIN", "value": f"(a subdomain of {domain})",
            "note": f"WARNING: {n8n_domain} is a subdomain of the domain being "
                    "retired, so it dies with it. This protocol does not move it — "
                    "it needs its own A record and a deploy.",
        })

    return items


# ── Phase 1: stage ───────────────────────────────────────────────────────────

async def stage(domain: str, *, force: bool = False) -> tuple[bool, str]:
    """Serve `domain` alongside the current one, with a real certificate.

    Nothing is taken away and nothing repoints. On any failure the site file is
    removed and Caddy reloaded back, so a failed stage leaves the host exactly as
    it was.
    """
    if not settings.doormat_protocol_enabled:
        return False, (
            "The Doormat Protocol is disabled on this deployment "
            "(DOORMAT_PROTOCOL_ENABLED is off). Nothing was changed."
        )

    target = normalize(domain)
    if not valid(target):
        return False, (
            f"REFUSED — '{domain}' is not a usable hostname, and nothing was "
            "changed. Give a bare domain like speda.example.com: lowercase, at "
            "least two labels, no scheme, no path, no port."
        )

    layout = await _layout()
    if "error" in layout:
        return False, f"REFUSED — nothing was changed: {layout['error']}"

    current = layout["current_domain"]
    if target == current:
        return False, (
            f"Nothing to do: the server is already serving {target}. If the "
            "Doormat Protocol is mid-flight, `status` will say so."
        )

    dns = await dns_check(target)
    if not dns["points_here"] and not force:
        if not dns["resolves"]:
            detail = f"{target} does not resolve at all yet."
        else:
            detail = (f"{target} resolves to {', '.join(dns['resolved'])}, but this "
                      f"host holds {', '.join(dns['host']) or '(none readable)'}.")
        return False, (
            f"REFUSED — nothing was changed. {detail}\n\n"
            "Point the DNS record at this server first. A Caddy site for a "
            "hostname that does not resolve here does not fail quietly: it "
            "retries against Let's Encrypt, which rate-limits, and the "
            "certificate never arrives.\n\n"
            f"Create an A record:  {target}  →  "
            + (", ".join(a for a in dns["host"] if ":" not in a) or "this server")
            + "\n"
            "DNS can take minutes to propagate; try again once it has.\n\n"
            "If a proxy sits in front (Cloudflare and the like), the record "
            "correctly points elsewhere — that is the one case for force=true."
        )

    site = f"{target} {{\n\treverse_proxy app:8000\n}}\n"
    write = "\n".join([
        f"cat > {layout['site_path']} <<'DOORMAT_EOF'",
        site.rstrip("\n"),
        "DOORMAT_EOF",
        "exit 0",
    ])
    code, _, err = await run(write, timeout=30)
    if code != 0:
        return False, (
            "REFUSED — the site block could not be written and nothing changed: "
            + (err.strip() or f"exit {code}")
        )

    ok, detail = await _reload(layout)
    if not ok:
        await run(f"rm -f {layout['site_path']}", timeout=20)
        await _reload(layout)
        return False, (
            "REFUSED — Caddy rejected the new configuration, so the site file was "
            f"removed and the old one is still live and untouched: {detail}"
        )

    served, verify_detail = await _verify(target)
    if not served:
        await run(f"rm -f {layout['site_path']}", timeout=20)
        await _reload(layout)
        return False, (
            f"ROLLED BACK — {target} was added but never served a valid "
            f"certificate, so it has been removed again and {current} is "
            f"untouched: {verify_detail}\n\n"
            "The usual cause is DNS that resolves here but port 80 not reaching "
            "this host, which is what Let's Encrypt needs for the HTTP-01 "
            "challenge."
        )

    set_doormat({
        "phase": STAGED, "target": target, "previous": current,
        "staged_at": _now(), "cutover_at": "",
    })
    logger.warning("doormat_staged", extra={"target": target, "previous": current})

    steps = checklist(target, current)
    lines = [
        f"DOORMAT STAGED — {target} is live and holds a valid certificate.",
        f"{current} is untouched and still serving; nothing has repointed yet.",
        "",
        "NEXT, AND THE OWNER HAS TO DO IT: the third-party consoles below. Every "
        "one is an ADD, never a replace — the old URI must keep working until the "
        "old domain is retired.",
        "",
    ]
    lines += _render(steps)
    lines += [
        "",
        "Once they confirm those are done, run cutover. Do NOT run cutover before "
        "that: cutover repoints Igor's redirect URIs, and if the consoles do not "
        "know the new one yet, signing in breaks with no clue why.",
    ]
    return True, "\n".join(lines)


def _render(steps: list[dict]) -> list[str]:
    out = []
    for i, step in enumerate(steps, 1):
        out.append(f"{i}. {step['provider']} — {step['where']}")
        out.append(f"   {step['field']}: {step['value']}")
        if step["note"]:
            out.append(f"   ({step['note']})")
    return out


async def _reload(layout: dict) -> tuple[bool, str]:
    """Reload Caddy in place. A rejected config leaves the RUNNING one alone,
    which is why a failure here is safe and a failure after it is not."""
    code, _, err = await run(
        f"docker exec {layout['container']} caddy reload "
        "--config /etc/caddy/Caddyfile --adapter caddyfile",
        timeout=60,
    )
    return code == 0, (err.strip() or f"exit {code}")[:600]


async def _verify(domain: str) -> tuple[bool, str]:
    """Does Caddy serve this hostname over real TLS, all the way to /health?

    `--resolve … 127.0.0.1` sends the request to Caddy on this box with the right
    SNI, so the answer does not depend on hairpin NAT working. No `-k`: an
    unverifiable certificate is the failure being tested for, not noise to
    suppress. Certificates take a few seconds to issue, hence the loop — and it
    is one host round trip, not twenty.
    """
    script = "\n".join([
        # Without this the missing-tool case reports as "no certificate", which
        # sends whoever reads it to look at ACME logs that are perfectly fine.
        'command -v curl >/dev/null 2>&1 || { echo "no_curl"; exit 0; }',
        "code=000",
        "for i in $(seq 1 20); do",
        f"  code=$(curl -sS -o /dev/null -w '%{{http_code}}' --max-time 8 "
        f"--resolve {domain}:443:127.0.0.1 https://{domain}/health 2>/dev/null || echo 000)",
        '  if [ "$code" = "200" ]; then break; fi',
        "  sleep 3",
        "done",
        'echo "http=$code"',
        "exit 0",
    ])
    code, out, err = await run(script, timeout=120)
    if code != 0:
        return False, (err.strip() or f"exit {code}")[:300]
    if "no_curl" in out:
        return False, (
            "the host has no curl, so nothing here can prove the domain serves. "
            "Install it (apt-get install -y curl) — this protocol will not move a "
            "domain it cannot verify"
        )
    status = out.strip().rpartition("http=")[2].strip()
    if status == "200":
        return True, "200"
    if status == "000":
        return False, ("no HTTPS response — Caddy has no valid certificate for it "
                       "yet (the ACME challenge has not completed or failed)")
    return False, f"served, but /health answered {status} instead of 200"


# ── Phase 2: cutover ─────────────────────────────────────────────────────────

async def cutover() -> tuple[bool, str]:
    """Repoint Igor's own settings to the staged domain. Both hostnames still serve.

    Writes the managed override file rather than live-mutating settings: every
    one of these fields is `requires_restart` in config_schema.py, and having a
    second module decide otherwise is how the schema stops describing reality.
    The restart is the caller's last step, and `status()` reports the gap until
    it happens.
    """
    if not settings.doormat_protocol_enabled:
        return False, "The Doormat Protocol is disabled on this deployment."

    state = get_doormat()
    if state.get("phase") != STAGED:
        return False, (
            "REFUSED — nothing was changed: there is no staged domain to cut over "
            "to. Run stage first; it is the step that proves the new hostname "
            "actually serves before anything depends on it."
        )

    target = state["target"]
    served, detail = await _verify(target)
    if not served:
        return False, (
            f"REFUSED — nothing was changed. {target} was staged but is not "
            f"serving right now: {detail}. Cutting over to a hostname that does "
            "not answer would repoint every redirect URI at a dead address."
        )

    updates = _derived(target)
    write_managed_env({key.upper(): value for key, value in updates.items()})
    set_doormat({**state, "phase": CUTOVER, "cutover_at": _now()})
    logger.warning("doormat_cutover", extra={"target": target, "keys": sorted(updates)})

    lines = [
        f"DOORMAT CUTOVER WRITTEN — Igor's settings now name {target}:",
        "",
    ]
    lines += [f"  {key} = {value}" for key, value in sorted(updates.items())]
    lines += [
        "",
        f"{state.get('previous') or 'the old domain'} is STILL SERVING — nothing "
        "has been taken away, so a mistake here is recoverable.",
        "",
        "These settings are read at startup, so THIS PROCESS IS STILL RUNNING ON "
        "THE OLD DOMAIN. One thing left, and it is the only thing left:",
        "",
        '    system_ops(action="restart_service", service="app")',
        "",
        "That is a self-restart: it schedules detached and fires after your reply. "
        "Write your report to the owner in the same message, then stop. Telegram "
        "webhooks re-register for every bot on boot, from the new base — there is "
        "no console step for those.",
        "",
        "Retire the old domain only once the owner confirms the new address works "
        "everywhere they use it.",
    ]
    return True, "\n".join(lines)


# ── Phase 3: retire ──────────────────────────────────────────────────────────

async def retire() -> tuple[bool, str]:
    """Stop serving the old hostname and make the deployment file say so.

    The only irreversible-feeling step, and the only one that recreates a
    container — which is why it is last, long after the new domain was proven.
    Caddy is down for a few seconds while it recreates.
    """
    if not settings.doormat_protocol_enabled:
        return False, "The Doormat Protocol is disabled on this deployment."

    state = get_doormat()
    if state.get("phase") != CUTOVER:
        return False, (
            "REFUSED — nothing was changed. Retire removes the OLD domain, which "
            "is only safe once cutover has moved everything off it. Current "
            f"phase: {state.get('phase') or 'idle'}."
        )

    target = state["target"]
    previous = state.get("previous", "")

    stale = restart_pending()
    if stale:
        return False, (
            "REFUSED — nothing was changed. The cutover settings were written but "
            "Igor has not restarted, so this process is STILL running on the old "
            f"domain for: {', '.join(stale)}. Retiring the old hostname now would "
            "break every one of them with nothing to say why.\n\n"
            '    system_ops(action="restart_service", service="app")\n\n'
            "then retire on a later turn."
        )

    served, detail = await _verify(target)
    if not served:
        return False, (
            f"REFUSED — nothing was changed: {target} is not serving right now "
            f"({detail}). The old domain stays up until the new one is provably "
            "working."
        )

    layout = await _layout()
    if "error" in layout:
        return False, f"REFUSED — nothing was changed: {layout['error']}"

    # Order matters and this is one command: the site file must be gone BEFORE
    # {$DOMAIN} becomes the same hostname, or Caddy sees a duplicate site address
    # and refuses the whole config — which on a recreate is not a safe no-op, it
    # is a Caddy that will not come up.
    script = "\n".join([
        "set -e",
        f"rm -f {layout['site_path']}",
        f"cd {layout['repo']}",
        f"if grep -qE '^DOMAIN=' {layout['env_file']}; then",
        f"  sed -i 's|^DOMAIN=.*|DOMAIN={target}|' {layout['env_file']}",
        "else",
        f"  printf '\\nDOMAIN=%s\\n' '{target}' >> {layout['env_file']}",
        "fi",
        f"DOMAIN={target} docker compose --profile domain up -d caddy",
        "exit 0",
    ])
    code, _, err = await run(script, timeout=180)
    if code != 0:
        return False, (
            "The retire step FAILED partway: "
            + (err.strip() or f"exit {code}")
            + f"\n\nCheck what Caddy is serving before telling the owner anything. "
            f"The site file may be gone while {target} is not yet the deployment's "
            f"DOMAIN, which would leave neither hostname served. Recover with: "
            f"cd {layout['repo']} && DOMAIN={target} docker compose --profile "
            "domain up -d caddy"
        )

    served, detail = await _verify(target)
    set_doormat({"phase": IDLE, "target": "", "previous": previous,
                 "retired_at": _now(), "staged_at": "", "cutover_at": ""})
    logger.warning("doormat_retired", extra={"target": target, "previous": previous})

    lines = [
        f"DOORMAT COMPLETE — the server answers to {target} and nothing else.",
        f"{previous or 'The old domain'} is no longer served; its certificate will "
        "not renew, which is correct.",
        "",
        f"Verified: https://{target}/health → {detail}" if served else
        f"WARNING — {target} did not verify after the recreate ({detail}). Check "
        f"`docker logs` on Caddy before telling the owner it is done.",
        "",
        "Remind the owner to remove the OLD redirect URIs from Google, Microsoft "
        "and Notion now — they were kept deliberately until this point, and a "
        "stale redirect URI on an expired domain is somebody else's login button "
        "once that domain is registered by another person.",
    ]
    return True, "\n".join(lines)


async def abort() -> tuple[bool, str]:
    """Undo a stage. Removes the added site and forgets the protocol state.

    Refuses after cutover, because by then the settings have moved and dropping
    the site file alone would leave Igor naming a hostname nothing serves.
    """
    state = get_doormat()
    phase = state.get("phase") or IDLE
    if phase == IDLE:
        return False, "Nothing to abort — the Doormat Protocol is not running."
    if phase == CUTOVER:
        return False, (
            "REFUSED — nothing was changed. Cutover has already repointed Igor's "
            "settings, so removing the new site now would leave every redirect URI "
            "and the Telegram webhook base naming a hostname nothing serves. To go "
            "back, stage the ORIGINAL domain "
            f"({state.get('previous') or 'the old one'}) and cut over to it."
        )

    layout = await _layout()
    if "error" in layout:
        return False, f"Could not reach Caddy, so nothing was changed: {layout['error']}"

    await run(f"rm -f {layout['site_path']}", timeout=20)
    ok, detail = await _reload(layout)
    set_doormat({"phase": IDLE, "target": "", "previous": "",
                 "staged_at": "", "cutover_at": ""})
    logger.warning("doormat_aborted", extra={"target": state.get("target")})

    return True, (
        f"DOORMAT ABORTED — {state.get('target')} is no longer served and the "
        f"protocol is idle. {layout['current_domain'] or 'The original domain'} "
        "was never touched."
        + ("" if ok else f"\n\nWARNING — Caddy did not reload cleanly: {detail}")
    )


async def status() -> dict:
    """Where the protocol is, and what the host actually shows.

    Reported separately for the same reason the Lockdown Protocol does it: a
    drift between the stored phase and the live configuration is the failure
    worth seeing, and collapsing them into one field is how it stays invisible.
    """
    state = get_doormat()
    phase = state.get("phase") or IDLE
    target = state.get("target", "")

    out = {
        "enabled": settings.doormat_protocol_enabled,
        "phase": phase,
        "target": target,
        "previous": state.get("previous", ""),
        "staged_at": state.get("staged_at", ""),
        "cutover_at": state.get("cutover_at", ""),
        "current_domain": "",
        "target_serving": None,
        "restart_pending": False,
        "checklist": [],
        "detail": "",
    }
    if not settings.doormat_protocol_enabled:
        out["detail"] = "DOORMAT_PROTOCOL_ENABLED is off on this deployment."
        return out

    layout = await _layout()
    if "error" in layout:
        out["detail"] = layout["error"]
        return out
    out["current_domain"] = layout["current_domain"]

    if phase != IDLE and target:
        served, detail = await _verify(target)
        out["target_serving"] = served
        out["detail"] = detail
        out["checklist"] = checklist(target, state.get("previous", ""))
        # The running process is the authority on whether the restart happened:
        # the managed file says the new domain, but this object was built at boot.
        out["restart_pending"] = bool(phase == CUTOVER and restart_pending())
    return out
