"""
Speda Browser — the eyes. A Playwright service in its own container.

Same shape as packages/sandbox: a small HTTP service the API talks to over the
internal Docker network, isolated from Igor's secrets and never published to the
internet (CLAUDE.md Security — the CVE-2025-9611 rule applies to any Playwright
surface, not just the upstream MCP server).

Why this exists rather than @playwright/mcp
-------------------------------------------
The upstream MCP server drives a browser fine, but a portal login through it
means the MODEL types the owner's password: it lands in the transcript, the
message table, the embedding index and the memory pipeline. That is not a
recoverable mistake. Here the credential travels Igor → this service → the page
and is never part of a completion. `/login` is the only endpoint that accepts a
password, and it never echoes one back.

The second reason is state. A student portal is worth automating exactly because
you stay logged in; storage_state is persisted per PROFILE (a named cookie jar
on the state volume), so `read` and `act` against `profile=obs` arrive already
authenticated for weeks, and the login only re-runs when the site says so.

Three verbs
-----------
  POST /read    one-shot: render a URL, hand back readable text + links.
                This is the plan-B fetch — a page that needs JS, or that
                refuses a plain HTTP client, is still readable here.
  POST /act     drive a live session: a list of steps, then a snapshot of
                where you ended up. Sessions persist between calls so a
                multi-page flow (login → grades → download) is one thread.
  POST /login   sign into a saved portal. Credentials in, verdict out.

Page representation
-------------------
Every response carries an ARIA snapshot (`page.aria_snapshot()`, Playwright
1.62) alongside the text. It is the compact, semantic view of what is on screen
— `- button "Giriş"`, `- link "Not Listesi"` — and it maps one-to-one onto the
selectors `/act` accepts (`role=button[name="Giriş"]`), so what the model reads
is directly what it can click. A DOM dump would be larger and less actionable.

Isolation
---------
No host mounts except its own state volume, no access to Igor's .env or
database, resource-limited, internal network only. It holds cookies for the
owner's portals, which is exactly why BROWSER_TOKEN gates every request: on a
shared Docker network "reachable" is not the same as "authorized".
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("browser")

STATE_DIR = Path(os.environ.get("BROWSER_STATE_DIR", "/state"))
ARTIFACT_DIR = STATE_DIR / "artifacts"
TOKEN = os.environ.get("BROWSER_TOKEN", "")
# Set only when the owner deliberately opted this container into it (see
# solve_captcha_with_vision's docstring for why it lives here rather than in
# Igor, despite the isolation rule this bends). Absent, captcha solving falls
# straight back to the OCR path with no behavior change.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip().strip('"').strip("'")
PORT = int(os.environ.get("BROWSER_PORT", "9200"))
HEADLESS = os.environ.get("BROWSER_HEADLESS", "1") != "0"

# Ceilings. Every one of these is a cap on what can reach a model's context or
# fill a disk, not a guess about what a page needs.
MAX_TEXT = 30_000          # readable text handed back per call
MAX_ARIA = 14_000          # aria snapshot — the clickable surface
MAX_LINKS = 80
MAX_STEPS = 25             # per /act call; a longer flow is several calls
NAV_TIMEOUT = 45_000       # ms — a navigation, which is allowed to be slow
# An element interaction is not. A selector that does not match waits out the
# whole timeout before saying so, and 45 seconds of that is 45 seconds the model
# spends learning it guessed wrong — on a page it can re-read in one. A control
# that genuinely takes longer than this to appear wants an explicit wait_for
# step, which is the caller SAYING it expects to wait rather than every wrong
# guess paying for the possibility.
ACT_TIMEOUT = 12_000       # ms
SESSION_IDLE = 900         # seconds a live session survives without use
ARTIFACT_TTL = 3 * 3600    # seconds a download/screenshot stays fetchable

# A real desktop Chrome UA. Not evasion — the default Playwright UA carries
# "HeadlessChrome", and a number of university portals serve a broken or
# blank page to it, which reads as "the tool is broken" rather than "the site
# refused us".
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

# Same spirit as the UA override above, not "evasion" in the adversarial
# sense — these are the owner's own accounts, and this container exists
# specifically to sign into them. But `navigator.webdriver`, a missing
# `window.chrome`, and an empty `navigator.plugins` are the three signals
# every headless-Chromium detector checks first, and a real Chrome window
# never trips any of them. Left unpatched, a portal can reject an otherwise
# byte-for-byte-correct login (right password, right captcha, well inside any
# session window) for no reason a response body ever names — see OBS, which
# did exactly that. Run before any page script via add_init_script.
_STEALTH_INIT_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' })),
});
Object.defineProperty(navigator, 'languages', { get: () => ['tr-TR', 'tr', 'en-US', 'en'] });
const origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (origQuery) {
  window.navigator.permissions.query = (params) => (
    params && params.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : origQuery(params)
  );
}
"""

_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _state_path(profile: str) -> Path:
    return STATE_DIR / "profiles" / f"{profile}.json"


