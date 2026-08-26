"""
Runtime-mutable application state that persists across restarts.

Unlike app.config.settings (read once from env at startup), these values can be
flipped at runtime — by the UI, an API call, or Speda itself via a tool — and are
written to a small JSON file so they survive a restart.

Currently holds the budget-mode flag. Add more runtime toggles here as needed.
"""

import json
import logging
from pathlib import Path

from app.config import _DATA_DIR, settings

logger = logging.getLogger(__name__)

_STATE_FILE = _DATA_DIR / "runtime_state.json"
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_STATE_FILE.read_text(encoding="utf-8")) if _STATE_FILE.exists() else {}
        except Exception as e:
            logger.warning("runtime_state_load_failed", extra={"error": str(e)})
            _cache = {}
    return _cache


def _save() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(_cache or {}, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("runtime_state_save_failed", extra={"error": str(e)})


def get_budget_mode() -> bool:
    """Current budget-mode state. Falls back to the config default if never set."""
    return bool(_load().get("budget_mode", settings.budget_mode))


def set_budget_mode(value: bool) -> bool:
    """Set budget mode and persist. Returns the new value."""
    state = _load()
    state["budget_mode"] = bool(value)
    _save()
    logger.info("budget_mode_set", extra={"budget_mode": bool(value)})
    return bool(value)


# ── House Party Protocol ────────────────────────────────────────────────────
# When engaged, inter-agent dispatch runs every agent at full interactive model
# grade (instead of the background tier) and broadcast dispatch ("all") becomes
# available. Toggled by the owner from the comms tray or by an agent via the
# house_party tool. Default off — it burns Sonnet across the whole roster.

def get_house_party() -> bool:
    return bool(_load().get("house_party", False))


def set_house_party(value: bool) -> bool:
    state = _load()
    state["house_party"] = bool(value)
    _save()
    logger.info("house_party_set", extra={"engaged": bool(value)})
    return bool(value)


# ── Lockdown Protocol ───────────────────────────────────────────────────────
# When engaged, the host's exposed inbound ports (SSH, the app's raw port) are
# sealed by firewall rules — see app/services/lockdown.py, which owns the actual
# containment and is the only thing that should move this flag. Persisted so a
# restart mid-incident re-applies the rules rather than silently reopening the
# ports while every client still shows LOCKDOWN ACTIVE.

def get_lockdown() -> bool:
    return bool(_load().get("lockdown", False))


def set_lockdown(value: bool) -> bool:
    state = _load()
    state["lockdown"] = bool(value)
    _save()
    logger.warning("lockdown_set", extra={"engaged": bool(value)})
    return bool(value)


# ── Lifeboat Protocol ───────────────────────────────────────────────────────
# What the owner has last been TOLD about host resource pressure — not what the
# host currently is. services/lifeboat.py owns the reading; this is only the
# memory of the last report, which is what turns a poll into an edge.
#
# {level, reported_at, pending}. Two slots, like the web watches and for the
# same reason: a scan parks an escalation as `pending` and n8n promotes it via
# /host/lifeboat/ack only after the trigger was accepted. Committing at scan
# time would swallow the escalation whenever the notify call failed, and a
# disk filling up is precisely the thing that must not be reported once into a
# dropped connection and then never again.

def get_lifeboat() -> dict:
    """Last reported pressure level and any parked-but-unacknowledged one."""
    return dict(_load().get("lifeboat", {}))


def set_lifeboat(state: dict) -> dict:
    store = _load()
    store["lifeboat"] = dict(state)
    _save()
    return dict(state)


# ── Doormat Protocol ────────────────────────────────────────────────────────
# Where a domain change has got to: {phase, target, previous, staged_at,
# cutover_at}. services/doormat.py owns every transition.
#
# Persisted because the protocol spans DAYS, not one turn: the owner stages a
# domain, goes and edits three third-party consoles, and comes back tomorrow to
# cut over. A phase held in memory would be lost to the very restart that
# cutover itself requires, and the protocol would forget that an old domain is
# still being served and still owes a retirement.

def get_doormat() -> dict:
    return dict(_load().get("doormat", {}))


def set_doormat(state: dict) -> dict:
    store = _load()
    store["doormat"] = dict(state)
    _save()
    logger.info("doormat_state_set", extra={"phase": state.get("phase") or "idle",
                                            "target": state.get("target", "")})
    return dict(state)


# ── Skyfall Protocol projects ───────────────────────────────────────────────
# One entry per launch target the owner has configured: name, description, the
# endpoint it hits (url/method/body/headers) and how long its countdown runs.
# services/skyfall.py owns the shape; this is only where they live.
#
# Header VALUES are secrets and are treated like portal passwords — stored here,
# masked by skyfall.mask() on every read that leaves the process, and never put
# anywhere a model can read them. A project's `Authorization: Bearer …` must not
# come back out in a chat message.
#
# ONLY the owner writes these, through the settings pane. No tool creates or
# edits a project: an agent that could write the target AND pull the trigger
# could hit anything, and the countdown would be guarding a URL the owner never
# chose.

def get_skyfall_projects() -> dict[str, dict]:
    return dict(_load().get("skyfall_projects", {}))


def get_skyfall_project(project_id: str) -> dict:
    return dict(_load().get("skyfall_projects", {}).get(project_id, {}))


def save_skyfall_project(project_id: str, record: dict) -> dict:
    store = _load()
    projects = dict(store.get("skyfall_projects", {}))
    projects[project_id] = dict(record)
    store["skyfall_projects"] = projects
    _save()
    return dict(record)


def delete_skyfall_project(project_id: str) -> bool:
    store = _load()
    projects = dict(store.get("skyfall_projects", {}))
    existed = projects.pop(project_id, None) is not None
    store["skyfall_projects"] = projects
    _save()
    logger.info("skyfall_project_deleted",
                extra={"project": project_id, "existed": existed})
    return existed


# ── Per-agent model overrides ───────────────────────────────────────────────
# The owner can pin any agent to a specific model ref ("provider:model", bare =
# Anthropic) from the UI. An override replaces the profile's interactive AND
# agent-dispatch allocation for that agent (checked first in
# AgentProfile.allocate_model); tiny background tasks keep their cheap tier.
# Empty/absent = the profile's own policy.

def get_agent_models() -> dict[str, str]:
    return dict(_load().get("agent_models", {}))


def get_agent_sources() -> dict[str, str]:
    """Per-agent 'source of truth' memory file: agent_id → /memories/…md. The
    file is preloaded into that agent's system prompt every turn (read) and the
    agent is told to write all of its domain data there (write). Set from the
    desktop Configuration tab. Empty = fall back to the built-in default for that
    agent (see app/skills/memory.AGENT_SOURCE_DEFAULTS)."""
    return dict(_load().get("agent_sources", {}))


def set_agent_source(agent_id: str, path: str | None) -> None:
    state = _load()
    sources = dict(state.get("agent_sources", {}))
    if path:
        sources[agent_id] = path
    else:
        sources.pop(agent_id, None)
    state["agent_sources"] = sources
    _save()
    logger.info("agent_source_set", extra={"agent_id": agent_id, "path": path or "(default)"})


def set_agent_model(agent_id: str, model: str | None) -> None:
    state = _load()
    models = dict(state.get("agent_models", {}))
    if model:
        models[agent_id] = model
    else:
        models.pop(agent_id, None)
    state["agent_models"] = models
    _save()
    logger.info("agent_model_set", extra={"agent_id": agent_id, "model": model or "(default)"})


# ── Per-legionnaire model overrides ─────────────────────────────────────────
# The owner can pin any Legion worker type (scout, researcher, analyst, judge,
# general) to a specific model ref. An empty/absent entry leaves that worker on
# its effort-derived allocation (see legion/roster.resolve_worker_model), which
# stays provider-agnostic. The deployment-wide LEGION_MODEL_OVERRIDE still wins
# over everything — it exists to pin the whole corps during an incident.

def get_legion_models() -> dict[str, str]:
    """worker_id → pinned model ref. Empty = every worker on effort policy."""
    return dict(_load().get("legion_models", {}))


def set_legion_model(worker_id: str, model: str | None) -> None:
    state = _load()
    models = dict(state.get("legion_models", {}))
    if model:
        models[worker_id] = model
    else:
        models.pop(worker_id, None)
    state["legion_models"] = models
    _save()
    logger.info(
        "legion_model_set",
        extra={"worker_id": worker_id, "model": model or "(default)"},
    )


def get_telegram_models() -> dict[str, str]:
    """Per-agent model override for Telegram channel only."""
    return dict(_load().get("telegram_models", {}))


def set_telegram_model(agent_id: str, model: str | None) -> None:
    state = _load()
    models = dict(state.get("telegram_models", {}))
    if model:
        models[agent_id] = model
    else:
        models.pop(agent_id, None)
    state["telegram_models"] = models
    _save()
    logger.info("telegram_model_set", extra={"agent_id": agent_id, "model": model or "(default)"})


# ── MCP connection toggles ──────────────────────────────────────────────────
# Servers all connect at startup (per MCP_ENABLED), but their tools are only
# shown to Claude if the server is "active". Toggling here hides/shows a server's
# tools live — no restart — which directly shrinks/grows the cached prompt prefix
# (and thus the cold-write that the ITPM limit cares about).

def get_disabled_servers() -> set[str]:
    return set(_load().get("disabled_servers", []))


def get_telegram_chat_id() -> str:
    """Chat id captured via the in-app 'Connect Telegram' flow. Falls back to the
    .env value if the owner hasn't connected through the UI yet."""
    return _load().get("telegram_chat_id", settings.telegram_chat_id)


def set_telegram_chat_id(chat_id: str) -> None:
    state = _load()
    state["telegram_chat_id"] = str(chat_id)
    _save()
    logger.info("telegram_chat_id_saved")


# ── Telegram multi-bot channel state ─────────────────────────────────────────
# The owner's Telegram user id is the SAME number in every bot's private chat,
# so it is captured once and shared across the fleet. `telegram_started` records
# which bots the owner has tapped Start on (Telegram forbids a bot from messaging
# a user who never started it — an unstarted bot falls back to Speda's). Update
# watermarks dedupe webhook retries and polling overlap per bot.

def get_telegram_owner_id() -> str:
    """The owner's Telegram user id — the only sender the gateway will process.
    Falls back to the legacy single-chat id captured by the old connect flow."""
    return _load().get("telegram_owner_id", "") or get_telegram_chat_id()


def set_telegram_owner_id(owner_id: str) -> None:
    state = _load()
    state["telegram_owner_id"] = str(owner_id)
    # Keep the legacy key in lockstep so the old connect status/endpoints and the
    # single-bot send path stay consistent (private-chat id == user id).
    state["telegram_chat_id"] = str(owner_id)
    _save()
    logger.info("telegram_owner_id_saved")


def get_telegram_started() -> set[str]:
    """agent_ids whose bot the owner has started (tapped /start on)."""
    return set(_load().get("telegram_started", []))


def mark_telegram_started(agent_id: str) -> None:
    state = _load()
    started = set(state.get("telegram_started", []))
    started.add(agent_id)
    state["telegram_started"] = sorted(started)
    _save()
    logger.info("telegram_bot_started", extra={"agent_id": agent_id})


def get_telegram_update_offset(agent_id: str) -> int:
    """Last processed getUpdates offset / webhook update_id watermark for a bot."""
    return int(_load().get("telegram_offsets", {}).get(agent_id, 0))


def set_telegram_update_offset(agent_id: str, offset: int) -> None:
    state = _load()
    offsets = dict(state.get("telegram_offsets", {}))
    # Monotonic — never move the watermark backwards (out-of-order retries).
    if offset > offsets.get(agent_id, 0):
        offsets[agent_id] = offset
        state["telegram_offsets"] = offsets
        _save()


def get_google_refresh_token() -> str:
    """Refresh token captured via the in-app 'Sign in with Google' flow.
    Falls back to the .env value if the user hasn't signed in through the UI."""
    return _load().get("google_refresh_token", settings.google_refresh_token)


def set_google_refresh_token(token: str) -> None:
    state = _load()
    state["google_refresh_token"] = token
    _save()
    logger.info("google_refresh_token_saved")


def get_microsoft_refresh_token() -> str:
    """Refresh token captured via the in-app 'Connect Microsoft 365' flow.
    Falls back to the .env value if the owner hasn't signed in through the UI."""
    return _load().get("microsoft_refresh_token", settings.microsoft_refresh_token)


def set_microsoft_refresh_token(token: str) -> None:
    """Persist the Microsoft refresh token.

    Written on sign-in AND on every rotation — Microsoft hands back a new refresh
    token with most refreshes, and keeping the original would strand the
    connection the day the issued chain moves past it.
    """
    state = _load()
    state["microsoft_refresh_token"] = token
    _save()
    logger.info("microsoft_refresh_token_saved")


def get_notion_access_token() -> str:
    """OAuth access token captured via the in-app Notion connection flow.
    Returns empty string if the user hasn't completed OAuth — does NOT fall
    back to the legacy notion_api_key (internal integration tokens don't work
    with the hosted MCP server)."""
    return _load().get("notion_access_token", "")


def set_notion_access_token(token: str) -> None:
    state = _load()
    state["notion_access_token"] = token
    _save()
    logger.info("notion_access_token_saved")


# ── Web watch snapshots ─────────────────────────────────────────────────────
# One entry per watched page: the last text snapshot Speda has been told about,
# plus a `pending` snapshot the scan produced but nobody has confirmed yet. Two
# slots, not one, because committing at scan time would swallow a publication
# whenever the trigger call that follows it failed — the change would be gone
# from the diff and no one would ever hear about it. n8n promotes pending →
# committed via /web/watch/ack only after the turn was accepted.
# NOT a scheduler: n8n still owns the cron (CLAUDE.md). This is just where the
# "what did the page look like last time" memory lives.

def get_web_watch(watch_id: str) -> dict:
    """Stored state for one watch: {fingerprint, snapshot, pending, updated_at}.
    Empty dict when the page has never been scanned."""
    return dict(_load().get("web_watches", {}).get(watch_id, {}))


def get_web_watches() -> dict[str, dict]:
    """Every stored watch. Read-only view for the listing endpoint — the owner
    edits the watch LIST in n8n, so this is how they see what Igor actually
    holds snapshots for (including watches they have since removed)."""
    return dict(_load().get("web_watches", {}))


def set_web_watch(watch_id: str, state: dict) -> None:
    store = _load()
    watches = dict(store.get("web_watches", {}))
    watches[watch_id] = state
    store["web_watches"] = watches
    _save()


def drop_web_watch(watch_id: str) -> bool:
    """Forget a watch entirely — the next scan re-baselines instead of diffing.
    Returns whether anything was actually removed."""
    store = _load()
    watches = dict(store.get("web_watches", {}))
    existed = watches.pop(watch_id, None) is not None
    store["web_watches"] = watches
    _save()
    logger.info("web_watch_dropped", extra={"watch_id": watch_id, "existed": existed})
    return existed


# ── Owner-defined MCP servers ───────────────────────────────────────────────
# MCP's whole premise is that a server is a thing you point at and authenticate,
# not a thing someone has to compile in — so the owner can add one by hand from
# Settings → Connections and it becomes a Tier-2 capability like any other. Every
# built-in server in app/mcp/servers.py could have been written as one of these;
# they stay in code only because they ship configured.
#
# Shape of one record:
#   {name, transport: "stdio"|"http", command: [...], url, env: {}, headers: {},
#    enabled: bool, added_at: iso8601, note: str}
#
# env/headers hold CREDENTIALS. They live in the same runtime_state.json as the
# Google and Microsoft refresh tokens — the file is already the trust boundary —
# and the API masks them on read so the Settings panel never renders a key back.

_SECRET_HINT = ("token", "key", "secret", "password", "auth", "pat", "credential")


def get_custom_mcp_servers() -> list[dict]:
    """Every hand-added server record, secrets included. Callers that render to
    a client must go through mask_custom_mcp_server first."""
    return [dict(s) for s in _load().get("custom_mcp_servers", [])]


def get_custom_mcp_server(name: str) -> dict:
    for server in get_custom_mcp_servers():
        if server.get("name") == name:
            return server
    return {}


def save_custom_mcp_server(record: dict) -> dict:
    """Insert or replace one server record by name. Returns what was stored.

    A re-save that omits `env`/`headers` KEEPS the stored values rather than
    wiping them — the UI sends back masked secrets it never saw, and treating
    those as "the owner cleared this field" would break the connection every
    time they renamed a server.
    """
    state = _load()
    servers = [dict(s) for s in state.get("custom_mcp_servers", [])]
    name = record.get("name", "")
    existing = next((s for s in servers if s.get("name") == name), None)
    if existing:
        for field in ("env", "headers"):
            merged = dict(existing.get(field) or {})
            for key, value in (record.get(field) or {}).items():
                # A masked value came back unchanged from the UI — keep the real one.
                if value == MASKED and key in merged:
                    continue
                merged[key] = value
            record[field] = merged
        record.setdefault("added_at", existing.get("added_at", ""))
        servers = [record if s.get("name") == name else s for s in servers]
    else:
        servers.append(record)
    state["custom_mcp_servers"] = servers
    _save()
    logger.info("custom_mcp_saved", extra={"server": name, "transport": record.get("transport")})
    return record


def delete_custom_mcp_server(name: str) -> bool:
    state = _load()
    servers = [dict(s) for s in state.get("custom_mcp_servers", [])]
    remaining = [s for s in servers if s.get("name") != name]
    existed = len(remaining) != len(servers)
    state["custom_mcp_servers"] = remaining
    # A deleted server must not stay in the disabled list forever — re-adding it
    # later would come back silently switched off with nothing pointing at why.
    disabled = set(state.get("disabled_servers", []))
    disabled.discard(name)
    state["disabled_servers"] = sorted(disabled)
    _save()
    logger.info("custom_mcp_deleted", extra={"server": name, "existed": existed})
    return existed


MASKED = "••••••••"


def mask_custom_mcp_server(record: dict) -> dict:
    """A copy safe to send to a client: every credential-looking value replaced
    with a fixed placeholder. Names of the keys survive — the owner needs to see
    THAT an API key is set, just not what it is."""
    out = dict(record)
    for field in ("env", "headers"):
        values = record.get(field) or {}
        out[field] = {
            k: (MASKED if (v and any(h in k.lower() for h in _SECRET_HINT)) else v)
            for k, v in values.items()
        }
    return out


# ── Web portals (the browser sidecar's credential vault) ────────────────────
# A portal is a site the owner has an account on and their agents are allowed to
# reach: the student automation, the library, a supplier's order page. The record
# holds the credentials; the COOKIES live in the sidecar (packages/browser), and
# the two never swap jobs — this file is already the trust boundary for the
# Google and Microsoft refresh tokens, and the sidecar must stay a container that
# renders untrusted pages without holding a password.
#
# The password never reaches a model. `portal_login` names a portal; the skill
# reads the record here and hands it to the sidecar over the internal network.
# Nothing in the completion, the message table, the embedding index or the
# memory pipeline ever contains it — which is the entire reason this is a vault
# and not just a prompt the owner types their password into.
#
# Shape of one record:
#   {name, label, login_url, home_url, username, password,
#    selectors: {username, password, submit}, extra_fields: {},
#    success_selector, success_url_contains, allowed_agents: [], note,
#    enabled: bool, added_at, last_login, last_status}

_PORTAL_SECRET_FIELDS = ("password",)


def get_portals() -> list[dict]:
    """Every portal record, credentials included. Anything rendering to a client
    must go through mask_portal first."""
    return [dict(p) for p in _load().get("portals", [])]


def get_portal(name: str) -> dict:
    for portal in get_portals():
        if portal.get("name") == name:
            return portal
    return {}


def save_portal(record: dict) -> dict:
    """Insert or replace one portal by name. Returns what was stored.

    A re-save that sends the masked password back KEEPS the stored one, for the
    same reason the MCP server records do: the UI is echoing a value it never
    saw, and reading that as "the owner cleared this" would break the login every
    time they fixed a typo in the label.
    """
    state = _load()
    portals = [dict(p) for p in state.get("portals", [])]
    name = record.get("name", "")
    existing = next((p for p in portals if p.get("name") == name), None)
    if existing:
        for field in _PORTAL_SECRET_FIELDS:
            if record.get(field) in (MASKED, None, ""):
                record[field] = existing.get(field, "")
        record.setdefault("added_at", existing.get("added_at", ""))
        # Login history belongs to the portal, not to whoever last edited it.
        for field in ("last_login", "last_status"):
            record.setdefault(field, existing.get(field, ""))
        portals = [record if p.get("name") == name else p for p in portals]
    else:
        portals.append(record)
    state["portals"] = portals
    _save()
    logger.info("portal_saved", extra={"portal": name})
    return record


def delete_portal(name: str) -> bool:
    state = _load()
    portals = [dict(p) for p in state.get("portals", [])]
    remaining = [p for p in portals if p.get("name") != name]
    existed = len(remaining) != len(portals)
    state["portals"] = remaining
    _save()
    logger.info("portal_deleted", extra={"portal": name, "existed": existed})
    return existed


def record_portal_login(name: str, ok: bool, message: str) -> None:
    """Stamp the outcome of the last sign-in attempt onto the record.

    This is what makes a silently-expired portal visible: the Settings row can
    say "last signed in 3 weeks ago, then stopped working" instead of the owner
    finding out when an agent reports it cannot read their grades.
    """
    from datetime import datetime, timezone

    state = _load()
    portals = [dict(p) for p in state.get("portals", [])]
    for portal in portals:
        if portal.get("name") == name:
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            portal["last_status"] = ("ok: " if ok else "failed: ") + message[:200]
            if ok:
                portal["last_login"] = stamp
            portal["last_attempt"] = stamp
            break
    state["portals"] = portals
    _save()


def mask_portal(record: dict) -> dict:
    """A copy safe to send to a client: the password replaced with a placeholder.
    The username survives — the owner needs to confirm WHICH account this is."""
    out = dict(record)
    for field in _PORTAL_SECRET_FIELDS:
        if record.get(field):
            out[field] = MASKED
    return out


def set_server_active(server: str, active: bool) -> bool:
    state = _load()
    disabled = set(state.get("disabled_servers", []))
    if active:
        disabled.discard(server)
    else:
        disabled.add(server)
    state["disabled_servers"] = sorted(disabled)
    _save()
    logger.info("server_active_set", extra={"server": server, "active": active})
    return active


# ── Health sync demand ──────────────────────────────────────────────────────
# A standing request for Speda GO to sync Health Connect NOW, rather than on its
# next ~4h trickle. Atomix raises it when a turn needs data that describes the
# present (a morning briefing), and the phone clears it by syncing.
#
# A flag rather than a push because Speda GO carries no Firebase — there is no
# way to wake the app from the server side, so this is a message left where the
# phone will find it: the app checks on foreground and after each sync. That
# means a demand raised while the phone is asleep goes unanswered, which is
# precisely why the caller must treat the wait as failable rather than assuming
# fresh data eventually arrives.

def request_health_sync(reason: str = "") -> float:
    """Raise the demand. Returns the epoch stamp recorded, which the caller can
    compare against later ingests to tell "the phone answered ME" apart from
    "a routine sync happened to land"."""
    import time

    state = _load()
    stamp = time.time()
    state["health_sync_demand"] = {"at": stamp, "reason": reason[:120], "served_at": 0}
    _save()
    logger.info("health_sync_demanded", extra={"reason": reason[:120]})
    return stamp


def get_health_sync_demand() -> dict:
    """{at, reason, served_at} — empty dict when nothing is outstanding."""
    return dict(_load().get("health_sync_demand", {}))


def clear_health_sync_demand() -> None:
    """Called when the phone delivers a batch: the demand has been served."""
    import time

    state = _load()
    demand = dict(state.get("health_sync_demand") or {})
    if not demand or demand.get("served_at"):
        return
    demand["served_at"] = time.time()
    state["health_sync_demand"] = demand
    _save()
