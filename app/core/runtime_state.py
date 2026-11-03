# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compatibility state for portals and device sync; no model, budget or memory authority."""
import json
import logging
import os
import tempfile
from app.config import _DATA_DIR, settings
logger = logging.getLogger(__name__)
_STATE_FILE = _DATA_DIR / 'runtime_state.json'
_cache = None
MASKED = chr(0x2022) * 8
_PORTAL_SECRET_FIELDS = ('password',)


def _load():
    global _cache
    if _cache is None:
        _cache = json.loads(_STATE_FILE.read_text(encoding='utf-8')) if _STATE_FILE.exists() else {}
        if not isinstance(_cache, dict):
            raise ValueError('Runtime settings file is malformed; restore its backup before changing it')
    return _cache


def _save():
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=_STATE_FILE.parent, prefix='.runtime-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(_cache or {}, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _STATE_FILE)
    finally:
        from pathlib import Path
        Path(temporary).unlink(missing_ok=True)


def get_house_party() -> bool:
    return bool(_load().get("house_party", False))


def get_telegram_cutover_offset(agent: str) -> int:
    """Retained polling cursor; native input receipts own all newer updates."""
    return int(_load().get('telegram_cutover_offsets', {}).get(agent, 0))


def set_house_party(value: bool) -> bool:
    # Presentation flag only. Coordinator owns all actual collaboration.
    _load()["house_party"] = bool(value)
    _save()
    return bool(value)


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


def record_portal_login(name: str, ok: bool, message: str, landed_url: str = "") -> None:
    """Stamp the outcome of the last sign-in attempt onto the record.

    This is what makes a silently-expired portal visible: the Settings row can
    say "last signed in 3 weeks ago, then stopped working" instead of the owner
    finding out when an agent reports it cannot read their grades.

    It also LEARNS `home_url` the first time a login lands somewhere, if the
    owner never set one. `ensure_logged_in()` needs a page that actually
    requires a session to probe "am I still in" — the raw login URL doesn't
    count, because plenty of portals (OBS among them) render the full login
    form there unconditionally, session or not, which made every check look
    like a fresh sign-in was needed and defeated the whole point of checking
    first. This never overwrites a home_url the owner configured by hand.
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
                if landed_url and not portal.get("home_url"):
                    portal["home_url"] = landed_url
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


def get_notion_credentials() -> dict:
    return dict(_load().get('notion_credentials', {'access_token': settings.notion_api_key}))


def set_notion_credentials(credentials: dict) -> None:
    _load()['notion_credentials'] = dict(credentials)
    _save()
