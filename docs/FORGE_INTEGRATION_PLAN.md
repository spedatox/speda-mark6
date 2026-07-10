# Forge Mark II × Mark VI × Heartbreaker — Integration Plan

**Goal:** One command (`speda.ps1`) boots the whole network: Mark VI backend, Mark VI's
sandbox, the Forge peer (Optimus Mark II engine) with its own Cell sandbox, and the
Heartbreaker UI — with Heartbreaker natively aware of the Forge link.

**Repos:**
- Mark VI backend + Heartbreaker: `C:\Users\AREL TARIM\speda-mark6`
- The Forge (Mark II): `C:\Users\AREL TARIM\forge-mk1`

---

## 0. Established facts — do NOT re-derive these

1. **The wire protocol already matches.** `forge/gate/peer.py` connects to Mark VI's
   `WS /agents/ws/{agent_id}` (in `app/routers/agents.py`), authenticates with
   `X-API-Key`, sends `agent_register` + heartbeats, and serves `task_dispatch` /
   `chat_request` / `chat_cancel` / `shutdown` frames. Its `chat_event` vocabulary
   (`chunk` / `tool` / `tool_result` / `done` / `error`) maps 1:1 onto
   `app/core/external_proxy.py`'s `_EVENT_MAP`. **No protocol work is needed.**

2. **`app/services/optimus_peer.py` is dead code.** It references settings
   (`optimus_peer_dir`, `optimus_peer_autostart`, `optimus_peer_python`,
   `optimus_peer_ws_url`) that do not exist in `app/config.py`, it is never
   instantiated in `app/main.py`'s lifespan, and it launches the **Mark I** entrypoint
   (`python -m optimus.peer`). Forge Mark II's entrypoint is
   `python -m forge connect --agent optimus`, run from the forge-mk1 root.

3. **Mark VI's sandbox never runs in local dev.** `packages/sandbox/server.py` is a
   stdlib-only HTTP exec server, used by `app/skills/sandbox.py` (`run_command` skill)
   via `settings.sandbox_url`. It is wired only in `docker-compose.yml`
   (`SANDBOX_URL=http://sandbox:9000`). Locally nothing starts it. Also:
   `sandbox_url` defaults to `http://localhost:9000`, which **collides with
   `shannon_url`** (also `localhost:9000`) in `app/config.py:272-276`.

4. **Docker is NOT installed on this dev machine.** Both sandboxes must run in their
   reduced-isolation local modes here; the Docker paths stay intact for Contabo:
   - Forge Cell: `FORGE_CELL_BACKEND=auto` → `SubprocessCell` (per-agent workspace
     jail; Windows-compatible, uses `asyncio.create_subprocess_shell`).
   - Mark VI sandbox: run `server.py` directly as a child process with a local
     workspace directory.

5. **Tooling constraints (Windows dev box):** no system python — use portable `uv`
   at `~\.local\bin\uv.exe` (`uv run --project <dir> ...`). Never edit source files
   through PowerShell 5.1 (mojibake risk). Forge-mk1 has a `pyproject.toml`; deps are
   `pydantic`, `websockets`, `anthropic`.

6. **Heartbreaker** (`packages/heartbreaker`) already brands Optimus as "Mark II"
   (`src/renderer/src/profile/brands.ts:53`). Backend status source:
   `GET /agents` returns online external peers (`agent_registry.list_online()`).
   `/chat/optimus` transparently proxies to the peer while online and falls back to
   the in-process `OptimusProfile` (`external_backend = True`) when offline.
   `context.extra["cwd"]` is already plumbed through the external proxy.

7. **`speda.ps1`** currently: starts uvicorn (`packages/api`) in a new window, waits
   for port 8000, then runs `npm run heartbreaker:dev`, and on exit kills the API +
   orphaned uvicorn workers.

---

## Phase 1 — Forge peer launcher (backend owns the lifecycle)

Rewrite `app/services/optimus_peer.py` → `app/services/forge_peer.py` (delete the old
module; update any imports):

