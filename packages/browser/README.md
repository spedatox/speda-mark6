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