# ── The browser, one per process ─────────────────────────────────────────────


class Engine:
    """Owns the Playwright process, the shared browser, and live sessions.

    One Chromium for the container; a CONTEXT per session, because a context is
    the cookie jar. Two profiles sharing a context would share a login, which on
    a portal means the second one silently acts as the first.
    """

    def __init__(self) -> None:
        self._pw = None
        self.browser: Browser | None = None
        # No lock guarding this. Every mutation is a dict pop or assign between
        # awaits, and the concurrent case that exists — two read calls, which
        # browse_page allows because it is read-only — never touches it: a read
        # builds its own context and closes it in a finally.
        self.sessions: dict[str, dict] = {}

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(
            headless=HEADLESS,
            # The container is the isolation boundary here (no host mounts, no
            # secrets, dropped capabilities, internal network). Chromium's own
            # sandbox additionally wants user-namespace cloning, which Docker's
            # default seccomp profile denies; running with it enabled and no
            # profile just fails to start. See packages/browser/README.md for
            # the seccomp upgrade path if this ever visits genuinely hostile
            # ground rather than the owner's portals and news sites.
            chromium_sandbox=False,
            args=["--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-blink-features=AutomationControlled"],
        )
        (STATE_DIR / "profiles").mkdir(parents=True, exist_ok=True)
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("browser_started headless=%s", HEADLESS)

    async def stop(self) -> None:
        for sid in list(self.sessions):
            await self.close_session(sid)
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()

    # ── contexts ────────────────────────────────────────────────────────────

    async def new_context(self, profile: str | None, locale: str) -> BrowserContext:
        assert self.browser is not None
        state = None
        if profile:
            path = _state_path(profile)
            if path.exists():
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                except Exception as e:  # noqa: BLE001
                    logger.warning("profile_state_unreadable profile=%s err=%s", profile, e)
        context = await self.browser.new_context(
            storage_state=state,
            user_agent=UA,
            locale=locale,
            timezone_id=os.environ.get("TZ", "Europe/Istanbul"),
            viewport={"width": 1440, "height": 900},
            accept_downloads=True,
        )
        await context.add_init_script(_STEALTH_INIT_JS)
        return context

    async def save_profile(self, context: BrowserContext, profile: str) -> None:
        """Persist the cookie jar. Called after every profile-bound call, not
        only after login — a portal that rotates its session cookie mid-visit
        would otherwise log us out the moment we stop looking."""
        if not profile:
            return
        try:
            path = _state_path(profile)
            path.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(path))
        except Exception as e:  # noqa: BLE001
            logger.warning("profile_save_failed profile=%s err=%s", profile, e)

    # ── sessions ────────────────────────────────────────────────────────────

    async def get_session(self, session_id: str | None, profile: str | None,
                          locale: str) -> tuple[str, dict]:
        await self.reap()
        if session_id and session_id in self.sessions:
            sess = self.sessions[session_id]
            sess["seen"] = time.time()
            return session_id, sess
        context = await self.new_context(profile, locale)
        page = await context.new_page()
        page.set_default_timeout(ACT_TIMEOUT)
        # A fresh id even when the caller named a dead one. Handing back the id
        # it asked for would say "still on your tab" about a blank page nine
        # menus back from where the model thinks it is; a changed id is the
        # signal that the thread was lost.
        sid = uuid.uuid4().hex[:12]
        sess = {
            "context": context, "page": page, "profile": profile or "",
            "seen": time.time(), "downloads": [], "errors": [],
        }
        page.on("console", lambda m: sess["errors"].append(m.text[:200])
                if m.type == "error" and len(sess["errors"]) < 10 else None)
        page.on("download", lambda d: asyncio.create_task(self._keep(sess, d)))
        self.sessions[sid] = sess
        return sid, sess

    async def _keep(self, sess: dict, download) -> None:
        """A file the page handed us. Parked as an artifact so Igor can fetch it
        and turn it into a download card — the point of automating a portal is
        usually the PDF at the end of it."""
        try:
            token = uuid.uuid4().hex
            name = download.suggested_filename or "download"
            target = ARTIFACT_DIR / f"{token}__{_safe(name)}"
            await download.save_as(str(target))
            sess["downloads"].append({
                "token": token, "name": name,
                "size": target.stat().st_size, "url": download.url,
            })
            logger.info("download_captured name=%s", name)
        except Exception as e:  # noqa: BLE001
            logger.warning("download_failed err=%s", e)

    async def close_session(self, session_id: str) -> bool:
        sess = self.sessions.pop(session_id, None)
        if not sess:
            return False
        try:
            await self.save_profile(sess["context"], sess["profile"])
            await sess["context"].close()
        except Exception:  # noqa: BLE001
            pass
        return True

    async def reap(self) -> None:
        cutoff = time.time() - SESSION_IDLE
        for sid, sess in list(self.sessions.items()):
            if sess["seen"] < cutoff:
                logger.info("session_reaped id=%s", sid)
                await self.close_session(sid)
        cutoff = time.time() - ARTIFACT_TTL
        for f in ARTIFACT_DIR.glob("*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


engine = Engine()


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120] or "file"


