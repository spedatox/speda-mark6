# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The browser desk — Igor's half of the Playwright sidecar (packages/browser).

Rule 1 keeps routers logic-free and Rule 5 keeps the orchestrator ignorant of
what a tool is, so everything between "a skill wants a page" and "a container
rendered one" lives here: reaching the sidecar, shaping what comes back into
something worth spending context on, pulling a captured download across, and
looking up which portal the owner means.

Two things this module deliberately owns rather than delegating:

**Credentials.** `login_portal()` reads the record from runtime_state and posts
it to the sidecar. No caller passes a password in, and none gets one back. A
skill names a portal; that is the whole of its access to the owner's accounts.

**The shape of a page.** `render()` returns text plus links plus, on request, an
ARIA snapshot — and every one of them is capped here, not just in the sidecar,
because the cap that matters is the one on what enters a completion.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

import httpx

from app.config import settings
from app.core.runtime_state import get_portal, get_portals, record_portal_login

logger = logging.getLogger(__name__)

# A render is slower than a fetch by construction — that is what it buys. The
# sidecar's own nav timeout is 45s, so this has to outlast it or a slow portal
# reads as "the browser is down" instead of "the page took 40 seconds".
_TIMEOUT = 75.0
_LOGIN_TIMEOUT = 90.0


class BrowserUnavailable(RuntimeError):
    """The sidecar is not configured or did not answer. Skills turn this into a
    sentence for the model rather than a traceback."""


def configured() -> bool:
    return bool(settings.browser_url)


def _headers() -> dict:
    return {"X-Browser-Token": settings.browser_token} if settings.browser_token else {}


