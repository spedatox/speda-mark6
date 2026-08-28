# The Browser

*Playwright, in its own container, with the owner's logins.*

Until this existed, everything Mark VI knew about the web it learned by asking a
server for HTML and believing the answer. That works for an RSS feed and an API.
It does not work for a university portal, a dashboard, a search results page, or
any of the pages the owner actually looks at — those are programs that draw
themselves after they load, and to an HTTP client they are an empty div.

Two capabilities come out of fixing that, and they are worth naming separately
because they fail differently and are secured differently.

**Reading the rendered web.** `browse_page` is the fallback beneath `fetch`,
Tavily, Exa and the news reader. Slower by construction; that is what it buys.

**Working the owner's accounts.** A student automation is not a scraping target.
It is an account, with a password, and the only version of this that is safe to
build is one where the password never becomes text a model produced.

---

## Shape

```
Heartbreaker ─┐
              ├─ Igor ── app/skills/browser.py     three tools
n8n ──────────┘           app/services/browser.py   the desk
                          app/core/runtime_state.py the vault (credentials)
                                   │
                                   │ internal Docker network + X-Browser-Token
                                   ▼
                          packages/browser          Playwright + Chromium
                                   │                storage_state (cookies)
                                   ▼
                              the actual web
```

The split down the middle is the security boundary. **Credentials live on the
Igor side. Cookies live on the browser side.** Neither holds the other's half.
The browser container renders pages the owner did not write, so it must not hold
a password; Igor holds every other secret already, so it is the natural vault.

---

## The three tools

| Tool | Verb | Read-only | Notes |
|------|------|-----------|-------|
| `browse_page` | read | yes (Rule 9) | one URL → text, links, optional ARIA element list |
| `browser_act` | do | no | up to 25 steps against a live session; captures downloads |
| `portal_login` | authenticate | no | takes a portal NAME; never a credential |

All three are `deferred` — their Rule 11 descriptions are long and they overlap
with `fetch`, so they load through `tool_search` when a task needs them rather
than riding in every prompt prefix.

`browser_act`'s step vocabulary now goes well past click/fill/select: it also
speaks `drag`, `type` (real keystrokes), `resize`, `evaluate` (full JS,
matching upstream `@playwright/mcp`'s `browser_evaluate` — see the warning in
`packages/browser/README.md`), `upload_file` (a file Igor already has, never a
raw path), and `new_tab`/`switch_tab`/`close_tab` for sessions that open a
second window. It also takes `dialog_policy`/`dialog_text` (default: dismiss
any alert/confirm/prompt), `include_network` (recent requests, for when a
click silently didn't fire one), and `close` (end the session deliberately
instead of waiting out the idle timeout). See
`packages/browser/README.md#the-act-step-vocabulary` for the full list.

### Selectors

`browser_act` speaks Playwright selectors. Prefer the semantic ones, because
they are exactly what the `aria` block in every response already listed:

```
role=button[name="Giriş"]
role=link[name=/not listesi/i]
text=Devamsızlık
#txtParamT01                 ← CSS, right answer when the page has stable ids
```

Turkish university portals mostly run on ASP.NET WebForms, so they do have ids.

### A typical flow

```
browse_page  url=https://obs.example.edu.tr/  portal=obs  interactive=true
  → signs in if needed, returns the page and its elements

browser_act  portal=obs  steps=[{action: click, target: role=link[name="Not Listesi"]}]
  → session_id: 4f2c…, the grades page

browser_act  session_id=4f2c…  steps=[{action: click, target: text=PDF indir}]
  → download captured, registered, delivered as a file card
```

---

## Portals

A portal is one record in `~/.speda/runtime_state.json`, added from
**Settings → Connections → Web portals**:

| Field | |
|---|---|
| `name` | what an agent says — `obs`. Lowercase, memorable. |
| `login_url` | the sign-in page |
| `home_url` | where to land afterwards (optional) |
| `username` / `password` | the credential. Masked on every read the UI does. |
| `selectors` | overrides, almost never needed — see below |
| `success_selector` / `success_url_contains` | how to prove we got in |
| `allowed_agents` | empty = every agent. Right for a library; wrong for a bank. |

**Saving signs in.** A credential that was stored but never tried is
indistinguishable from one that works, right up until an agent needs it at 2am.
The verdict comes back in the browser's own words.

### Why selectors are usually blank

A login form is the one form on the web with a reliable shape: exactly one
password box, and the text input above it is the username. The container finds
it that way, searching every frame — portals built on WebForms and most SSO
pages put the form in an iframe, and looking only at the top document is the
classic reason a login bot reports "the selector isn't there".

Fill the advanced fields in only when a test sign-in says it could not find the
fields.

### How "am I still signed in?" is answered