# ── Reading a page ───────────────────────────────────────────────────────────

# Runs in the page. Returns the readable text and the links.
#
# It reads innerText off the LIVE element, never a clone. innerText is defined
# in terms of layout — it is what a user would get by selecting the page and
# copying — which is exactly the property we want, and exactly the property a
# detached clone does not have: cloneNode gives you a node that was never laid
# out, so innerText silently degrades to textContent and every block boundary
# disappears. "Example DomainThis domain is for use in…" is what that looks
# like, and it survives review because it is not empty, just wrong.
#
# Reading live also means script, style, noscript and anything display:none are
# already excluded — they are not rendered, so innerText never saw them. No
# scrubbing needed, and none is done: dropping <nav> would put this extractor
# out of step with services/web_watch.py's extract_lines(), which keeps it, and
# those two feed one snapshot.
_EXTRACT_JS = """
() => {
  const scope = document.querySelector('main, article, [role=main]') || document.body;
  if (!scope) return { text: '', links: [] };
  const text = (scope.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim();
  const links = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const label = (a.innerText || a.getAttribute('aria-label') || '').trim();
    const href = a.href;
    if (!href || href.startsWith('javascript:')) continue;
    const key = label + '|' + href;
    if (seen.has(key)) continue;
    seen.add(key);
    links.push({ text: label.slice(0, 120), href });
  }
  return { text, links };
}
"""


async def snapshot(page: Page, want_aria: bool = True) -> dict:
    """What the model gets back: where it is, what it can read, what it can click.

    `has_password` is the one field here that is a FACT rather than a rendering.
    Whether a page is a login wall is the question the whole portal flow turns
    on, and everything else available to answer it is a guess: the URL says
    /login on plenty of signed-in pages, and reading the ARIA tree for the word
    "şifre" fails on a portal that labels the box with an icon, or in English, or
    not at all. Playwright can simply be asked, from the side of the wire that
    has the DOM, so it is — and the answer travels with every response.
    """
    out: dict[str, Any] = {"url": page.url, "title": ""}
    try:
        out["title"] = (await page.title())[:200]
    except Exception:  # noqa: BLE001
        pass
    out["has_password"] = await find_login_frame(page) is not None
    try:
        data = await page.evaluate(_EXTRACT_JS)
        out["text"] = (data.get("text") or "")[:MAX_TEXT]
        out["links"] = (data.get("links") or [])[:MAX_LINKS]
    except Exception as e:  # noqa: BLE001
        out["text"], out["links"] = "", []
        out["extract_error"] = str(e)[:200]
    if want_aria:
        try:
            out["aria"] = (await page.locator("body").aria_snapshot())[:MAX_ARIA]
        except Exception:  # noqa: BLE001
            out["aria"] = ""
    return out


async def settle(page: Page, wait_for: str | None, wait_ms: int) -> None:
    """Give the page a chance to finish being a page.

    networkidle is best-effort on purpose: a portal with a polling widget or a
    live chat bubble never goes idle, and waiting for that would time out every
    call on a page that was ready in 400ms.
    """
    if wait_for:
        try:
            await page.wait_for_selector(wait_for, timeout=NAV_TIMEOUT)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        await page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:  # noqa: BLE001
        pass
    if wait_ms:
        await page.wait_for_timeout(min(wait_ms, 15_000))


# ── Steps — the vocabulary /act speaks ───────────────────────────────────────


