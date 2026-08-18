"""
The browser — three tools over the Playwright sidecar (packages/browser).

`browse_page` is the one that gets used. It is the plan B beneath every other
way this system reads the web: `fetch`, Tavily, Exa and the news reader all
speak HTTP to a server and take what it says, which is nothing at all on a page
whose content arrives by JavaScript, and a challenge page on a site that has
opinions about clients. Rendering costs seconds where a fetch costs
milliseconds, so it is second in line — but second in line is not last resort,
and a page the owner can see in Chrome should never be a page their assistant
cannot read.

`portal_login` and `browser_act` are the other half: the web the owner is logged
into. A student automation is not a scraping target, it is an account, and the
thing that makes it usable is that the password never becomes text a model
produced. The model names a portal; app/services/browser.py reads the record and
hands it to the container. See app/core/runtime_state.py for the vault.

Rule 11 applies with force here. These three tools overlap with fetch, with
run_command, and with each other, and the only thing standing between the model
and a browser render of a static JSON endpoint is a description that says when
NOT to reach for one.
"""

import logging

from app.config import settings
from app.core.context import AgentContext
from app.services import browser as browser_svc
from app.skills.base import Skill

logger = logging.getLogger(__name__)


def _portal_hint() -> str:
    """The configured portals, appended to a description at registration time.

    Named portals in the tool description are what make `portal_login` findable:
    the model does not know the owner has an `obs` account until something says
    so, and a tool whose argument is an unguessable name is a tool that never
    gets called.
    """
    try:
        rows = browser_svc.portal_catalogue()
    except Exception:  # noqa: BLE001
        return ""
    return f"\n\nPortals configured right now:\n{rows}" if rows else ""


def _unavailable(e: Exception) -> str:
    return str(e)


