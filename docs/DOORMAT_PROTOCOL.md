# Doormat Protocol — changing the domain without locking yourself out

**Owner agent:** Orion · **Tool:** `doormat_protocol` · **Endpoints:** `/host/doormat*`

| Piece | Where |
|---|---|
| the phases, preconditions, rollbacks | [`app/services/doormat.py`](../packages/igor/app/services/doormat.py) |
| the owner-only gate | [`app/skills/doormat.py`](../packages/igor/app/skills/doormat.py) |
| the HTTP surface | [`app/routers/doormat.py`](../packages/igor/app/routers/doormat.py) |
| what Orion is told to do | `app/prompts/agents/orion/05_doormat.md` |
| the invariants, pinned | [`tests/test_doormat.py`](../packages/igor/tests/test_doormat.py) |

Moving Mark VI to a new domain is not one change. It is a certificate, a reverse
proxy, four settings inside Igor, a Telegram webhook per bot, three third-party
consoles nobody on this side can reach, and two clients still pointing at the old
address. Done in one step, any of them can fail in a way that takes the app down
with no way back in — because the way back in was the thing that just moved.

---

## The rule

> **The old door stays open until the new one is proven.**

Caddy serves as many hostnames as it is given, so there is never a moment where
neither works. That one fact is what turns a risky migration into three
reversible steps. Do not collapse them.

---

## The phases

### 1. `stage` — the new domain, alongside the old

```
doormat_protocol(action="stage", domain="speda.example.com")
POST /host/doormat/stage   {"domain": "speda.example.com"}
```

Writes `caddy-sites/doormat.caddy`, reloads Caddy, and verifies over real TLS
that `https://new/health` answers 200. **Nothing else changes** — the current
domain, every setting, every client is untouched.

**It refuses unless the domain already resolves to this server.** That refusal is
the most valuable thing in the protocol: a Caddy site for a hostname pointing
elsewhere does not fail quietly, it enters an ACME retry loop against a
rate-limited Let's Encrypt and the certificate never arrives. The refusal names
the A record to create and the address to point it at.

`force=true` exists for exactly one case — a proxy in front (Cloudflare and the
like), where the record correctly points somewhere else. It is not for a record
that has not propagated yet.

**A stage that fails anywhere rolls itself back**: the site file is removed and
Caddy reloaded to the previous config. A rejected Caddyfile never displaces the
running one, which is why a failure here is safe and a failure later would not be.

### 2. The part this server cannot do

Staging returns a checklist built from what **this deployment actually uses**,
with the exact strings to paste. Providers that are not configured do not appear
— a checklist padded with irrelevant steps is a checklist that gets skimmed, and
the one that mattered was in the middle of it.

| Provider | Where | What to add |
|---|---|---|
| Google | console.cloud.google.com → APIs & Services → Credentials → your OAuth 2.0 Client | `https://new/oauth/google/callback` |
| Microsoft | portal.azure.com → App registrations → your app → Authentication → Web | `https://new/oauth/microsoft/callback` |
| Notion | notion.so/my-integrations → your integration → Authorization | `https://new/oauth/notion/callback` |
| Telegram | *nothing* | re-registered automatically on restart |
| Desktop app | the app's connection settings | `https://new` — no rebuild, the address is editable |
| Speda GO | the phone app's connection settings | `https://new` |

**ADD, never replace.** The old redirect URI keeps working until `retire`. Same
principle as the Caddy site, for the same reason — replacing instead of adding
breaks sign-in on the address still in use.

If `N8N_DOMAIN` is a subdomain of the domain being retired, the checklist says so
loudly: it dies with its parent, needs its own A record and a deploy, and this
protocol does not move it.

### 3. `cutover` — repoint Igor

```
doormat_protocol(action="cutover")
POST /host/doormat/cutover
```

Writes four settings to the managed override file:

- `TELEGRAM_WEBHOOK_BASE`
- `GOOGLE_OAUTH_REDIRECT`
- `MICROSOFT_OAUTH_REDIRECT`
- `NOTION_OAUTH_REDIRECT`

Both hostnames still serve, so this is still recoverable. It refuses if the
staged domain is not answering right now.

All four are `requires_restart` in `config_schema.py`, so **the running process is
still on the old domain when this returns.** One step remains:

```
system_ops(action="restart_service", service="app")
```

That is a self-restart — the [SERVER OPERATIONS](../packages/igor/app/prompts/agents/orion/03_operations.md)
rules apply in full: it schedules detached, fires after the turn, and the report
goes in the same reply. Telegram webhooks re-register for every bot on boot.

A skipped restart does not go unnoticed: `status` compares the managed file
against the live `settings` object and reports the drift, and `retire` refuses
while it exists.

### 4. `retire` — close the old door

```
doormat_protocol(action="retire")
POST /host/doormat/retire
```

Only once the owner confirms the new address works everywhere they use it.
Removes the site file, rewrites `DOMAIN=` in `packages/igor/.env`, and recreates
Caddy — **the only step that recreates a container**, so Caddy blinks for a few
seconds.

The ordering inside that single host command is load-bearing: the site file must
be gone *before* `{$DOMAIN}` becomes the same hostname. Caddy refuses a Caddyfile
with a duplicate site address, and on a recreate a refused config is not a safe
no-op — it is a proxy that does not come back up.

It refuses while the new domain is not serving, and while the cutover restart is
still outstanding.

Afterwards: remove the OLD redirect URIs from Google, Microsoft and Notion. They
were kept deliberately until now, but a stale redirect URI on a domain you no
longer own becomes somebody else's login button the day someone else registers it.

### `abort`

Undoes a stage completely. **Refuses after cutover** — by then the settings have
moved, and dropping the new site would leave every redirect URI naming a hostname
nothing serves. To reverse a cutover, stage the original domain and cut over to
it; it is the same protocol run backwards.

---

## What it discovers rather than being told

The Caddy site directory, the repo root, the deployment `.env` path and the
hostname currently being served are all read from Docker at run time — the site
mount's source *is* the repo's `caddy-sites/`, and the repo root is its parent. A
second copy of any of that in settings would be a second thing to be wrong.

The consequence: **Caddy must already be running.** Setting up a domain for the
first time is a deploy concern (`DOMAIN` in `packages/igor/.env`, then
`./deploy.sh`), not a domain *change*.

---

## Safety

- `DOORMAT_PROTOCOL_ENABLED` is off by default and needs the `system_ops` host
  bridge configured to reach the host at all.
- The tool is `restricted_to={"orion", "optimus"}` — invisible to every other agent.
- **Every phase except `status` refuses a non-user trigger.** There is no
  autonomous path and no emergency that would justify one: changing the domain is
  never urgent enough to do without the person whose domain it is. `status` is
  readable from a background turn, because noticing a week-old unfinished move is
  useful and reading is not acting.
- The hostname pattern (RFC 1123, lowercase, two labels minimum) is also the
  injection guard — the value reaches a shell heredoc and a Caddyfile, and
  nothing that fails the pattern gets near either.
- The protocol's own site file only ever names *the other door*, so it and the
  deployment's `{$DOMAIN}` block can never collide.