async def run_step(page: Page, step: dict, sess: dict | None = None) -> str:
    """Execute one step, return a one-line account of it.

    `target` is any Playwright selector, and the ones worth reaching for are the
    semantic ones the aria snapshot already named: `role=button[name="Giriş"]`,
    `text=Not Listesi`. A CSS selector works too, and is the right answer when
    the page has ids.
    """
    if not isinstance(step, dict):
        raise ValueError(f"a step must be an object, got {type(step).__name__}")
    action = (step.get("action") or "").strip().lower()
    target = step.get("target") or ""
    value = step.get("value")

    if action == "goto":
        resp = await page.goto(target or value or "", wait_until="domcontentloaded",
                               timeout=NAV_TIMEOUT)
        return f"goto {page.url} → {resp.status if resp else '?'}"
    if action == "click":
        await page.locator(target).first.click(timeout=ACT_TIMEOUT)
        return f"click {target}"
    if action == "fill":
        loc = page.locator(target).first
        # Same readonly-until-focus trick do_login guards against — a click
        # first costs nothing on a normal field and unblocks a gated one.
        try:
            await loc.click(timeout=ACT_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass
        await loc.fill(str(value or ""), timeout=ACT_TIMEOUT)
        return f"fill {target}"
    if action == "select":
        await page.locator(target).first.select_option(str(value or ""), timeout=ACT_TIMEOUT)
        return f"select {target} = {value}"
    if action == "check":
        await page.locator(target).first.check(timeout=ACT_TIMEOUT)
        return f"check {target}"
    if action == "press":
        if target:
            await page.locator(target).first.press(str(value or "Enter"))
        else:
            await page.keyboard.press(str(value or "Enter"))
        return f"press {value or 'Enter'}"
    if action == "hover":
        await page.locator(target).first.hover(timeout=ACT_TIMEOUT)
        return f"hover {target}"
    if action == "scroll":
        await page.mouse.wheel(0, int(value or 900))
        return f"scroll {value or 900}"
    if action == "wait_for":
        await page.wait_for_selector(target, timeout=NAV_TIMEOUT)
        return f"wait_for {target}"
    if action == "wait":
        await page.wait_for_timeout(min(int(value or 1000), 15_000))
        return f"wait {value or 1000}ms"
    if action == "back":
        await page.go_back(timeout=NAV_TIMEOUT)
        return f"back → {page.url}"
    if action == "screenshot":
        token = uuid.uuid4().hex
        name = "screenshot.png"
        path = ARTIFACT_DIR / f"{token}__{name}"
        await page.screenshot(path=str(path), full_page=bool(value))
        # Rides out with the downloads, because that is the only channel that
        # reaches the owner. A token in a log line is a picture nobody sees.
        if sess is not None:
            sess["downloads"].append({
                "token": token, "name": name,
                "size": path.stat().st_size, "url": page.url,
            })
        return f"screenshot of {page.url}"
    raise ValueError(
        f"unknown action '{action}' — use goto, click, fill, select, check, press, "
        f"hover, scroll, wait, wait_for, back, screenshot"
    )


# ── Login — the only endpoint that sees a password ───────────────────────────

# Autodetection, because asking the owner for CSS selectors is asking them to
# open devtools on their own student portal. A login form is the one form on the
# web with a reliable shape: exactly one password box, and the text box above it
# is the username. Explicit selectors stay available for the pages that aren't.
_PASSWORD = "input[type=password]"
_USERNAME_HINTS = (
    "input[type=email]", "input[name*=user i]", "input[name*=kullanic i]",
    "input[id*=user i]", "input[name*=login i]", "input[name*=email i]",
    "input[type=text]", "input:not([type])",
)
_SUBMIT_HINTS = (
    "button[type=submit]", "input[type=submit]", "role=button[name=/giriş|login|sign in|oturum/i]",
    # Plenty of WebForms-era portals style their login control as an <a>
    # driving __doPostBack rather than a real <button> — no type=submit, no
    # explicit role=button, so none of the hints above ever see it. Matched
    # by text before the bare tag-name fallbacks below get a chance to grab
    # the first UNRELATED <button> on the page (a password show/hide toggle,
    # on OBS) and report a false "submitted".
    "a:text-matches(\"giriş|log ?in|sign in|oturum aç\", \"i\")",
    "button", "input[type=button]",
)


async def find_login_frame(page: Page):
    """The frame holding the password box. Portals built on ASP.NET WebForms and
    a surprising number of SSO pages put the form in an iframe; looking only at
    the top document is why "the selector isn't there" is the classic login-bot
    failure."""
    for frame in [page.main_frame, *page.frames]:
        try:
            if await frame.locator(_PASSWORD).count():
                return frame
        except Exception:  # noqa: BLE001
            continue
    return None


async def first_visible(frame, selectors: tuple[str, ...] | list[str]):
    for sel in selectors:
        try:
            loc = frame.locator(sel).first
            if await loc.count() and await loc.is_visible():
                return loc, sel
        except Exception:  # noqa: BLE001
            continue
    return None, ""


# ── The arithmetic captcha a surprising number of Turkish university portals
# gate their login behind (OSTİM's OBS among them): a "what's N + M" challenge
# rendered as an image, answered as a plain number. Not a security captcha in
# any real sense — it stops a form-post bot, not a browser that can read an
# image — and the owner already proved they're allowed through the door by
# supplying the password. Reading it is squarely within what portal_login is
# for: getting the owner's own account signed in, not defeating someone else's
# access control.
_CAPTCHA_IMG_HINTS = (
    "img[id*=captcha i]", "img[id*=guvkod i]", "img[id*=güvkod i]",
    "img[alt*=güvenlik i]", "img[alt*=captcha i]",
    "img[title*=güvenlik i]", "img[title*=captcha i]",
)
_CAPTCHA_INPUT_HINTS = (
    "input[id*=seccode i]", "input[name*=seccode i]",
    "input[id*=captcha i]", "input[name*=captcha i]",
)


async def find_captcha(frame):
    img = None
    for sel in _CAPTCHA_IMG_HINTS:
        try:
            loc = frame.locator(sel).first
            if await loc.count():
                img = loc
                break
        except Exception:  # noqa: BLE001
            continue
    if img is None:
        return None, None
    inp = None
    for sel in _CAPTCHA_INPUT_HINTS:
        try:
            loc = frame.locator(sel).first
            if await loc.count():
                inp = loc
                break
        except Exception:  # noqa: BLE001
            continue
    return img, inp


async def solve_captcha_with_vision(png: bytes) -> str | None:
    """Read a "N + M = ?" captcha image with a vision-capable model and
    return the sum as a string, or None on any failure.

    This is the primary solver, not a fallback: the noise these images carry
    (wavy background lines, colored circles overlapping the digits — see
    OSTİM's OBS) is specifically the kind of thing pixel-level OCR struggles
    with and a vision model reads past without effort, the same way a human
    glancing at the image does. It also runs from THIS container rather than
    round-tripping to Igor and back — some portals expire the login session
    faster than that round trip takes, so solving has to happen in the same
    breath as the capture and the submit. That is the whole reason
    OPENAI_API_KEY is configured here at all, bending the "this container
    holds no secrets" isolation rule as a deliberate, narrow exception.
    """
    if not OPENAI_API_KEY:
        return None
    import base64
    try:
        import httpx
    except ImportError:
        return None
    b64 = base64.b64encode(png).decode()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": (
                                "This is a captcha image showing an arithmetic "
                                "expression like 'N + M = ?'. Reply with ONLY the "
                                "computed numeric answer, nothing else — no words, "
                                "no punctuation."
                            )},
                            {"type": "image_url", "image_url": {
                                "url": f"data:image/png;base64,{b64}"
                            }},
                        ],
                    }],
                    "max_tokens": 10,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        text = (data["choices"][0]["message"]["content"] or "").strip()
        digits = re.search(r"-?\d{1,4}", text)
        return digits.group(0) if digits else None
    except Exception as e:  # noqa: BLE001
        logger.warning("captcha_vision_failed err=%s", str(e)[:200])
        return None