class BrowsePageSkill(Skill):
    name = "browse_page"
    deferred = True
    search_keywords = (
        "browse open page website url render javascript headless chrome playwright "
        "scrape fetch read site portal login logged in blocked 403 empty page spa "
        "web sayfa aç tarayıcı giriş"
    )
    read_only = True          # Rule 9 — a render is a read; parallel is safe
    requires_network = True
    description = (
        "Opens a URL in a real Chromium browser and returns the page's readable text, "
        "its links, and optionally the list of things on it you could click. Use this "
        "when a plain fetch failed or came back empty or truncated, when the content only "
        "exists after JavaScript runs (dashboards, SPAs, search results, most modern news "
        "and university sites), when a site refused a plain HTTP client, or when the page "
        "is behind one of the owner's logins — pass `portal` and the browser arrives with "
        "that account's cookies, signing in first if the session has expired. Do NOT use it "
        "as your first move for ordinary articles or APIs: `fetch` and the search tools are "
        "far faster and cheaper, and this is the fallback for when they come up short. It "
        "returns the page title, URL, text (truncated), and a list of links you can pass "
        "straight back as the next URL."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full URL to open, including https://."},
            "portal": {
                "type": "string",
                "description": (
                    "Optional. The name of one of the owner's saved portals (e.g. 'obs'). "
                    "The page is then loaded with that account's session, and the browser "
                    "signs in first if it has expired. Leave empty for the public web."
                ),
            },
            "wait_for": {
                "type": "string",
                "description": (
                    "Optional CSS selector to wait for before reading, e.g. 'table.grades'. "
                    "Use when the interesting part of the page loads after the rest."
                ),
            },
            "interactive": {
                "type": "boolean",
                "description": (
                    "Include the ARIA element list (buttons, links, fields with their names). "
                    "Set true when you intend to follow up with browser_act; it costs tokens "
                    "and is useless if you only want to read."
                ),
                "default": False,
            },
        },
        "required": ["url"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        url = (args.get("url") or "").strip()
        if not url:
            return "No URL given."
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        portal = (args.get("portal") or "").strip().lower()
        interactive = bool(args.get("interactive"))

        try:
            if portal:
                from app.core.runtime_state import get_portal

                record = get_portal(portal)
                if not record:
                    return (f"No portal called '{portal}'. Known portals: "
                            f"{', '.join(browser_svc.portal_names()) or '(none configured)'}. "
                            f"The owner adds them in Settings → Connections → Web portals.")
                if not browser_svc.portal_allows(record, context.agent_id):
                    return (f"The '{portal}' portal is not shared with {context.agent_id}. "
                            f"The owner restricted it to: "
                            f"{', '.join(record.get('allowed_agents') or [])}.")
                outcome = await browser_svc.ensure_logged_in(portal, probe_url=url)
                if not outcome.get("ok"):
                    return (f"Could not get into '{portal}': {outcome.get('message')}\n"
                            f"The owner may need to re-check the credentials in "
                            f"Settings → Connections → Web portals.")
                page = outcome["page"]
            else:
                page = await browser_svc.render(
                    url, wait_for=args.get("wait_for") or None, aria=interactive
                )
        except browser_svc.BrowserUnavailable as e:
            return _unavailable(e)

        if page.get("error"):
            return f"The browser could not load that page: {page['error']}"

        logger.info("browse_page", extra={
            "request_id": context.request_id, "url": page.get("url"),
            "portal": portal, "chars": len(page.get("text") or ""),
        })
        return browser_svc.format_page(page, include_aria=interactive)


class BrowserActSkill(Skill):
    name = "browser_act"
    deferred = True
    search_keywords = (
        "click type fill form button submit navigate browser session interact "
        "download portal automation login student grades transcript multi-step "
        "tıkla doldur form indir"
    )
    read_only = False
    requires_network = True
    description = (
        "Drives a live browser session: performs a short list of steps (click, fill, "
        "select, press, scroll, wait, goto, back, screenshot) and returns the page you "
        "ended up on, including any file the site downloaded. Use it when reading is not "
        "enough — working through a portal's menus, submitting a search form, opening a "
        "grade or transcript page, downloading a PDF the site only hands out after a click. "
        "Call browse_page first with interactive=true to see the elements and their names, "
        "then target them with the selectors it showed you (role=button[name=\"Giriş\"], "
        "text=Not Listesi, or a CSS selector like #btnLogin). Do NOT use it to read a page "
        "you could simply open, and do NOT use it to type a password — call portal_login, "
        "which handles credentials without them passing through you. Pass the session_id "
        "back on the next call to keep the same tab and stay where you are; a new call "
        "without one starts fresh at a blank page. Returns the steps performed, any step "
        "that failed and why, the resulting page text and elements, and downloads captured."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "description": (
                    "Up to 25 steps, performed in order, stopping at the first failure."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["goto", "click", "fill", "select", "check", "press",
                                     "hover", "scroll", "wait", "wait_for", "back",
                                     "screenshot"],
                        },
                        "target": {
                            "type": "string",
                            "description": (
                                "Selector for the element, or the URL for 'goto'. Prefer the "
                                "semantic form the ARIA list gives you: role=link[name=\"Notlar\"], "
                                "text=Devamsızlık. CSS works too: #txtParamT01."
                            ),
                        },
                        "value": {
                            "type": "string",
                            "description": (
                                "Text for 'fill', option for 'select', key name for 'press', "
                                "milliseconds for 'wait'. Never a password."
                            ),
                        },
                    },
                    "required": ["action"],
                },
            },
            "session_id": {
                "type": "string",
                "description": (
                    "The session to continue, from a previous browser_act result. Omit to "
                    "start a new one."
                ),
            },
            "portal": {
                "type": "string",
                "description": (
                    "Name of a saved portal to run this session as, so it starts already "
                    "signed in. Only needed on the first call of a session."
                ),
            },
            "wait_for": {
                "type": "string",
                "description": "Optional CSS selector to wait for after the last step.",
            },
        },
        "required": ["steps"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        steps = args.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return "No steps given. Provide at least one, e.g. [{\"action\":\"goto\",\"target\":\"https://…\"}]."
        portal = (args.get("portal") or "").strip().lower()

        if portal:
            from app.core.runtime_state import get_portal

            record = get_portal(portal)
            if not record:
                return (f"No portal called '{portal}'. Known: "
                        f"{', '.join(browser_svc.portal_names()) or '(none configured)'}.")
            if not browser_svc.portal_allows(record, context.agent_id):
                return f"The '{portal}' portal is not shared with {context.agent_id}."

        try:
            result = await browser_svc.act(
                steps,
                session_id=(args.get("session_id") or "").strip() or None,
                profile=portal or None,
                wait_for=args.get("wait_for") or None,
            )
        except browser_svc.BrowserUnavailable as e:
            return _unavailable(e)

        lines = [f"session_id: {result.get('session_id')}  "
                 f"(pass this back to stay on the same tab)"]
        if result.get("performed"):
            lines.append("Performed:\n" + "\n".join(f"  {i+1}. {s}"
                                                    for i, s in enumerate(result["performed"])))
        if result.get("failed"):
            lines.append(f"STOPPED at: {result['failed']}\n"
                         f"The page below is where it stopped — re-read the elements and "
                         f"try a different selector.")
        if result.get("console_errors"):
            lines.append("Page console errors: " + "; ".join(result["console_errors"]))

        # A file the site handed us is the point of most portal flows, so it gets
        # pulled across and registered immediately rather than described.
        for item in result.get("downloads") or []:
            path = await browser_svc.pull_artifact(item.get("token", ""), item.get("name", ""))
            if path:
                from app.core.files import register_file

                meta = register_file(context, str(path), title=item.get("name"))
                lines.append(f"Downloaded **{meta['title']}** ({meta['kind']}) — "
                             f"delivered to the owner as a file. Do not paste a link to it.")
            else:
                lines.append(f"The site sent a file ({item.get('name')}) but it could not "
                             f"be retrieved from the browser container.")

        logger.info("browser_act", extra={
            "request_id": context.request_id, "session": result.get("session_id"),
            "steps": len(steps), "failed": bool(result.get("failed")),
        })
        lines.append(browser_svc.format_page(result, include_aria=True))
        return "\n\n".join(lines)


class PortalLoginSkill(Skill):
    name = "portal_login"
    deferred = True
    search_keywords = (
        "login sign in portal account student automation obs university credentials "
        "password session expired authenticate giriş yap öğrenci otomasyon şifre oturum"
    )
    read_only = False
    requires_network = True
    description = (
        "Signs into one of the owner's saved web portals — their student automation, "
        "library account, or any other site whose credentials they have stored — and keeps "
        "the session so later browse_page and browser_act calls on that portal arrive "
        "already logged in. Use it when a portal page turns out to be a login wall, when a "
        "browse_page against a portal reports the session expired, or when the owner asks "
        "you to check something on an account you have not opened this session. You never "
        "see or supply the password: name the portal and the backend hands the credential "
        "straight to the browser, so nothing sensitive passes through this conversation — "
        "never ask the owner to type a password to you, and if a portal is not configured, "
        "tell them to add it in Settings → Connections → Web portals. Returns whether the "
        "sign-in landed, and what the page said if it did not."
        + _portal_hint()
    )
    input_schema = {
        "type": "object",
        "properties": {
            "portal": {
                "type": "string",
                "description": "Name of the saved portal, e.g. 'obs'. Not a URL, not a username.",
            },
        },
        "required": ["portal"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        name = (args.get("portal") or "").strip().lower()
        known = ", ".join(browser_svc.portal_names())
        if not name:
            return ("Which portal? Configured: " + (known or "none yet — the owner adds "
                    "them in Settings → Connections → Web portals."))
        from app.core.runtime_state import get_portal

        record = get_portal(name)
        if not record:
            return f"No portal called '{name}'. Configured: {known or '(none)'}."
        if not browser_svc.portal_allows(record, context.agent_id):
            return f"The '{name}' portal is not shared with {context.agent_id}."
        if not record.get("password"):
            return (f"The '{name}' portal has no password stored. The owner sets it in "
                    f"Settings → Connections → Web portals — do not ask them for it here.")

        try:
            result = await browser_svc.login_portal(name)
        except browser_svc.BrowserUnavailable as e:
            return _unavailable(e)

        label = record.get("label") or name
        if result.get("already"):
            return (f"Already signed in to {label} — the stored session is still live. "
                    f"Currently on: {result.get('title') or result.get('url')}")
        if result.get("ok"):
            home = record.get("home_url") or result.get("url")
            return (f"Signed in to {label}. Landed on {result.get('title') or home}. "
                    f"Use browse_page with portal='{name}' to read pages there, or "
                    f"browser_act with portal='{name}' to work through it.")
        return (f"Could not sign in to {label}: {result.get('message')}\n"
                f"Tell the owner what happened and ask them to check the saved credentials "
                f"in Settings → Connections → Web portals. Do not ask them to give you the "
                f"password here.")


BROWSER_SKILLS = (BrowsePageSkill, BrowserActSkill, PortalLoginSkill)


def browser_available() -> bool:
    """Whether to register the tier at all. An unconfigured browser advertising
    three tools it cannot run is worse than a missing capability: the model
    spends a turn discovering it, every turn."""
    return bool(settings.browser_url)
