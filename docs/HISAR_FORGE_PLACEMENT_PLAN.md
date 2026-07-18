# H.İ.S.A.R. × Forge × Mark VI — Placement Plan

**Goal:** Place Hisar Mark I (the web-desktop file system) and Forge Mark I (the
privileged execution peer) beside SPEDA on the Contabo server so that:

1. **Agents can commit work to Hisar on command** — "SPEDA, put that report in
   Hisar" lands the file in the vault, visible on the web desktop.
2. **Forge saves the projects it scaffolds to Hisar** — a coding job dispatched
   to Optimus leaves its workspace browsable (and downloadable) from Hisar.
3. **Hisar is a daily-driver** at `https://hisar.spedatox.systems` — file
   transfer between the owner's devices and unknown/guest devices, TLS'd, with
   a real login.

**Repos involved:**

| Repo | Role | State today |
|---|---|---|
| `spedatox/speda-mark6` | Igor backend + Heartbreaker + this plan | Deployed on Contabo behind Caddy (`$DOMAIN`) |
| `spedatox/hisar-mk1` | Web-desktop file client (React/Vite, single `hisar.jsx`) | Frontend MVP; in-memory demo FS; backend seam documented in its README |
| `spedatox/forge-mark1` | Execution peer (Optimus engine) | Standalone; connects to Igor over `WS /agents/ws/optimus`; per-agent Cell workspaces under `FORGE_WORKSPACE_ROOT` |

---

## 0. Established facts — do not re-derive

1. **Igor's dispatch already carries a working directory.** `dispatch_agent`'s
   `working_directory` arg flows into the `task_dispatch {task_id, from, task, cwd}`
   frame that Forge serves (`forge/gate/peer.py`). Pointing a coding job at a
   Hisar-visible path needs zero protocol work.
2. **Hisar's file operations are behind a single seam.** `doMkdir` / `doRename`
   / `doDelete` / `doUpload` + the `fs`-derived listing inside `hisar.jsx`, with
   `VITE_API_BASE` reserved for the backend URL. The README already specs the
   FastAPI surface (`/auth/login`, `/files/list|upload|download|delete|mkdir|rename`).
3. **The prod proxy is Caddy with automatic TLS**, one site block per domain
   (`Caddyfile` + the `caddy` compose service, `--profile domain`). A subdomain
   is one more site block + one DNS A record. No new infrastructure.
4. **Igor's deliverables live in `/tmp/speda_outputs/`** (bind-mounted into the
   app container), registered via `app/core/files.py`, and wiped after 24h by
   n8n → `DELETE /admin/outputs`. Hisar is the natural "promote this file to
   permanent" target.
5. **Forge runs its jobs in per-agent Cell workspaces** under
   `FORGE_WORKSPACE_ROOT` (subprocess jail or Docker). The workspace root is
   just a path — it can live anywhere on the host.

---

## 1. Target topology

```
                    spedatox.systems ──────────► Caddy ──► igor (app:8000)
              hisar.spedatox.systems ──────────► Caddy ──► hisar (hisar:8600)

  ┌────────────────────────── Contabo host ──────────────────────────┐
  │                                                                  │
  │  igor (Mark VI)          hisar-api (FastAPI)       forge (peer)  │
  │  /tmp/speda_outputs      SANDBOX_ROOT =            WORKSPACE =   │
  │        │                 /opt/hisar/vault          /opt/hisar/   │
  │        │  hisar_deposit        ▲                     vault/      │
  │        └──── HTTP ────────────┘                      Forge/  ────┤
  │                                ▲                    (direct FS)  │
  │                                └── owner ⇄ browser (web desktop) │
  └──────────────────────────────────────────────────────────────────┘
```

- **One vault** on the host: `/opt/hisar/vault`. Hisar's API is the only
  network-facing door into it; Forge writes into a subtree of it directly
  (same host, shared bind mount) — its projects simply *appear* in the desktop.
- **Igor never mounts the vault.** Agents deposit through Hisar's HTTP API with
  a machine token. One auditable door for anything an agent does; the vault's
  permission model stays in one place.
- **Forge stays a standalone peer** (CLAUDE.md: Optimus is the single external
  exception). No change to how it links to Igor.

### Vault layout (convention, not code)

```
/opt/hisar/vault/
├── Desktop/          # what the web desktop shows as the desktop surface
├── Documents/
├── Transfers/        # daily-driver drop zone (uploads from any device)
├── SPEDA/            # ← agent deposits land here, per-agent subfolders
│   ├── speda/  sentinel/  nightcrawler/  ...
└── Forge/            # ← Forge's own subtree
    ├── workspaces/   # live Cell workspaces (FORGE_WORKSPACE_ROOT)
    └── projects/     # completed/archived scaffolds
```

---

## 2. Phase H1 — Hisar backend (the missing half)

New code in `hisar-mk1` (backend beside the client, same repo):