_CLICKABLE = "button, input[type=submit], input[type=button], a[href]"


async def choose_element_by_reasoning(frame, description: str):
    """Read every clickable element's own text/attributes and have a model
    pick which one matches `description`, by understanding, not by pixels.

    CSS-hint lists (_SUBMIT_HINTS and friends) encode an assumption about
    markup — button vs. anchor, a type= attribute, an ARIA role someone
    remembered to set — and a portal is free to change any of that without
    changing what a human reads. OSTİM's OBS renders its login control as a
    plain <a> driving a postback, which matched none of the "real button"
    hints and let a later hint grab an unrelated <button> instead (the
    password show/hide toggle).

    A first attempt at the general fix asked a vision model to POINT at the
    control in a screenshot — that failed too, off by over 100px on this
    exact page, because pixel-grounding is a known weak spot for general
    vision-chat models even when reading the same page's TEXT is trivial for
    them. This asks a strictly easier question: given each candidate's own
    label, does IT read as the login button — text classification, not
    spatial guessing, so it plays to the model's actual strength.
    """
    if not OPENAI_API_KEY:
        return None
    try:
        import httpx
    except ImportError:
        return None

    candidates = frame.locator(_CLICKABLE)
    try:
        count = await candidates.count()
    except Exception:  # noqa: BLE001
        return None
    if count == 0:
        return None

    rows = []
    locs = []
    for i in range(min(count, 40)):
        loc = candidates.nth(i)
        try:
            if not await loc.is_visible():
                continue
            text = (await loc.inner_text()).strip()[:60]
            aria = (await loc.get_attribute("aria-label") or "")[:60]
            title = (await loc.get_attribute("title") or "")[:60]
            tag = await loc.evaluate("el => el.tagName.toLowerCase()")
        except Exception:  # noqa: BLE001
            continue
        rows.append(f"{len(locs)}: <{tag}> text={text!r} aria-label={aria!r} title={title!r}")
        locs.append(loc)
    if not rows:
        return None

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{
                        "role": "user",
                        "content": (
                            "Clickable elements on a web page, one per line as "
                            "index: <tag> text=... aria-label=... title=...\n\n"
                            + "\n".join(rows)
                            + f"\n\nWhich index is: {description}? "
                            "Reply with ONLY the index number, or -1 if none match."
                        ),
                    }],
                    "max_tokens": 10,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            text = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        m = re.search(r"-?\d+", text)
        if not m:
            return None
        idx = int(m.group(0))
        if idx < 0 or idx >= len(locs):
            return None
        return locs[idx]
    except Exception as e:  # noqa: BLE001
        logger.warning("choose_element_failed err=%s", str(e)[:200])
        return None


