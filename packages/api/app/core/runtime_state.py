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
