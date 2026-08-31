# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SKYFALL PROTOCOL — the owner's own launch rail.

The owner defines PROJECTS. A project is a name, an explanation, and an endpoint
it will hit: URL, method, an optional JSON body, optional headers, and how many
seconds the countdown runs. Arming one opens a full-screen clock; letting the
clock reach zero fires the endpoint; aborting sends nothing at all.

That is the whole protocol, and everything below exists to keep one property
true.

THE SCREEN CANNOT BE SKIPPED
----------------------------
There are two ways in — the owner tells Speda, or picks a project in the
settings pane — and both land on the same countdown. Nothing in this module
fires as a side effect of arming, and the agent path has no way to reach `fire`
at all: the tool (app/skills/skyfall.py) can only ARM, and arming is a message
to the client saying "open the clock". The client fires when the clock runs out.

That is also why arming refuses on a channel with no screen. Telegram can carry
the sentence "activate Skyfall" perfectly well and has nowhere to draw a
countdown or an abort — see core/surface.py. A Skyfall that fired without one
would be the design thrown away and replaced with "an agent that can POST to
arbitrary URLs when asked nicely".

WHO WRITES A PROJECT, AND WHO CANNOT
------------------------------------
**Only the owner, from the settings pane.** There is no tool that creates,
edits or deletes a project, and adding one would be the mistake this whole file
is arranged against: an agent that can both write the target and pull the
trigger is an agent that can hit anything, and the countdown would be guarding a
URL the owner never chose. Agents arm what already exists, by name.

Headers get the portal-credential treatment (core/runtime_state.py already does
this for `password`): stored server-side, masked on every read, never returned
to a client, and never — under any path — placed in something a model reads. A
project's `Authorization: Bearer …` is exactly the kind of string that must not
end up quoted back in a chat message.