def solve_arithmetic_captcha(png: bytes) -> str | None:
    """Read a two-number "N + M = ?" captcha image and return the sum as a
    string, or None if it couldn't be read with any confidence.

    This style renders the digits in a flat, desaturated color and buries them
    in colored noise circles and wavy lines — the noise is what defeats a
    plain OCR pass, and it is also the thing that gives the digits away: it is
    saturated where the digits are not. Thresholding on saturation isolates
    the glyphs before Tesseract ever sees the image, which is doing the actual
    work here — not the OCR step.
    """
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return None
    try:
        im = Image.open(io.BytesIO(png)).convert("RGB")
        px = im.load()
        mask = Image.new("L", im.size, 255)
        mpx = mask.load()
        for y in range(im.height):
            for x in range(im.width):
                r, g, b = px[x, y]
                hi, lo = max(r, g, b), min(r, g, b)
                if hi - lo < 40 and hi < 200:   # low saturation, not near-white
                    mpx[x, y] = 0
        text = pytesseract.image_to_string(
            mask, config="--psm 7 -c tessedit_char_whitelist=0123456789+-x*="
        )
        m = re.search(r"(\d{1,3})\s*([+\-x*])\s*(\d{1,3})", text)
        if not m:
            return None
        a, op, b_ = int(m.group(1)), m.group(2), int(m.group(3))
        if op == "+":
            return str(a + b_)
        if op == "-":
            return str(a - b_)
        return str(a * b_)
    except Exception:  # noqa: BLE001
        return None