- **New settings in `app/config.py`** (+ `.env.example`):
  - `forge_autostart: bool = True`
  - `forge_dir: str = ""` — absolute path to forge-mk1 (empty disables; validate
    `<forge_dir>/forge/__main__.py` exists)
  - `forge_agent: str = "optimus"`
  - `forge_ws_url: str = "ws://127.0.0.1:8000/agents/ws/optimus"`
  - `forge_cell_backend: str = "auto"`
- **Launch command:** `uv run --project <forge_dir> python -m forge connect --agent
  <forge_agent>` with `cwd=<forge_dir>`. (Fallback: a `forge_python` setting if the
  operator wants a specific interpreter.)
- **Injected env:** `SPEDA_API_KEY`, `SPEDA_WS_URL=<forge_ws_url>`,
  `FORGE_CELL_BACKEND`, `FORGE_WORKSPACE_ROOT=<forge_dir>\.forge\workspaces`, and
  pass through `ANTHROPIC_API_KEY` (the Forge makes its own inference calls; Mark VI
  is never an inference proxy).
- **Wire into `app/main.py` lifespan:** instantiate, `await launcher.start()` after
  the WS routes are live, store on `app.state.forge_launcher`, `await stop()` on
  shutdown (terminate → 5 s grace → kill). Keep the original best-effort semantics:
  a missing dir or instant crash logs a warning and Mark VI keeps running — the
  in-process Optimus profile is the fallback, exactly as when the peer is offline.
- Peer stdout/stderr inherit the backend console (one terminal for the network).

**Acceptance:** starting uvicorn alone brings the peer up; `GET /agents` lists
`optimus` online within a few seconds; killing the backend kills the peer.

## Phase 2 — Mark VI sandbox, local mode

New `app/services/sandbox_launcher.py`, same lifecycle pattern as Phase 1:

- **New settings:** `sandbox_autostart: bool = True`,
  `sandbox_local_port: int = 9100`,
  `sandbox_workspace: str = "<repo>\.speda\sandbox_workspace"`.
- **Fix the port collision:** change the `sandbox_url` default to
  `http://localhost:9100` (Shannon keeps 9000). docker-compose is unaffected — it
  overrides via `SANDBOX_URL=http://sandbox:9000`.
- **Behaviour:** if `sandbox_autostart` and `sandbox_url` points at localhost and
  nothing answers `/health`, spawn
  `uv run python packages/sandbox/server.py` with
  `SANDBOX_PORT=<sandbox_local_port>`, `SANDBOX_WORKSPACE=<sandbox_workspace>`.
  Stop it on shutdown. If something already answers `/health`, do not spawn (Docker
  or a manual instance is running).
- `server.py` itself needs no changes (stdlib-only, already cross-platform); this is
  honestly reduced isolation on Windows — same posture as Forge's `SubprocessCell`.
  Docker remains the production isolation on Contabo. Do not weaken the skill
  description's claims beyond adding nothing.

**Acceptance:** with the backend up, asking SPEDA to `run_command` executes locally
and returns stdout; files persist across calls in `.speda\sandbox_workspace`.

## Phase 3 — Forge Cell verification (no code expected)

- `FORGE_CELL_BACKEND=auto` selects `SubprocessCell` here (no Docker daemon).
- Smoke: `uv run --project C:\Users\AREL TARIM\forge-mk1 python -m forge demo` and
  `uv run --project ... python -m pytest` (13 tests) must pass on Windows. Fix only
  genuine Windows breakage if found (e.g. path or event-loop issues); do not touch
  the Docker backend.
- Per-agent workspaces live under `forge-mk1\.forge\workspaces\<agent_id>` — never
  shared, per the Cell contract.

## Phase 4 — Heartbreaker native Forge awareness

All UI work in `packages/heartbreaker` only (never `packages/desktop`,
per HEARTBREAKER.md). Follow the established Stark fluid-glass language
(`.hb-holo` recipe in `src/renderer/src/theme/heartbreaker.css`); banned props:
grids, corner brackets, ruler ticks, scanlines.