async def _post(path: str, payload: dict, timeout: float = _TIMEOUT) -> dict:
    if not configured():
        raise BrowserUnavailable(
            "The browser isn't configured (BROWSER_URL unset), so pages can't be rendered."
        )
    url = f"{settings.browser_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=_headers())
    except Exception as e:  # noqa: BLE001
        raise BrowserUnavailable(f"The browser service didn't answer: {e}") from e
    if resp.status_code == 401:
        raise BrowserUnavailable(
            "The browser service rejected our token — BROWSER_TOKEN differs between "
            "the app and the browser container."
        )
    if resp.status_code >= 400:
        raise BrowserUnavailable(f"Browser service error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def health() -> dict:
    """Liveness plus the list of profiles that currently hold cookies. Used by
    the Settings panel, so it never raises — an unreachable browser is a status,
    not an exception, on a page whose whole job is showing status."""
    if not configured():
        return {"status": "off", "reason": "BROWSER_URL not set"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{settings.browser_url.rstrip('/')}/health", headers=_headers()
            )
        if resp.status_code == 401:
            return {"status": "down", "reason": "BROWSER_TOKEN mismatch"}
        resp.raise_for_status()
        return resp.json()
    except Exception as e:  # noqa: BLE001
        return {"status": "down", "reason": str(e)[:200]}


# ── Rendering ────────────────────────────────────────────────────────────────


async def render(
    url: str,
    *,
    profile: str | None = None,
    wait_for: str | None = None,
    wait_ms: int = 0,
    aria: bool = False,
    screenshot: bool = False,
) -> dict:
    """Load one URL in a real browser and hand back what it says."""
    return await _post("/read", {
        "url": url, "profile": profile or "", "wait_for": wait_for,
        "wait_ms": wait_ms, "aria": aria, "screenshot": screenshot,
    })


async def act(
    steps: list[dict],
    *,
    session_id: str | None = None,
    profile: str | None = None,
    wait_for: str | None = None,
    dialog_policy: str | None = None,
    dialog_text: str | None = None,
    files: dict[str, str] | None = None,
    include_network: bool = False,
    close: bool = False,
) -> dict:
    """Run a short flow against a live session and report where it landed.

    `files` is `{filename: base64 bytes}` — the skill resolves the filename
    against Igor's own output directory before this is ever called, so what
    crosses the wire here is already-verified bytes, never a path the model
    invented. `close` ends the session after the steps run (valid with an
    empty `steps` list too, as a deliberate "done with this tab" call).
    """
    return await _post("/act", {
        "steps": steps, "session_id": session_id or None,
        "profile": profile or "", "wait_for": wait_for,
        "dialog_policy": dialog_policy or None, "dialog_text": dialog_text or None,
        "files": files or None, "include_network": include_network, "close": close,
    })


async def close_session(session_id: str) -> None:
    if not configured():
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.delete(
                f"{settings.browser_url.rstrip('/')}/session/{session_id}",
                headers=_headers(),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("browser_session_close_failed", extra={"error": str(e)})


async def forget_profile(name: str) -> dict:
    """Sign a portal out — drop its cookie jar in the sidecar."""
    return await _post("/forget", {"profile": name}, timeout=20.0)


# ── Portals ──────────────────────────────────────────────────────────────────


def portal_names() -> list[str]:
    return [p["name"] for p in get_portals() if p.get("enabled", True) and p.get("name")]


def portal_catalogue() -> str:
    """One line per portal, for a tool description or a system prompt. Labels
    and notes only — never a username, and obviously never a password."""
    rows = []
    for portal in get_portals():
        if not portal.get("enabled", True):
            continue
        label = portal.get("label") or portal.get("name")
        note = portal.get("note") or ""
        rows.append(f"- {portal['name']} — {label}{(' · ' + note) if note else ''}")
    return "\n".join(rows)


def portal_allows(portal: dict, agent_id: str) -> bool:
    """Whether this agent may use this portal.

    Empty allowed_agents means every agent, which is the right default for a
    library catalogue and the wrong one for a bank. The owner decides per portal;
    the check is here so both the skill and the router get the same answer.
    """
    allowed = portal.get("allowed_agents") or []
    return not allowed or agent_id in allowed


async def login_portal(name: str) -> dict:
    """Sign in to a saved portal. The credential goes app → sidecar → page and
    is never returned, logged, or handed to a caller.

    Some portals (OSTİM's OBS among them) gate login behind an image captcha.
    That is solved entirely inside the sidecar — see
    packages/browser/server.py's solve_captcha_with_vision — rather than here:
    OBS's login session expires faster than a capture-then-ask-Igor-then-
    submit round trip takes, so solving has to happen in the same call that
    fills and submits the form. This function stays a single POST.
    """
    portal = get_portal(name)
    if not portal:
        raise BrowserUnavailable(
            f"No portal called '{name}'. Known portals: "
            f"{', '.join(portal_names()) or '(none configured)'}."
        )
    if not portal.get("enabled", True):
        raise BrowserUnavailable(f"The '{name}' portal is switched off in Settings.")
    if not portal.get("login_url"):
        raise BrowserUnavailable(f"The '{name}' portal has no login URL configured.")

    result = await _post("/login", {
        "profile": name,
        "login_url": portal.get("login_url"),
        "username": portal.get("username") or "",
        "password": portal.get("password") or "",
        "selectors": portal.get("selectors") or {},
        "extra_fields": portal.get("extra_fields") or {},
        "success_selector": portal.get("success_selector") or "",
        "success_url_contains": portal.get("success_url_contains") or "",
    }, timeout=_LOGIN_TIMEOUT)

    record_portal_login(name, bool(result.get("ok")), result.get("message", ""),
                        landed_url=result.get("url") or "")
    logger.info("portal_login", extra={"portal": name, "ok": bool(result.get("ok"))})
    return result


async def ensure_logged_in(name: str, probe_url: str | None = None) -> dict:
    """Visit the portal; if it bounced us to a login form, sign in and revisit.

    This is the call a skill actually wants. Sessions expire on the site's clock,
    not ours, so "am I still logged in" can only be answered by asking — and
    making every caller write that dance is how you end up with three versions of
    it that disagree about what a login page looks like.
    """
    portal = get_portal(name)
    if not portal:
        raise BrowserUnavailable(f"No portal called '{name}'.")
    target = probe_url or portal.get("home_url") or portal.get("login_url") or ""
    if not target:
        raise BrowserUnavailable(f"The '{name}' portal has no URL to visit.")

    page = await render(target, profile=name, aria=True)
    if not looks_like_login(page, portal):
        return {"ok": True, "logged_in": True, "fresh": False, "page": page}

    login = await login_portal(name)
    if not login.get("ok"):
        return {"ok": False, "logged_in": False, "fresh": True,
                "message": login.get("message", "login failed"), "page": login}
    page = await render(target, profile=name, aria=True)
    return {"ok": True, "logged_in": True, "fresh": True, "page": page}


_LOGIN_WORDS = re.compile(
    r"\b(giriş yap|oturum aç|kullanıcı adı|şifre|parola|sign in|log ?in|password|username)\b",
    re.I,
)


def looks_like_login(page: dict, portal: dict) -> bool:
    """Did we land on a login wall?

    Three signals, in descending order of how much they actually know:

    1. `has_password` — the sidecar asked the DOM whether a password field is on
       screen. That is a fact, and on its own it settles almost every case.
    2. `success_url_contains` — the owner stating outright what "inside" looks
       like. It exists for the portal that keeps a hidden login form on every
       page, where (1) is a permanent false positive.
    3. Reading the page, in both languages the owner's portals are written in.
       Only reached when the sidecar is an older build that sends neither.

    Where they run out, this errs toward "yes, log in again": a needless
    re-login costs six seconds, while missing one makes an agent report a login
    form's contents as the owner's exam results.
    """
    url = (page.get("url") or "").lower()
    if portal.get("success_url_contains"):
        return portal["success_url_contains"].lower() not in url
    if "has_password" in page:
        return bool(page["has_password"])
    text = f"{page.get('title', '')}\n{(page.get('text') or '')[:1500]}"
    aria = page.get("aria") or ""
    if "textbox" in aria and re.search(r"password|şifre|parola", aria, re.I):
        return True
    if any(word in url for word in ("login", "signin", "giris", "oturum")):
        return bool(_LOGIN_WORDS.search(text))
    return False


# ── What the model sees ──────────────────────────────────────────────────────


def format_page(page: dict, *, include_links: bool = True, include_aria: bool = False,
                limit: int | None = None) -> str:
    """A rendered page as text a model can reason over.

    Links are listed separately rather than left inline because that is what the
    next call needs: the model's follow-up is almost always "open the third one",
    and an href buried in a paragraph is not addressable.
    """
    limit = limit or settings.browser_max_chars
    parts: list[str] = []
    title = page.get("title") or ""
    parts.append(f"# {title}\n{page.get('url', '')}" if title else page.get("url", ""))

    text = (page.get("text") or "").strip()
    if not text:
        parts.append("(The page rendered but has no readable text — it may be a "
                     "login wall, a PDF viewer, or an app that draws to canvas.)")
    else:
        parts.append(text[:limit] + ("\n…(truncated)" if len(text) > limit else ""))

    if include_aria and page.get("aria"):
        parts.append("## Interactive elements (ARIA)\n"
                     "Target these with role selectors, e.g. role=button[name=\"Giriş\"].\n"
                     + page["aria"][:4000])

    if include_links and page.get("links"):
        seen, rows = set(), []
        for link in page["links"]:
            href = link.get("href") or ""
            label = (link.get("text") or "").strip()
            if not label or href in seen:
                continue
            seen.add(href)
            rows.append(f"- {label} → {href}")
            if len(rows) >= 40:
                break
        if rows:
            parts.append("## Links\n" + "\n".join(rows))
    return "\n\n".join(p for p in parts if p)


# ── Artifacts ────────────────────────────────────────────────────────────────


async def pull_artifact(token: str, filename: str) -> Path | None:
    """Copy a captured download or screenshot out of the sidecar into the outputs
    directory, where register_file() can turn it into a download card.

    The file crosses on purpose rather than being served from the sidecar: the
    browser container is not reachable from the client, and a link the owner
    cannot open is not a delivery.
    """
    if not configured():
        return None
    safe = re.sub(r"[^\w.\-]", "_", filename or "download")[:80] or "download"
    dest = Path(settings.temp_outputs_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{uuid.uuid4().hex[:8]}_{safe}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{settings.browser_url.rstrip('/')}/artifact",
                params={"token": token}, headers=_headers(),
            )
            resp.raise_for_status()
            target.write_bytes(resp.content)
    except Exception as e:  # noqa: BLE001
        logger.warning("browser_artifact_pull_failed", extra={"token": token, "error": str(e)})
        return None
    return target