```
hisar-mk1/
├── server/
│   ├── main.py           # FastAPI app factory + static hosting of dist/
│   ├── auth.py           # owner login (argon2 hash → JWT) + machine token check
│   ├── files.py          # the 6 file routes, all sandboxed
│   └── config.py         # SANDBOX_ROOT, secrets, limits — env-driven
├── Dockerfile            # multi-stage: npm build → python slim, serves both
└── (existing client files)
```

**Routes** — exactly the README's spec, plus two additions:

| Method | Endpoint | Auth | Notes |
|---|---|---|---|
| `POST` | `/auth/login` | password | Owner login → JWT (httpOnly cookie) |
| `GET` | `/files/list?path=` | JWT | Listing with size/mtime/kind |
| `POST` | `/files/upload?path=` | JWT **or** machine token | Multipart, streamed to disk |
| `GET` | `/files/download?path=` | JWT | `FileResponse`, range support |
| `DELETE` | `/files/delete?path=` | JWT | To `.trash/` first (restore later) |
| `POST` | `/files/mkdir` | JWT or machine token | |
| `POST` | `/files/rename` | JWT | Within-sandbox move |
| `GET` | `/health` | none | For uptime checks |
| `POST` | `/deposit` | **machine token only** | Agent/Forge door: `{path, filename}` + multipart; creates parents, never overwrites (suffixes `-2`, `-3`…) |

**Security requirements (non-negotiable):**

- Every path resolves through one function: `resolve(base, user_path)` →
  `realpath` containment check against `SANDBOX_ROOT`. Reject traversal and
  symlink escapes. This is the single most attacked surface of the whole plan.
