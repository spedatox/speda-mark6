"""
Configuration — read/write every configurable setting from the desktop
Settings → Configuration tab. Zero business logic beyond persisting to the
managed override .env and live-applying the safe subset (Rule 1).

Security: this endpoint reads and writes secrets, so it sits behind the same
X-API-Key gate as everything else (AuthMiddleware). Secret values are NEVER
returned to the client — GET reports only whether each is set plus a short
masked hint; PUT overwrites a secret only when a non-empty value is sent for it.
"""

import logging

from fastapi import APIRouter, Request

from app.config import read_managed_env, settings, write_managed_env
from app.config_schema import CONFIG_GROUPS, FIELD_BY_KEY, ConfigField

logger = logging.getLogger(__name__)
router = APIRouter(tags=["config"])


def _mask(value: str) -> str:
    """Short hint for a secret so the owner can tell WHICH key is stored without
    exposing it. Shows only the last 4 chars of anything long enough."""
    if not value:
        return ""
    v = str(value)
    return f"…{v[-4:]}" if len(v) > 8 else "••••"


def _serialize_field(f: ConfigField) -> dict:
    raw = getattr(settings, f.key, "")
    is_set = bool(raw) if not isinstance(raw, bool) else True
    out: dict = {
        "key": f.key,
        "label": f.label,
        "type": f.type,
        "secret": f.secret,
        "requires_restart": f.requires_restart,
        "help": f.help,
        "placeholder": f.placeholder,
        "options": list(f.options),
        "is_set": is_set,
    }
    if f.secret:
        out["hint"] = _mask(str(raw)) if raw else ""
    else:
        out["value"] = raw
    return out


@router.get("/config")
async def get_config():
    """The full config catalog with current values (secrets masked)."""
    return {
        "groups": [
            {
                "id": g.id,
                "label": g.label,
                "blurb": g.blurb,
                "fields": [_serialize_field(f) for f in g.fields],
            }
            for g in CONFIG_GROUPS
        ]
    }


def _coerce_for_settings(f: ConfigField, raw):
    if f.type == "bool":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if f.type == "int":
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return getattr(settings, f.key)
    return str(raw)


def _to_env(f: ConfigField, raw) -> str:
    if f.type == "bool":
        return "true" if _coerce_for_settings(f, raw) else "false"
    if f.type == "int":
        return str(_coerce_for_settings(f, raw))
    return str(raw)


@router.put("/config")
async def put_config(request: Request, body: dict):
    """Persist changed settings to the managed override .env and live-apply those
    that are read lazily. Body: {"values": {key: value, ...}} — send ONLY the
    fields the owner changed. For a secret, an empty string clears the override;
    an omitted secret is left untouched.

    Returns which keys were applied live vs. need a restart, and any rejected
    unknown keys.
    """
    values = body.get("values")
    if not isinstance(values, dict) or not values:
        return {"applied_live": [], "restart_required": [], "rejected": [], "detail": "no values"}

    env_updates: dict[str, str | None] = {}
    applied_live: list[str] = []
    restart_required: list[str] = []
    rejected: list[str] = []

    for key, raw in values.items():
        f = FIELD_BY_KEY.get(key)
        if f is None:
            rejected.append(key)
            continue

        env_key = f.key.upper()
        # A secret sent empty = clear the override (revert to .env/default).
        if f.secret and (raw is None or str(raw) == ""):
            env_updates[env_key] = None
        else:
            env_updates[env_key] = _to_env(f, raw)

        # Live-apply the safe subset (lazily-read fields). Restart-required fields
        # are persisted only — a subsystem built at startup won't pick them up.
        if not f.requires_restart:
            try:
                setattr(settings, f.key, _coerce_for_settings(f, raw))
                applied_live.append(f.key)
            except Exception as e:  # noqa: BLE001
                logger.warning("config_live_apply_failed", extra={"key": f.key, "error": str(e)})
                restart_required.append(f.key)
        else:
            restart_required.append(f.key)

    write_managed_env(env_updates)
    logger.info(
        "config_updated",
        extra={
            "changed": sorted(env_updates),
            "applied_live": applied_live,
            "restart_required": restart_required,
        },
    )
    return {
        "applied_live": applied_live,
        "restart_required": restart_required,
        "rejected": rejected,
    }