async def do_login(page: Page, spec: dict, skip_navigate: bool = False) -> dict:
    """Fill the form, submit, and decide honestly whether we're in.

    The verdict matters more than the mechanics. "Still on a page with a
    password box" is the one signal that survives every portal's habit of
    answering a bad password with HTTP 200 and a red span, so it is what we
    check first — before any success selector the owner configured.

    `skip_navigate` is set when this is the second half of a captcha-audio
    round trip: the caller already has a live session sitting on the login
    page with a specific challenge loaded, and a fresh goto would swap that
    challenge out from under the answer it just transcribed.
    """
    steps: list[str] = []
    login_url = spec.get("login_url") or ""
    if login_url and not skip_navigate:
        await page.goto(login_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        steps.append(f"opened {login_url}")
    await settle(page, None, 600)

    frame = await find_login_frame(page)
    if frame is None:
        # Already signed in is the happy version of "no password box".
        snap = await snapshot(page)
        return {
            "ok": True, "already": True,
            "message": "No password field on the page — the stored session looks live.",
            "steps": steps, **snap,
        }

    sel = spec.get("selectors") or {}
    user_sel = sel.get("username")
    pass_sel = sel.get("password") or _PASSWORD
    submit_sel = sel.get("submit")

    if user_sel:
        user_loc = frame.locator(user_sel).first
    else:
        user_loc, user_sel = await first_visible(frame, _USERNAME_HINTS)
    if user_loc is None:
        return {"ok": False, "message": "Found a password box but no username field. "
                                        "Set the username selector on the portal.",
                "steps": steps, **(await snapshot(page))}

    pass_loc = frame.locator(pass_sel).first
    # Some WebForms-style portals ship the password box `readonly` and only drop
    # that attribute on focus/mouseover (an anti-autofill trick, not a real
    # disable — see e.g. OBS's txtParamT02). fill() requires "editable" and
    # times out against it forever; a click first is what a real visit does
    # anyway, and it's what fires the listener that unlocks the field.
    try:
        await user_loc.click(timeout=ACT_TIMEOUT)
    except Exception:  # noqa: BLE001
        pass
    try:
        await user_loc.fill(spec.get("username") or "", timeout=ACT_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"Could not fill the username field "
                                        f"({type(e).__name__}).",
                "steps": steps, **(await snapshot(page))}
    try:
        await pass_loc.click(timeout=ACT_TIMEOUT)
    except Exception:  # noqa: BLE001
        pass
    try:
        await pass_loc.fill(spec.get("password") or "", timeout=ACT_TIMEOUT)
    except Exception:  # noqa: BLE001
        # Playwright's own TimeoutError embeds the fill() argument — the
        # password itself — in its call log. `e` is deliberately never touched
        # below: not logged, not included in the response, not re-raised.
        return {"ok": False, "message": "The password field never became "
                "editable in time — the portal may gate it behind a "
                "focus/click it didn't get, or blocked the session outright.",
                "steps": steps, **(await snapshot(page))}
    steps.append(f"filled {user_sel} + password")

    cap_img, cap_inp = await find_captcha(frame)
    if cap_img is not None and cap_inp is not None:
        answer, source = None, ""
        if spec.get("captcha_answer"):
            answer, source = str(spec["captcha_answer"]), "supplied"
        else:
            try:
                answer = await solve_captcha_with_vision(await cap_img.screenshot())
                source = "vision"
            except Exception:  # noqa: BLE001
                pass
        if answer is None:
            try:
                answer = solve_arithmetic_captcha(await cap_img.screenshot())
                source = "OCR best-effort"
            except Exception:  # noqa: BLE001
                pass

        if answer:
            try:
                await cap_inp.click(timeout=ACT_TIMEOUT)
                await cap_inp.fill(answer, timeout=ACT_TIMEOUT)
                steps.append(f"filled captcha answer via {source}")
            except Exception:  # noqa: BLE001
                steps.append("read the captcha but could not fill the answer field")
        else:
            steps.append("a captcha is present and could not be solved — "
                         "login will likely fail")

    # Extra fields — a student number, a captcha answer the owner pre-solved, a
    # department dropdown. Anything the page wants that isn't the two obvious.
    for extra_sel, extra_val in (spec.get("extra_fields") or {}).items():
        try:
            await frame.locator(extra_sel).first.fill(str(extra_val), timeout=10_000)
            steps.append(f"filled {extra_sel}")
        except Exception as e:  # noqa: BLE001
            steps.append(f"extra field {extra_sel} failed: {e}")

    # Finding the submit control by markup is exactly the kind of guess that
    # breaks the moment a portal's template changes — no type=submit, no
    # role=button, and the next heuristic down the list is free to grab an
    # unrelated <button> that happens to render first (OBS's password
    # show/hide toggle did precisely that). Reading every candidate's own
    # label and having a model choose by understanding it — not by pixel
    # position, see choose_element_by_reasoning's docstring for why — is the
    # primary path whenever a key is configured; the CSS hints are the
    # fallback for when it isn't, not the other way around.
    submit_sel_desc = None
    if submit_sel:
        submit, submit_sel = frame.locator(submit_sel).first, submit_sel
    elif OPENAI_API_KEY:
        submit = await choose_element_by_reasoning(
            frame, "the login/submit control that signs the user in (e.g. "
                   "'Giriş', 'Login', 'Sign in') — NOT a password show/hide "
                   "icon, NOT a language switcher, NOT a forgot-password link"
        )
        submit_sel_desc = "chosen by reasoning over labeled candidates"
        if submit is None:
            submit, submit_sel = await first_visible(frame, _SUBMIT_HINTS)
    else:
        submit, submit_sel = await first_visible(frame, _SUBMIT_HINTS)
    try:
        if submit is not None:
            await submit.click(timeout=ACT_TIMEOUT)
            steps.append(f"submitted via {submit_sel_desc or submit_sel}")
        else:
            await frame.locator(pass_sel).first.press("Enter")
            steps.append("submitted via Enter")
    except Exception as e:  # noqa: BLE001
        steps.append(f"submit click failed ({e}) — pressing Enter")
        await frame.locator(pass_sel).first.press("Enter")

    await settle(page, spec.get("success_selector"), 1500)

    still_there = await find_login_frame(page) is not None
    ok = not still_there
    reason = ""
    if ok and spec.get("success_selector"):
        try:
            ok = await page.locator(spec["success_selector"]).count() > 0
            reason = "" if ok else "signed in, but the success selector never appeared"
        except Exception:  # noqa: BLE001
            pass
    if ok and spec.get("success_url_contains"):
        ok = spec["success_url_contains"].lower() in page.url.lower()
        if not ok:
            reason = f"landed on {page.url}, which does not contain " \
                     f"'{spec['success_url_contains']}'"
    if still_there:
        reason = ("still on a page with a password field — wrong credentials, a "
                  "captcha, or an extra field the form wants")

    snap = await snapshot(page)
    return {"ok": ok, "already": False, "steps": steps,
            "message": "Signed in." if ok else f"Login did not complete: {reason}", **snap}


# ── HTTP surface ─────────────────────────────────────────────────────────────

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    await engine.start()
    try:
        yield
    finally:
        await engine.stop()


app = FastAPI(title="Speda Browser", docs_url=None, redoc_url=None, lifespan=lifespan)


def check(token: str | None) -> None:
    if TOKEN and token != TOKEN:
        raise HTTPException(status_code=401, detail="bad or missing X-Browser-Token")


def check_profile(profile: str | None) -> str:
    profile = (profile or "").strip().lower()
    if profile and not _PROFILE_RE.match(profile):
        raise HTTPException(status_code=400, detail="invalid profile name")
    return profile


@app.get("/health")
async def health(x_browser_token: str | None = Header(default=None)):
    """Liveness, unauthenticated so a container healthcheck works without the
    secret. The PROFILE NAMES need the token: they are the list of sites the
    owner has an account on, which is not much, and is more than nothing."""
    out = {
        "status": "ok" if engine.browser and engine.browser.is_connected() else "down",
        "sessions": len(engine.sessions),
    }
    if not TOKEN or x_browser_token == TOKEN:
        out["profiles"] = sorted(p.stem for p in (STATE_DIR / "profiles").glob("*.json")) \
            if (STATE_DIR / "profiles").exists() else []
    return out


@app.post("/read")
async def read(body: dict, x_browser_token: str | None = Header(default=None)):
    """Render one URL and hand back what it says. No session survives the call.

    This is the endpoint the research fallback uses: a single page, fully
    rendered, with the cookies of a profile if one is named.
    """
    check(x_browser_token)
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    profile = check_profile(body.get("profile"))
    locale = body.get("locale") or "tr-TR"

    context = await engine.new_context(profile or None, locale)
    try:
        page = await context.new_page()
        page.set_default_timeout(ACT_TIMEOUT)
        status = None
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            status = resp.status if resp else None
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": f"could not load {url}: {e}"}, status_code=200)
        await settle(page, body.get("wait_for"), int(body.get("wait_ms") or 0))
        snap = await snapshot(page, want_aria=bool(body.get("aria", False)))
        snap["status"] = status
        if body.get("screenshot"):
            token = uuid.uuid4().hex
            await page.screenshot(path=str(ARTIFACT_DIR / f"{token}__screen.png"),
                                  full_page=False)
            snap["screenshot"] = token
        await engine.save_profile(context, profile)
        return snap
    finally:
        await context.close()


@app.post("/act")
async def act(body: dict, x_browser_token: str | None = Header(default=None)):
    """Run steps against a live session and report where they landed."""
    check(x_browser_token)
    profile = check_profile(body.get("profile"))
    steps = body.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise HTTPException(status_code=400, detail="steps must be a non-empty list")
    if len(steps) > MAX_STEPS:
        raise HTTPException(status_code=400, detail=f"at most {MAX_STEPS} steps per call")

    sid, sess = await engine.get_session(body.get("session_id"), profile or None,
                                         body.get("locale") or "tr-TR")
    page: Page = sess["page"]
    sess["downloads"] = []
    sess["errors"] = []

    log: list[str] = []
    failed = None
    for step in steps:
        try:
            log.append(await run_step(page, step, sess))
        except Exception as e:  # noqa: BLE001
            failed = f"{step} → {type(e).__name__}: {str(e)[:300]}"
            break

    await settle(page, body.get("wait_for"), int(body.get("wait_ms") or 0))
    # A download lands through an event handler that may still be writing when
    # the last step returns. Only worth waiting for after something that could
    # have started one.
    if any(isinstance(s, dict) and (s.get("action") or "").lower() in ("click", "press")
           for s in steps):
        await page.wait_for_timeout(600)
    snap = await snapshot(page)
    await engine.save_profile(sess["context"], sess["profile"])
    return {
        "session_id": sid, "performed": log, "failed": failed,
        "downloads": sess["downloads"], "console_errors": sess["errors"][:5],
        **snap,
    }


@app.post("/login")
async def login(body: dict, x_browser_token: str | None = Header(default=None)):
    """Sign a profile in. The only endpoint that takes a password, and it is
    never written to a log, a response, or the profile's state file."""
    check(x_browser_token)
    profile = check_profile(body.get("profile"))
    if not profile:
        raise HTTPException(status_code=400, detail="profile is required")
    wants_sid = (body.get("session_id") or "").strip() or None
    if not body.get("login_url") and not wants_sid:
        raise HTTPException(status_code=400, detail="login_url is required")

    sid, sess = await engine.get_session(wants_sid, profile, body.get("locale") or "tr-TR")
    # Continuing the exact session the caller named, not a fresh one reaped
    # under the same id — only then is a re-navigate skippable, per do_login's
    # skip_navigate contract.
    continuing = wants_sid is not None and sid == wants_sid
    try:
        result = await do_login(sess["page"], body, skip_navigate=continuing)
        if result.get("ok"):
            await engine.save_profile(sess["context"], profile)
        logger.info("login profile=%s ok=%s", profile, result.get("ok"))
        result["session_id"] = sid
        return result
    finally:
        # A login session is not a working session — the caller continues with
        # /act, which opens its own with the cookies we just saved.
        if body.get("keep_session") is not True:
            await engine.close_session(sid)


@app.get("/artifact")
async def artifact(token: str = Query(...), x_browser_token: str | None = Header(default=None)):
    """Fetch a captured download or screenshot by token."""
    check(x_browser_token)
    if not re.fullmatch(r"[a-f0-9]{8,40}", token):
        raise HTTPException(status_code=400, detail="bad token")
    for path in ARTIFACT_DIR.glob(f"{token}__*"):
        return FileResponse(str(path), filename=path.name.split("__", 1)[-1])
    raise HTTPException(status_code=404, detail="artifact not found or expired")


@app.post("/forget")
async def forget(body: dict, x_browser_token: str | None = Header(default=None)):
    """Drop a profile's cookies — the sign-out. Live sessions on it go too, or
    the next call would keep using the context we just claimed to have cleared."""
    check(x_browser_token)
    profile = check_profile(body.get("profile"))
    if not profile:
        raise HTTPException(status_code=400, detail="profile is required")
    for sid, sess in list(engine.sessions.items()):
        if sess["profile"] == profile:
            sess["profile"] = ""          # don't re-save on the way out
            await engine.close_session(sid)
    path = _state_path(profile)
    existed = path.exists()
    if existed:
        path.unlink()
    logger.info("profile_forgotten profile=%s existed=%s", profile, existed)
    return {"profile": profile, "cleared": existed}


@app.delete("/session/{session_id}")
async def close_session(session_id: str, x_browser_token: str | None = Header(default=None)):
    check(x_browser_token)
    return {"closed": await engine.close_session(session_id)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