1. **Forge link status.** Poll `GET /agents` (piggyback the existing agents polling
   in `src/renderer/src/lib/agents.ts` if present, else a light 10 s interval).
   Surface engine state for Optimus:
   - In `AgentSwitcherOverlay.tsx` and/or the header identity area: a subtle status
     jewel + label — **"FORGE LINK — Mark II engine"** when the peer is online,
     **"IN-PROCESS FALLBACK"** when offline. No layout shift between states.
   - `SystemsBoard.tsx`: add a Forge row (peer online/offline, agent id, last
     heartbeat if cheaply available from the agents payload).
2. **Tool-event rendering.** Optimus chats streamed from the Forge emit tool events
   named `run_command`, `read_file`, `write_file`, `graph_query`, `graph_path`,
   `graph_overview`. Verify `ChatMain.tsx` / `Message.tsx` render these in the tool
   timeline; add friendly labels/icons for them (e.g. graph tools → "codebase
   graph"). No new event plumbing should be required — the SSE vocabulary is
   identical to in-process tools.
3. **Workspace (`cwd`) control — small, optional but requested "native" behaviour.**
   When chatting with Optimus, allow setting a working directory for the Forge job
   (a compact field in the Optimus chat header or agent switcher, persisted in
   localStorage). Send it so it lands in `context.extra["cwd"]` → the peer's
   `chat_request.cwd` → the Cell workspace / Graphify root. Check
   `app/routers/chat.py` + `app/schemas/chat.py` for whether the request schema
   already accepts `cwd`; add the field if missing (schema-only — no router logic,
   per CLAUDE.md Rule 1).

## Phase 5 — `speda.ps1` unified boot

Keep the principle: **the backend owns child lifecycles** (forge peer + sandbox are
lifespan children, not ps1 jobs). `speda.ps1` changes are cosmetic + cleanup only:

- After the API handshake, probe `GET /agents` (with `X-API-Key`) once or twice and
  log `FORGE LINK ESTABLISHED — Optimus Mark II online` / `Forge peer offline —
  in-process fallback active`.
- Extend the shutdown orphan sweep to also kill lingering `python.*forge connect`
  and `python.*sandbox.*server.py` processes (the lifespan normally handles this;
  the sweep is belt-and-braces for crashed backends).

## Phase 6 — Done signal (all must pass)

1. `.\speda.ps1` alone brings up: API, local sandbox (port 9100 `/health` ok),
   Forge peer (`GET /agents` shows `optimus`), Heartbreaker window.
2. In Heartbreaker, the Optimus status shows **FORGE LINK** online.
3. A chat to Optimus streams from the Forge with visible `run_command` /
   `graph_query` tool events, and the Cell workspace shows the side effects.
4. A chat to SPEDA using `run_command` executes in the local Mark VI sandbox and
   returns real output.
5. Kill the forge peer process manually → the next Optimus turn answers via the
   in-process profile (no error surfaced to the user); the peer's reconnect/backoff
   restores FORGE LINK without a backend restart. (If the launcher child died, it is
   acceptable that relink requires backend restart — but prefer a supervised
   restart-on-exit with capped backoff in the launcher.)
6. Test suites green: forge-mk1 pytest (13 tests) and `packages/api` tests.
7. Closing the ps1 window leaves zero orphaned uvicorn / forge / sandbox processes.

---

## Risks & guardrails

- **CLAUDE.md rules apply throughout:** no logic in routers; no identity/model IDs in
  core; everything on `app.state` via lifespan; structured logging with
  `request_id`; no internal schedulers.
- **Port map after this plan:** API 8000 · sandbox 9100 (local) / 9000 (compose,
  internal) · Shannon 9000 · Heartbreaker renderer 5274 / web 5273.
- **Secrets:** the Forge strips provider keys from Cell command env; keep it that
  way. The sandbox holds no secrets — never mount the API's `.env` into its
  workspace.
- **Do not** attempt Docker-based paths on this machine (no daemon); do not remove
  or degrade them either — Contabo uses them.
- **Do not** edit files via PowerShell heredocs/Set-Content (PS 5.1 UTF-16 mojibake);
  use proper editor tooling.
