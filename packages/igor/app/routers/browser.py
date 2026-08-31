# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Web portals — the owner's saved logins, and the browser that uses them.

Sits under /connections/… because that is what it is: a connection, the same
kind of thing as the Notion token or the Google refresh token, just to a site
that never published an API. The Settings panel that drives it lives in
packages/heartbreaker (PortalsPanel.tsx).

Rule 1 — no logic here. The vault is app/core/runtime_state.py, the browser is
app/services/browser.py, and this file only validates a name and routes.

One thing worth stating plainly: **saving a portal signs into it immediately**,
unless the owner asks not to. A credential that was stored but never tried is
indistinguishable from one that works right up until an agent needs it at 2am,
which is the worst possible moment to discover a typo. Same reasoning as the
custom-MCP save in connections.py.
"""

import datetime
import logging
import re

from fastapi import APIRouter, Request

from app.core.runtime_state import (
    delete_portal,
    get_portal,
    get_portals,
    mask_portal,
    save_portal,
)
from app.services import browser as browser_svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["portals"])

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


@router.get("/connections/portals")
async def list_portals(request: Request):
    """Every saved portal with its password masked, plus whether the browser
    that would use them is actually up."""
    status = await browser_svc.health()
    live = set(status.get("profiles") or [])
    rows = []
    for record in get_portals():
        row = mask_portal(record)
        # "Has cookies in the sidecar" is the honest definition of signed-in —
        # anything else would be this file guessing on the portal's behalf.
        row["session"] = record.get("name") in live
        rows.append(row)
    agents = [p.agent_id for p in request.app.state.profiles.roster() if p.dispatch_target]
    return {"portals": rows, "browser": status, "agents": agents}


@router.post("/connections/portals")
async def save(request: Request, body: dict):
    """Add or update a portal, then try the login.

    Body: {name, label, login_url, home_url, username, password, selectors:{},
           extra_fields:{}, success_selector, success_url_contains,
           allowed_agents:[], note, enabled, test: bool}
    """
    name = (body.get("name") or "").strip().lower()
    if not _NAME_RE.match(name):
        return {"error": "Name must be 2–41 characters: lowercase letters, digits, "
                         "'_' or '-', starting with a letter or digit."}
    login_url = (body.get("login_url") or "").strip()
    if not login_url.startswith(("http://", "https://")):
        return {"error": "The login URL must be the full https:// address of the sign-in page."}

    existing = get_portal(name)
    record = {
        "name": name,
        "label": (body.get("label") or "").strip()[:120] or name,
        "login_url": login_url,
        "home_url": (body.get("home_url") or "").strip(),
        "username": (body.get("username") or "").strip(),
        "password": body.get("password") or "",
        "selectors": {k: v for k, v in (body.get("selectors") or {}).items() if v},
        "extra_fields": {k: v for k, v in (body.get("extra_fields") or {}).items() if v},
        "success_selector": (body.get("success_selector") or "").strip(),
        "success_url_contains": (body.get("success_url_contains") or "").strip(),
        # Empty means every agent. A student portal is fine wide open; a bank is
        # not, and the owner is the only one who knows which this is.
        "allowed_agents": [a for a in (body.get("allowed_agents") or []) if a],
        "note": (body.get("note") or "")[:200],
        "enabled": bool(body.get("enabled", True)),
        "added_at": existing.get("added_at") or _now(),
    }
    stored = save_portal(record)

    if not stored.get("enabled", True):
        return {"portal": mask_portal(stored), "ok": False,
                "message": "Saved, but left switched off."}
    if body.get("test") is False:
        return {"portal": mask_portal(stored), "ok": None, "message": "Saved."}

    try:
        result = await browser_svc.login_portal(name)
    except browser_svc.BrowserUnavailable as e:
        return {"portal": mask_portal(get_portal(name)), "ok": False,
                "message": f"Saved, but the browser is unreachable: {e}"}
    return {
        "portal": mask_portal(get_portal(name)),
        "ok": bool(result.get("ok")),
        "message": result.get("message", ""),
        "landed_on": result.get("title") or result.get("url", ""),
    }


@router.post("/connections/portals/{name}/login")
async def login_now(name: str):
    """Sign in on demand — the 'Test' button, and the fix after a password change."""
    if not get_portal(name):
        return {"error": f"No portal called '{name}'."}
    try:
        result = await browser_svc.login_portal(name)
    except browser_svc.BrowserUnavailable as e:
        return {"ok": False, "message": str(e)}
    return {
        "ok": bool(result.get("ok")),
        "already": bool(result.get("already")),
        "message": result.get("message", ""),
        "landed_on": result.get("title") or result.get("url", ""),
        "portal": mask_portal(get_portal(name)),
    }


@router.post("/connections/portals/{name}/forget")
async def forget(name: str):
    """Sign out: drop the cookie jar, keep the credentials."""
    try:
        result = await browser_svc.forget_profile(name)
    except browser_svc.BrowserUnavailable as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "cleared": result.get("cleared", False)}


@router.delete("/connections/portals/{name}")
async def remove(name: str):
    """Forget a portal entirely — credentials AND session.

    The cookie drop is not optional and not best-effort-silent: deleting the
    password while leaving a live session in the sidecar would mean the agents
    keep their access after the owner revoked it.
    """
    existed = delete_portal(name)
    cleared = False
    try:
        cleared = (await browser_svc.forget_profile(name)).get("cleared", False)
    except browser_svc.BrowserUnavailable as e:
        logger.warning("portal_session_not_cleared", extra={"portal": name, "error": str(e)})
        return {"deleted": existed, "session_cleared": False,
                "warning": "Credentials deleted, but the browser was unreachable, so its "
                           "stored session for this portal is still there. Re-run the "
                           "delete once the browser container is up."}
    return {"deleted": existed, "session_cleared": cleared}