WHAT IS NOT GUARDED, AND WHY THAT IS RIGHT
------------------------------------------
The URL is not filtered, and internal hosts are not blocked. `http://n8n:5678/…`
and `http://app:8000/…` are the likely targets, not an attack: this is the
owner's own configuration, typed by the owner, in their own settings pane. The
boundary that matters here is *who writes the project*, and that boundary is
above.
"""

import json
import logging
import uuid

import httpx

from app.core.clock import utc_now
from app.core.runtime_state import (
    delete_skyfall_project,
    get_skyfall_project,
    get_skyfall_projects,
    save_skyfall_project,
)

logger = logging.getLogger(__name__)

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# Countdown bounds. The floor is not decoration: a one-second clock is a screen
# that technically exists and cannot actually be aborted, which is the same as
# not having one. The ceiling stops a typo from arming something that appears to
# hang forever.
MIN_COUNTDOWN, MAX_COUNTDOWN, DEFAULT_COUNTDOWN = 3, 300, 10

# How long the fired request may take before it is given up on. Generous — a
# deploy hook can be slow — but bounded, because the owner is watching a screen.
FIRE_TIMEOUT = 60.0

_MAX_BODY_CHARS = 20_000
# What comes back is shown on the countdown screen, so it is trimmed to
# something a human reads rather than something a terminal scrolls.
_MAX_RESPONSE_CHARS = 4_000


def _now() -> str:
    return utc_now().isoformat(timespec="seconds")


# ── Projects ─────────────────────────────────────────────────────────────────

def validate(record: dict) -> str:
    """Empty string when the project is usable, otherwise why it is not.

    Deliberately strict about the body: a JSON body that does not parse would
    fail at the one moment nobody wants a surprise — after the countdown, with
    the owner watching. It is checked when it is SAVED instead.
    """
    name = str(record.get("name") or "").strip()
    if not name:
        return "a project needs a name"
    if len(name) > 80:
        return "the name is too long (80 characters max)"

    url = str(record.get("url") or "").strip()
    if not url:
        return "a project needs an endpoint URL"
    if not url.lower().startswith(("http://", "https://")):
        return "the endpoint URL must start with http:// or https://"

    method = str(record.get("method") or "POST").strip().upper()
    if method not in METHODS:
        return f"method must be one of {', '.join(METHODS)}"

    body = record.get("body")
    if body not in (None, ""):
        if len(str(body)) > _MAX_BODY_CHARS:
            return "the request body is too large"
        try:
            json.loads(str(body))
        except (TypeError, ValueError) as exc:
            return f"the request body is not valid JSON: {exc}"

    headers = record.get("headers")
    if headers not in (None, {}, ""):
        if not isinstance(headers, dict):
            return "headers must be a set of name/value pairs"
        for key in headers:
            if not str(key).strip():
                return "a header with no name"

    seconds = record.get("countdown_seconds", DEFAULT_COUNTDOWN)
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "the countdown must be a whole number of seconds"
    if not MIN_COUNTDOWN <= seconds <= MAX_COUNTDOWN:
        return (
            f"the countdown must be between {MIN_COUNTDOWN} and {MAX_COUNTDOWN} "
            f"seconds — below {MIN_COUNTDOWN} there is no real chance to abort"
        )
    return ""


def normalize(record: dict, existing: dict | None = None) -> dict:
    """A submitted project, cleaned into what gets stored.

    A header sent back as the mask is left ALONE rather than overwritten: the UI
    never receives the real value, so re-saving a form it rendered would
    otherwise blank every secret the owner did not retype.
    """
    prior = existing or {}
    headers = {}
    for key, value in (record.get("headers") or {}).items():
        key = str(key).strip()
        if not key:
            continue
        if value == MASK:
            kept = (prior.get("headers") or {}).get(key)
            if kept is not None:
                headers[key] = kept
            continue
        headers[key] = str(value)

    return {
        "id": prior.get("id") or record.get("id") or uuid.uuid4().hex[:12],
        "name": str(record.get("name") or "").strip(),
        "description": str(record.get("description") or "").strip()[:600],
        "url": str(record.get("url") or "").strip(),
        "method": str(record.get("method") or "POST").strip().upper(),
        "body": str(record.get("body") or "").strip(),
        "headers": headers,
        "countdown_seconds": int(record.get("countdown_seconds") or DEFAULT_COUNTDOWN),
        "created_at": prior.get("created_at") or _now(),
        "updated_at": _now(),
        "last_fired_at": prior.get("last_fired_at", ""),
        "last_result": prior.get("last_result", ""),
    }


MASK = "••••••••"


def mask(record: dict) -> dict:
    """A project as anything outside this module may see it.

    Header VALUES never leave — the names do, because the owner has to be able
    to see which headers a project carries in order to manage them. This is the
    same split runtime_state already makes for portal passwords.
    """
    return {
        **{k: v for k, v in record.items() if k != "headers"},
        "headers": {key: MASK for key in (record.get("headers") or {})},
        "has_body": bool(record.get("body")),
    }


def listing() -> list[dict]:
    """Every project, masked, newest first."""
    projects = sorted(
        get_skyfall_projects().values(),
        key=lambda p: p.get("created_at", ""),
        reverse=True,
    )
    return [mask(p) for p in projects]


def save(record: dict) -> tuple[dict | None, str]:
    """Create or update one project. Returns (masked project, error)."""
    existing = get_skyfall_project(str(record.get("id") or "")) or None
    merged = normalize(record, existing)
    problem = validate(merged)
    if problem:
        return None, problem
    save_skyfall_project(merged["id"], merged)
    logger.info("skyfall_project_saved",
                extra={"project": merged["id"], "project_name": merged["name"]})
    return mask(merged), ""


def remove(project_id: str) -> bool:
    return delete_skyfall_project(project_id)


def find(query: str) -> tuple[dict | None, list[dict]]:
    """Resolve what the owner SAID into exactly one project.

    Returns (project, candidates). A project only comes back when the answer is
    unambiguous: an exact id, an exact name, or a single case-insensitive
    substring hit. Anything else returns the candidates and no project, because
    the alternative is an agent picking one of two similarly-named projects and
    arming a screen for the wrong endpoint — and the owner's abort window is the
    only thing between that mistake and a real request.
    """
    projects = list(get_skyfall_projects().values())
    text = (query or "").strip().lower()
    if not text:
        return None, projects

    for p in projects:
        if p.get("id", "").lower() == text or p.get("name", "").strip().lower() == text:
            return p, [p]

    hits = [p for p in projects if text in p.get("name", "").lower()]
    return (hits[0], hits) if len(hits) == 1 else (None, hits or projects)


# ── Firing ───────────────────────────────────────────────────────────────────

async def fire(project_id: str) -> tuple[bool, dict]:
    """Send the project's request. Called only when a countdown reached zero.

    Never raises: this runs with the owner watching a screen, and an exception
    that reached the router would render as a blank panel rather than as the
    truth, which is that a request may well have been sent.
    """
    project = get_skyfall_project(project_id)
    if not project:
        return False, {"error": "no such project — it may have been deleted since the "
                                "countdown started", "fired": False}

    problem = validate(project)
    if problem:
        # A project edited into an invalid state between arming and zero.
        return False, {"error": f"the project is not usable: {problem}", "fired": False}

    body = project.get("body") or ""
    headers = dict(project.get("headers") or {})
    if body and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json"

    started = _now()
    logger.warning(
        "skyfall_fire",
        extra={"project": project_id, "project_name": project.get("name"),
               "method": project.get("method"), "url": project.get("url")},
    )

    try:
        async with httpx.AsyncClient(timeout=FIRE_TIMEOUT, follow_redirects=True) as client:
            response = await client.request(
                project.get("method", "POST"),
                project["url"],
                content=body.encode("utf-8") if body else None,
                headers=headers,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("skyfall_fire_failed",
                     extra={"project": project_id, "error": str(exc)[:300]})
        result = {
            "fired": True, "ok": False, "status": 0,
            "error": str(exc)[:300], "started_at": started, "finished_at": _now(),
        }
        _record(project, result)
        return False, result

    text = response.text or ""
    result = {
        "fired": True,
        "ok": response.is_success,
        "status": response.status_code,
        "body": text[:_MAX_RESPONSE_CHARS],
        "truncated": len(text) > _MAX_RESPONSE_CHARS,
        "started_at": started,
        "finished_at": _now(),
        "error": "",
    }
    logger.warning("skyfall_fired", extra={"project": project_id,
                                           "status": response.status_code})
    _record(project, result)
    return response.is_success, result


def abort(project_id: str, remaining: float = 0.0) -> dict:
    """Record that a countdown was stopped. Nothing was sent.

    Worth a log line of its own rather than silence: "the owner armed Skyfall
    and changed their mind with four seconds left" is a real event, and a
    protocol whose aborts leave no trace cannot answer "did that fire or not?"
    afterwards.
    """
    project = get_skyfall_project(project_id) or {}
    logger.warning(
        "skyfall_aborted",
        extra={"project": project_id, "project_name": project.get("name", ""),
               "remaining_s": round(float(remaining or 0), 1)},
    )
    return {"aborted": True, "fired": False, "project_id": project_id}


def _record(project: dict, result: dict) -> None:
    """Stamp the outcome onto the project so the pane can show what happened last."""
    summary = (
        f"{result['status']} at {result['finished_at']}"
        if result.get("status") else f"failed: {result.get('error', '')[:120]}"
    )
    save_skyfall_project(project["id"], {**project,
                                         "last_fired_at": result["finished_at"],
                                         "last_result": summary})


def arming_payload(project: dict) -> dict:
    """What the client needs to draw the countdown — and nothing more.

    No headers, no body: the screen shows what is about to happen, and the
    request is assembled server-side at zero. A client that never holds the
    secret cannot leak it, and a client that cannot alter the body cannot turn
    an armed countdown into a different request than the one the owner armed.
    """
    return {
        "project_id": project["id"],
        "name": project.get("name", ""),
        "description": project.get("description", ""),
        "method": project.get("method", "POST"),
        "url": project.get("url", ""),
        "countdown_seconds": int(project.get("countdown_seconds") or DEFAULT_COUNTDOWN),
        "armed_at": _now(),
    }
