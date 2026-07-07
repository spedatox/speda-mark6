"""
Runtime-mutable application state that persists across restarts.

Unlike app.config.settings (read once from env at startup), these values can be
flipped at runtime — by the UI, an API call, or SPEDA itself via a tool — and are
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
# a user who never started it — an unstarted bot falls back to SPEDA's). Update
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
