# Speda Browser

A Playwright service in its own container. Igor's agents reach it through
`app/services/browser.py`; nothing else on the network should.

```
POST /read     render one URL, return readable text + links (+ optional aria, screenshot)
POST /act      run steps against a live session, return where they landed
POST /login    sign a profile in — the only endpoint that takes a password
GET  /artifact fetch a captured download or screenshot by token
POST /forget   drop a profile's cookies (the sign-out)
DELETE /session/{id}
GET  /health
```

Every request needs `X-Browser-Token` when `BROWSER_TOKEN` is set. Set it.

## Profiles

A **profile** is a named cookie jar — `obs`, `library`, `bank` — persisted as
`storage_state` under `/state/profiles/<name>.json` on the `browser_state`
volume. `/login` writes it; `/read` and `/act` load it, so a portal login
survives restarts and every later visit arrives already authenticated.

Credentials are **not** stored here. They live in Igor's `runtime_state.json`
(the same file already holding the Google and Microsoft refresh tokens) and are
passed in on the `/login` call. This container keeps cookies, never passwords.

## Isolation

| | |
|---|---|
| Network | internal compose network only — never a published port, never Caddy |
| Mounts | its own `browser_state` volume; no host paths, no Igor `.env`, no database |
| User | `pwuser`, non-root |
| Caps | all dropped, `no-new-privileges` |
| Limits | 2 GB memory, 2 CPUs, 512 pids |

Chromium's own sandbox is **off** (`chromium_sandbox=False`): it needs
user-namespace cloning, which Docker's default seccomp profile denies, so
enabling it without a profile just fails to launch. The container is the
boundary instead. To harden further, take Playwright's
`utils/docker/seccomp_profile.json` from the upstream repo, drop it beside this
file, add

```yaml
    security_opt:
      - seccomp=./packages/browser/seccomp_profile.json
```

to the `browser` service, and flip `chromium_sandbox` back to `True` in
`server.py`. Worth doing if this ever browses genuinely hostile ground rather
than the owner's portals, news sites and search results.

## Local run (no Docker)

```bash
pip install playwright fastapi "uvicorn[standard]" && playwright install chromium
BROWSER_STATE_DIR=~/.speda/browser_state BROWSER_PORT=9200 python packages/browser/server.py
```

Then point Igor at it with `BROWSER_URL=http://localhost:9200`.

## Selectors

`/act` takes any Playwright selector. Prefer the semantic ones, because they are
the ones the `aria` field in every response already named:

```
role=button[name="Giriş"]      role=link[name=/not listesi/i]
text=Devamsızlık               #txtParamT01
```

A CSS selector is the right answer when the page has stable ids — most Turkish
university portals run on ASP.NET WebForms and do.

## The `/act` step vocabulary

```
goto click fill type select check press hover drag scroll resize
wait wait_for back screenshot evaluate upload_file
new_tab switch_tab close_tab
```

Most are one selector and one value. The ones worth a note:

- **`type` vs `fill`** — `fill` sets a field's value directly; `type` sends real
  per-keystroke events (`press_sequentially`). Reach for `type` on anything
  whose own JS listens to keydown rather than a value change — autocomplete, a
  date picker, a masked input.
- **`drag`** — `target` is the source selector, `value` the destination.
- **`resize`** — `value` is `"WxH"`, e.g. `"390x844"`.
- **`evaluate`** — runs JS. Page-level when `target` is empty (`value` can be a
  plain expression, `document.title`); scoped to one element when `target` is
  set (`value` must then be a one-argument function, `el => el.value`, per
  Playwright's own `Locator.evaluate` contract). This is full power, matching
  upstream `@playwright/mcp`'s `browser_evaluate` — nothing here sandboxes what
  the JS can read. It must never be used to read a password field back out;
  that is the one thing this whole sidecar exists to prevent from the other
  direction.
- **`upload_file`** — `target` is the `<input type=file>`, `value` is a key
  into the call's top-level `files: {name: base64}` map (see below). Never a
  raw filesystem path.
- **`new_tab` / `switch_tab` / `close_tab`** — a session can hold more than one
  tab now; `target`/`value` on `new_tab` optionally navigates the new tab,
  `switch_tab`/`close_tab` take a tab index. Every response carries `tabs`
  (`[{index, url, title}]`) and `active_tab`.

## Dialogs, uploads, console and network

`/act`'s body also takes:

| Field | |
|---|---|
| `dialog_policy` | `"dismiss"` (default) or `"accept"` — how an alert/confirm/prompt raised by one of THIS call's steps is handled. Dismiss is the safe default: a needless dismiss costs nothing, wrongly accepting a "permanently delete?" confirm cannot be undone. |
| `dialog_text` | Text to submit if the dialog is a `prompt()` and the policy is `accept`. |
| `files` | `{filename: base64}` — decoded to scratch paths before steps run, removed after. An `upload_file` step's `value` names one of these keys. |
| `include_network` | `false` by default. When `true`, the response carries `network` — the last ~20 requests, non-2xx and xhr/fetch prioritized. |
| `close` | Ends the session after the steps run. Valid with an empty `steps` list too, as a plain "done with this tab" call. |

Every response also carries `console` (all console levels, capped) alongside
the existing `console_errors`, and `dialogs` (any alert/confirm/prompt the
session's steps raised, with their message).
