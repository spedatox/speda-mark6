# Speda Mark VI — Deployment Runbook

Move the backend to a server (Docker) and ship the desktop app. Written for a
fresh Hetzner CX33 (or any Ubuntu box) but works anywhere Docker runs.

---

## 0. What you're deploying

```
server (Docker)                          your PC
┌─────────────────────────────┐          ┌─────────────────────┐
│ app       (FastAPI :8000)   │◀── API ──│ Speda Mark VI .exe  │
│ sandbox   (capable compute) │          │ (Electron desktop)  │
│ browser   (Playwright)      │          └─────────────────────┘
│ caddy     (TLS, optional)   │
└─────────────────────────────┘
```

The desktop app talks to the server over HTTP.

**The database is SQLite**, at `/root/.speda/speda.db` on the server, pinned by
`DATABASE_URL` in `docker-compose.yml`. There is no database service: a postgres
container ran here until 2026-08-22, dormant since the SQLite cutover on
2026-07-13 and written to by nothing. Its final dump is kept at
`/root/backups/postgres-speda-2026-08-22.sql.gz`. Single-user workload, one
file, backed up by copying it — do not add a database service back without
moving `DATABASE_URL` along with it.

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
# Compose reads packages/igor/.env — copy the template and fill it in:
cp packages/igor/.env.example packages/igor/.env
# Required: ANTHROPIC_API_KEY, SPEDA_API_KEY (the desktop app must send the same).
# Optional: TAVILY_API_KEY, NOTION_API_KEY, GOOGLE_* etc. See the file's comments.
```

## 3. Bring up the sandbox first

```bash
docker compose up -d sandbox
```

## 4. Bring your existing data (ONE TIME)

The database is a single SQLite file. Copy your local `~/.speda/speda.db` to the
server at `/root/.speda/speda.db` before first boot and the whole history —
sessions, messages, memory files, embeddings — is simply there. No migration
step, and nothing to re-index.

```bash
scp ~/.speda/speda.db root@<server>:/root/.speda/speda.db
```

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
npm run dist         # -> dist/Speda Mark VI-0.1.0-setup.exe
```

Install the `.exe`. The app reads `SPEDA_API_BASE` / `SPEDA_API_KEY` at launch,
so to re-point it later just relaunch with different env values (or set them as
system environment variables so they persist).

> Unsigned build → Windows SmartScreen will warn once ("More info → Run anyway").
> Expected for an in-house app.

---

## The "capable computer" (sandbox)

`docker compose` runs a `sandbox` service — an isolated container (no secrets, no
host mounts, 1 GB / 1 CPU / 256 pids, no-new-privileges). Speda's `run_command`
tool executes shell/Python in it; files and installed packages persist in the
`sandbox_workspace` volume. It's only reachable from the `app` over the internal
Docker network.

## Future — n8n (proactivity)

Add an `n8n` service to the same compose network, point its HTTP nodes at
`http://app:8000`, and use the existing `X-API-Key` auth. Wire triggers
(schedules, webhooks) to drive Speda proactively.

---

## Quick reference

| Action | Command |
|---|---|
| Bring everything up | `docker compose up -d` |
| Tail API logs | `docker compose logs -f app` |
| Rebuild after code change | `docker compose up -d --build app` |
| Back up the database | `scp root@<server>:/root/.speda/speda.db ./speda-backup.db` |
| Build desktop installer | `npm run dist` (in packages/heartbreaker) |
| Toggle budget mode | UI button, ask Speda, or `BUDGET_MODE` in .env |