- Two credentials, two scopes: the **owner JWT** (full CRUD, short-lived,
  issued by login) and the **machine token** (`X-Hisar-Token`, env-set,
  constant-time compare — same pattern as Igor's `X-API-Key`) which can only
  `upload`/`mkdir`/`deposit` under `/SPEDA` and `/Forge`. An agent credential
  can add files; it can never read, list, or delete the owner's vault.
- Upload size cap (env, default 2 GiB), request rate limit on `/auth/login`.
- Runs as a non-root user; the vault bind mount is the only writable path.

**Client wiring** — replace the in-memory demo behind the existing seam:
`fs` state hydrates from `GET /files/list`, the four `do*` handlers become
`fetch` calls, login posts to `/auth/login`. Add an upload-progress bar
(XHR `onprogress`) since real transfers are the daily use-case. Everything else
(windowing, Spotlight, Quick Look) stays untouched.

---

## 3. Phase H2 — Deployment at hisar.spedatox.systems

**DNS:** A record `hisar.spedatox.systems` → Contabo IP (same as apex).

**Caddyfile** gains one block — Caddy auto-provisions the cert:

```caddyfile
{$DOMAIN} {
	reverse_proxy app:8000
}

hisar.{$DOMAIN} {
	reverse_proxy hisar:8600
	request_body {
		max_size 2GB
	}
}
```

**docker-compose.yml** gains one service:

```yaml
  hisar:
    build: ../hisar-mk1          # or a prebuilt image; see note below
    restart: unless-stopped
    expose: ["8600"]
    environment:
      HISAR_SANDBOX_ROOT: /vault
      HISAR_OWNER_PASSWORD_HASH: ${HISAR_OWNER_PASSWORD_HASH}
      HISAR_JWT_SECRET: ${HISAR_JWT_SECRET}
      HISAR_MACHINE_TOKEN: ${HISAR_MACHINE_TOKEN}
    volumes:
      - /opt/hisar/vault:/vault
```

*Note:* the GitOps deploy currently builds from the speda-mark6 repo only. Two
options, in order of preference: (a) publish `hisar-mk1` as a GHCR image from
its own repo's CI and reference `image:` here; (b) clone both repos side-by-side
on the server and use the relative `build:` context. Start with (b) — it matches
how Forge already deploys — and move to (a) when it stabilizes.

**Forge placement** (same host, no container change needed):

```
FORGE_WORKSPACE_ROOT=/opt/hisar/vault/Forge/workspaces
```

If Forge runs in Docker later, bind-mount `/opt/hisar/vault/Forge` into it.

---

## 4. Phase H3 — Agents commit work to Hisar (`hisar_deposit` skill)

New Tier-1 skill in `packages/api/app/skills/hisar.py`, registered like every
other skill (Rule 5 — drop a file in `skills/`, the orchestrator never changes):

- **`hisar_deposit`** — *"Deposits a file into H.İ.S.A.R., the owner's
  permanent file vault at hisar.spedatox.systems. Use it when the owner asks to
  save, keep, or 'put in Hisar' a file you generated or received — deliverables
  in `/tmp/speda_outputs` are wiped after 24 h, so anything worth keeping must
  be deposited. Do NOT use it for scratch output nobody asked to keep, and never
  to read or manage vault contents (the vault is write-only for agents). Returns
  the vault path where the file landed and its public desktop location."*
  (≥3–4 sentences per Rule 11 — final text lives in the skill.)
- Args: `source` (a `/tmp/speda_outputs` filename or a `file_id` from
  `register_file`), optional `folder` (default `SPEDA/{agent_id}/`), optional
  `filename`.
- Implementation: streams the file to Hisar's `POST /deposit` with
  `X-Hisar-Token`. New settings in `app/config.py`: `hisar_url`,
  `hisar_machine_token` (both optional — skill registers only when configured,
  same pattern as other optional integrations).
- `read_only = False`, `requires_network = True`. Available to every agent's
  tool allowlist; each profile opts in (`app/profiles/*.py`).
- Depositing is user-visible work → the agent tells the owner where it landed,
  one sentence.

**Flow:** owner: *"Sentinel, monthly budget PDF'ini Hisar'a koy"* → Sentinel
generates via `documents` skill → `hisar_deposit(source=...)` → file at
`vault/SPEDA/sentinel/2026-07-budget.pdf` → visible in the web desktop
immediately (listing is live), downloadable from any logged-in device.

---

## 5. Phase H4 — Forge saves scaffolds to Hisar

Two layers, both cheap because of the shared vault:

1. **Passive (free):** with `FORGE_WORKSPACE_ROOT` inside the vault, every live
   workspace is already browsable at `Forge/workspaces/{agent}/...`. Nothing to
   build; this ships with Phase H2.
2. **Active (small Forge change, `forge-mark1` repo):** on terminal
   `task_result` for a job whose task was a scaffold/new-project (heuristic: the
   Cell created a new top-level directory), Forge copies the final tree —
   minus `.git`, `node_modules`, `.venv`, build dirs — to
   `Forge/projects/{name}-{date}/`. Config: `FORGE_ARCHIVE_DIR` (blank = off).
   Alternative for a remote Forge: POST the tree as a tar to Hisar `/deposit`;
   same endpoint, no extra surface.

Result: *"Optimus, scaffold me a FastAPI starter"* → dispatch (with or without
`working_directory`) → Forge builds it in the Cell → the finished project sits
in Hisar under `Forge/projects/`, and the owner drags it out of the browser onto
whatever machine they're on.

---

## 6. Phase H5 — Daily-driver polish (post-MVP, ordered by value)

1. **Share links** — `POST /share {path, ttl}` → signed URL, no login needed:
   the "send a file to an unknown device" case in one tap. Optional password.
2. **Upload-only guest drop** — `hisar.spedatox.systems/drop/{token}`: others
   can send files *to* the owner without seeing the vault.
3. **Restore from Trash + Empty Trash** (the client seam already anticipates it).
4. **Persist desktop layout** server-side (`/state` blob) so the desktop is the
   same from every device.
5. **SPEDA awareness of the vault** — a read-only `hisar_list` skill so the
   owner can ask "what's in my Transfers folder?" (separate decision: it widens
   the machine scope from write-only to read; do it only if genuinely wanted).

---

## 7. Security summary

| Surface | Control |
|---|---|
| Vault path traversal | Single `resolve()` chokepoint, realpath containment, symlink rejection — unit-tested first |
| Owner auth | Argon2 password hash, short-lived JWT in httpOnly cookie, login rate-limit |
| Agent/Forge auth | `X-Hisar-Token` constant-time compare; scope limited to upload/mkdir under `SPEDA/` + `Forge/` |
| Transport | Caddy TLS on both domains; HTTP never exposed |
| Blast radius | Hisar container: non-root, vault is its only writable mount, no access to Igor's DB/secrets/network beyond compose |
| Forge | Unchanged threat model — Cell isolation as today; the vault subtree it can touch contains no Igor state |
| Igor | Gains only two optional settings and one outbound-HTTP skill; no inbound surface added |

---

## 8. Build order & done signals

| Phase | Where | Done when |
|---|---|---|
| **H1** Hisar backend | `hisar-mk1` | Path-sandbox unit tests green; client runs CRUD against it locally end-to-end (login, upload w/ progress, download, rename, trash) |
| **H2** Deploy | `speda-mark6` (compose/Caddy) + DNS | `https://hisar.spedatox.systems` serves the desktop with TLS; upload from a phone works; vault survives container recreation |
| **H3** `hisar_deposit` | `speda-mark6` `packages/api` | Owner asks SPEDA to save a generated file → appears in the web desktop; skill absent when `hisar_url` unset |
| **H4** Forge archive | `forge-mark1` | A dispatched scaffold job ends with the project under `Forge/projects/`, visible in Hisar |
| **H5** Polish | `hisar-mk1` | Share link opened from a device that has never logged in |

H1 → H2 are strictly ordered. H3 and H4 are independent of each other and can
interleave once H2 is live. H5 last.