Only by asking. Sessions expire on the site's clock, not ours.
`ensure_logged_in()` visits, decides whether it landed on a login wall, signs in
if so, and revisits. The decision prefers the configured signals when they exist
(`success_url_contains` is the owner stating outright what "inside" looks like)
and otherwise reads the page in both languages the owner's portals are written
in. It accepts false positives: a needless re-login costs six seconds, while a
missed one makes an agent report a login form's contents as the owner's grades.

---

## The probe fallback

`POST /web/watch/scan` is a cheap probe (see CLAUDE.md → "Cheap probes"): plain
HTTP, no model, so a poll that finds nothing costs nothing. When that fetch
comes back with **no readable text**, it now retries once through the browser
instead of returning a permanent error.

That error was the failure mode worth ending. The pages a watch is *for* —
exam results, an academic calendar — are exactly the ones most likely to be
rendered client-side, so the watch the owner cared most about was the watch that
silently never worked.

`lines_from_render()` deliberately produces the same line shape as
`extract_lines()` (visible text, anchors as `text [href]`). The two feed one
snapshot, and a watch that renders once and fetches the next time must not
report the whole page as new because its two extractors disagreed about
formatting.

Switch: `BROWSER_FALLBACK_ENABLED`. The scan reports `rendered: true` when it
happened.

---

## Deployment

```yaml
browser:
  build: ./packages/browser
  expose: ["9200"]          # never ports:, never a Caddy site
  environment:
    BROWSER_TOKEN: ${BROWSER_TOKEN:-}
  volumes:
    - browser_state:/state  # cookie jars only
  ipc: host
  init: true
  cap_drop: [ALL]
```

`deploy.sh` **generates** `BROWSER_TOKEN` into `packages/igor/.env` on first run
if it is missing, then exports it so both halves agree without a second secret
file. Nobody types it: it is a secret shared between two containers and nothing
else, so a manual step could only ever contribute the chance of leaving it empty
— which silently means the sidecar accepts anything on the compose network. To
rotate it deliberately, delete the line and re-run `deploy.sh`. Igor reads `BROWSER_URL` and
`BROWSER_TOKEN`; the sidecar reads `BROWSER_TOKEN` and nothing else of Igor's.

Locally there is no autostart — Chromium is a 400 MB install, not a subprocess.
Run `packages/browser/server.py` yourself and point `BROWSER_URL` at it.

---

## The other path: the open public web (`packages/playwright-mcp`)

Everything above is one design, built around one constraint: a login must
never make the model type a password. That constraint has a cost — the step
vocabulary is ours to build, one action at a time, and reaching full parity
with the official `@playwright/mcp` (drag, evaluate, tabs, dialogs, uploads,
network/console inspection, resize…) means writing every one of those by hand.

For a page that needs no login at all, that cost buys nothing. So there's a
**second, separate** container — `packages/playwright-mcp`, the official
Microsoft server — registered as an ordinary Tier 2 MCP server
(`app/mcp/servers.py`) whenever `playwright_mcp_url` is set. It gives literal
1:1 tool parity with the upstream server: `browser_click`, `browser_evaluate`,
`browser_drag`, `browser_tabs`, and the rest, maintained by Microsoft, not us.

**The rule is absolute and it never inverts:** this path is for the open
public web ONLY. The three tools above — `browse_page` / `browser_act` /
`portal_login` — stay the ONLY way into one of the owner's saved logins,
forever, because signing in through this server means the model itself types
the password (see `packages/browser/server.py`'s module docstring for why that
is the one thing this whole design refuses to do). The steering note in
`app/prompts/core/03_capabilities.md` tells the model exactly this.

It is isolated the same way as the sidecar above — its own container,
`expose:` never `ports:`, no host mounts, resource-limited, `--isolated` (every
session's browser profile lives in memory only, never touches disk). The one
difference: `@playwright/mcp` has no built-in request authentication of its
own, so the network boundary — never published, never behind Caddy — IS the
entire boundary, the same as `packages/sandbox` already relies on. See
`packages/playwright-mcp/Dockerfile` for the version pin (CVE-2025-9611, a DNS
rebinding bug via missing Origin/Host validation, is fixed in `@playwright/mcp`
0.0.40+ — pinned well above that, deliberately, never `@latest`).

## Costs

A render is roughly 2–6 seconds and ~200 MB of container memory while it runs,
against ~200 ms for a fetch. It is still two orders of magnitude cheaper than an
agentic turn, which is the number that actually governs the design here.

`BROWSER_MAX_CHARS` caps what reaches a completion. The container caps itself
too, but the cap that costs money is the one on this side.

## What this does not do

- **Solve CAPTCHAs.** If a portal starts challenging, the login reports it and
  the owner deals with it. Nothing here tries to look like it isn't automation
  beyond sending a normal desktop user-agent, which exists because the default
  Playwright UA makes some portals serve a blank page.
- **Accept cookie banners or terms.** Never automatically. An agent that clicks
  "I agree" on the owner's behalf has agreed to something on their behalf.
- **Store passwords in the browser container**, or hand one to a model, or write
  one to a log. If you are adding a feature that needs it to, the feature is
  wrong.
