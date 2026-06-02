# SPEDA Mark VI — Deployment Runbook

Move the backend to a server (Docker) and ship the desktop app. Written for a
fresh Hetzner CX33 (or any Ubuntu box) but works anywhere Docker runs.

---

## 0. What you're deploying

```
server (Docker)                         your PC
┌────────────────────────────┐          ┌─────────────────────┐
│ postgres   (data + memory) │          │ SPEDA Mark VI .exe  │
│ app        (FastAPI :8000) │◀── API ──│ (Electron desktop)  │
│ sandbox    (capable comp.) │          └─────────────────────┘
└────────────────────────────┘
```

The desktop app talks to the server over HTTP. Postgres holds all sessions,
messages, and memory (migrated from your current SQLite — no re-indexing).

---

## 1. Provision the server

1. Create a Hetzner CX33 (4 vCPU / 8 GB / 80 GB), Ubuntu 24.04.
2. SSH in, install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```
3. Open the API port (or front it with a reverse proxy + TLS later):
   ```bash
   ufw allow 8000/tcp && ufw allow OpenSSH && ufw enable
   ```

## 2. Get the code + secrets onto the server

```bash
git clone <your-repo> speda && cd speda
# Create packages/api/.env (or repo-root .env that compose reads) with your keys:
#   ANTHROPIC_API_KEY=...
#   TAVILY_API_KEY=...
#   SPEDA_API_KEY=<pick a strong shared secret>   # the desktop app must match
#   MCP_ENABLED=tavily            # keep lean; add more when on a higher tier
#   BUDGET_MODE=true
```

## 3. Bring up Postgres + sandbox first

```bash
docker compose up -d postgres sandbox
```

## 4. Migrate your existing data (ONE TIME — preserves the 1989 facts)

Copy your local `~/.speda/speda.db` to the server, then run the migrator from
inside a temporary app container (or locally pointing at the server's Postgres):

```bash
# from packages/api, with the venv:
python scripts/migrate_sqlite_to_postgres.py \
  --source /path/to/speda.db \
  --dest "postgresql+asyncpg://speda:speda@localhost:5432/speda"
```

Verify first with `--dry-run`. Expect ~440 sessions / ~14k messages / 7 memory
files. This is why you never re-pay for indexing.

## 5. Start the API

```bash
docker compose up -d app
docker compose logs -f app      # watch for startup_complete
curl http://localhost:8000/health
```

## 6. Point the desktop app at the server + build it

On your PC, set the server URL + matching key, then build the installer:

```powershell
$env:SPEDA_API_BASE = "http://<server-ip>:8000"
$env:SPEDA_API_KEY  = "<same secret as the server .env>"
cd packages/heartbreaker
npm install          # first time — pulls electron-builder
npm run dist         # -> dist/SPEDA Mark VI-0.1.0-setup.exe
```

Install the `.exe`. The app reads `SPEDA_API_BASE` / `SPEDA_API_KEY` at launch,
so to re-point it later just relaunch with different env values (or set them as
system environment variables so they persist).

> Unsigned build → Windows SmartScreen will warn once ("More info → Run anyway").
> Expected for an in-house app.

---

## The "capable computer" (sandbox)

`docker compose` runs a `sandbox` service — an isolated container (no secrets, no
host mounts, 1 GB / 1 CPU / 256 pids, no-new-privileges). SPEDA's `run_command`
tool executes shell/Python in it; files and installed packages persist in the
`sandbox_workspace` volume. It's only reachable from the `app` over the internal
Docker network.

## Future — n8n (proactivity)

Add an `n8n` service to the same compose network, point its HTTP nodes at
`http://app:8000`, and use the existing `X-API-Key` auth. Wire triggers
(schedules, webhooks) to drive SPEDA proactively.

---

## Quick reference

| Action | Command |
|---|---|
| Bring everything up | `docker compose up -d` |
| Tail API logs | `docker compose logs -f app` |
| Rebuild after code change | `docker compose up -d --build app` |
| Migrate data (dry run) | `python scripts/migrate_sqlite_to_postgres.py --dry-run` |
| Build desktop installer | `npm run dist` (in packages/heartbreaker) |
| Toggle budget mode | UI button, ask SPEDA, or `BUDGET_MODE` in .env |
