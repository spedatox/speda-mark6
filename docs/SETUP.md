# SPEDA Mark VI — Complete Setup Guide

Everything you need to deploy, configure, and operate the full system: Igor (the backend), the agent roster, Docker services, MCP integrations, Heartbreaker (the desktop client), and CI/CD.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Server Setup (Contabo)](#2-server-setup-contabo)
3. [Environment Configuration](#3-environment-configuration)
4. [Deploy the Stack](#4-deploy-the-stack)
5. [Post-Deploy: n8n Setup](#5-post-deploy-n8n-setup)
6. [MCP Server Configuration](#6-mcp-server-configuration)
7. [Google Workspace (OAuth)](#7-google-workspace-oauth)
8. [Telegram (Proactive Delivery)](#8-telegram-proactive-delivery)
9. [Desktop Client Build](#9-desktop-client-build)
10. [CI/CD (GitHub Actions)](#10-cicd-github-actions)
11. [Docker Services Reference](#11-docker-services-reference)
12. [Agent Roster & Tool Scoping](#12-agent-roster--tool-scoping)
13. [Environment Variable Reference](#13-environment-variable-reference)
14. [Security Hardening Checklist](#14-security-hardening-checklist)
15. [Cost Control](#15-cost-control)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. Prerequisites

**On the server (Ubuntu/Debian on Contabo or similar):**

```bash
# Docker + Compose v2
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# log out and back in, then verify:
docker compose version
```

**On your dev machine:**

- Node.js 18+ (for desktop client builds)
- Python 3.11+ with [uv](https://github.com/astral-sh/uv) (for local dev)
- PowerShell (for `build-app.ps1` — Windows or pwsh on macOS/Linux)
- Git

**Accounts / API keys you'll need:**

| Service | Required? | Get it from |
|---|---|---|
| Anthropic | **Yes** | [console.anthropic.com](https://console.anthropic.com) |
| Tavily | Recommended | [tavily.com](https://tavily.com) |
| Domain name | Recommended | Any registrar (for HTTPS via Caddy) |
| Telegram bot | Optional | [@BotFather](https://t.me/BotFather) on Telegram |
| Google Cloud OAuth | Optional | [Google Cloud Console](https://console.cloud.google.com) |
| GitHub PAT | Optional | GitHub Settings → Developer settings → Tokens |
| Alpha Vantage | Optional | [alphavantage.co](https://www.alphavantage.co/support/#api-key) |
| Exa | Optional | [exa.ai](https://exa.ai) |
| Brave Search | Optional | [brave.com/search/api](https://brave.com/search/api/) |
| Notion | Optional | [notion.so/profile/integrations](https://www.notion.so/profile/integrations) |

---

## 2. Server Setup (Contabo)

SSH into your server and clone the repo:

```bash
ssh user@your-server-ip

# Clone
git clone https://github.com/spedatox/speda-mark6.git
cd speda-mark6

# Create the secret file (gitignored — never committed)
cp packages/api/.env.example packages/api/.env
```

Point your domain's DNS A record at the server's IP address. Ports 80 and 443 must be open for Caddy's automatic HTTPS.

---

## 3. Environment Configuration

Edit `packages/api/.env` — this is the **single source of secrets** for the entire system.

### Minimum viable (gets you running)

```bash
# LLM — the one key you absolutely need
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Auth — generate strong random values
# python -c "import secrets;print(secrets.token_urlsafe(48))"
SPEDA_API_KEY=your-strong-random-api-key
N8N_SECRET=your-strong-random-n8n-secret

# Database — CHANGE THE PASSWORD (only takes effect on a fresh DB volume)
POSTGRES_USER=speda
POSTGRES_PASSWORD=your-strong-db-password
POSTGRES_DB=speda

# Domain (enables Caddy auto-HTTPS; omit for plain HTTP on :8000)
DOMAIN=speda.yourdomain.com

# Primary search (makes every agent useful on day one)
TAVILY_API_KEY=tvly-your-key
```

### Full configuration

See [Section 13: Environment Variable Reference](#13-environment-variable-reference) for every variable, its default, and what it controls.

---

## 4. Deploy the Stack

From the repo root on the server:

```bash
./deploy.sh
```

This will:

1. Check that Docker and Compose are installed
2. Read `DOMAIN` from your `.env` — if set, it starts Caddy for automatic HTTPS
3. Export Postgres credentials so the compose file interpolates them
4. Build and start all services: **postgres** → **sandbox** → **app** → **n8n** → **caddy** (if domain set)
5. Poll the `/health` endpoint until the API reports healthy (up to 120s)
6. Print the live URL

**First-time boot** automatically:
- Creates all database tables
- Runs additive schema migrations (idempotent — safe to run on every restart)
- Seeds the default owner user (ID 1)
- Registers all 7 agent profiles
- Connects available MCP servers (skips those missing API keys)

### Importing existing data

If you have an existing SQLite database from local development:

```bash
./deploy.sh --migrate ~/.speda/speda.db
```

This copies sessions, messages, and memory files from SQLite into Postgres (one-time).

### Verify

```bash
# Health check
curl https://yourdomain.com/health

# Authenticated request
curl -H "X-API-Key: your-key" https://yourdomain.com/sessions

# Check logs
docker compose logs -f app
```

---

## 5. Post-Deploy: n8n Setup

n8n is bound to localhost only (not internet-exposed). Access it via SSH tunnel:

```bash
# From your local machine:
ssh -L 5678:127.0.0.1:5678 user@your-server-ip
```

Then open `http://localhost:5678` in your browser:

1. Create your n8n owner account (first-time only)
2. Go to **Settings** → **n8n API** → **Create an API Key**
3. Copy the key and add it to `packages/api/.env`:
   ```
   N8N_API_KEY=your-n8n-api-key
   ```
4. Restart the app container:
   ```bash
   docker compose restart app
   ```

SPEDA can now create, list, and manage n8n workflows (watchers, schedules) through the `manage_automations` tool.

---

## 6. MCP Server Configuration

MCP servers degrade gracefully — if a key is missing, the server is skipped at startup and logged as degraded. Add keys as you need each integration.

### Key-only servers (add key to `.env`, restart)

| Server | Env Var | Agent(s) | What it enables |
|---|---|---|---|
| Tavily | `TAVILY_API_KEY` | All | Web search (primary) |
| Exa | `EXA_API_KEY` | All research agents | Semantic/neural search |
| Brave Search | `BRAVE_SEARCH_API_KEY` | NightCrawler | Alternative web search |
| Alpha Vantage | `ALPHA_VANTAGE_API_KEY` | Sentinel | Stock quotes, financials |
| GitHub | `GITHUB_TOKEN` | Centurion, Optimus | Repo/code/PR review |
| Notion | `NOTION_API_KEY` | All | Notion pages/databases |

### No-key servers (always available)

| Server | Transport | Agent(s) | What it does |
|---|---|---|---|
| fetch | stdio (npm) | All | Pulls web page content → markdown |
| arxiv | stdio (uvx) | Ultron, NightCrawler | Academic paper search |
| cve_intelligence | stdio (npm) | Centurion | CVE/vulnerability lookup |
| filesystem | stdio (npm) | Optimus | File operations (sandboxed to outputs dir) |

### Server enablement

Control which servers connect at startup:

```bash
# Only connect these (comma-separated)
MCP_ENABLED=tavily,google_gmail,google_calendar,notion

# Or connect everything that has a key
MCP_ENABLED=all
```

### Lazy tool loading

By default, only `always_on_servers` (Tavily) tools sit in the prompt prefix. Everything else is listed in a compact catalog and loaded on-demand via `use_toolset`. This keeps the cached prefix small and cheap.

```bash
LAZY_TOOLS=true
ALWAYS_ON_SERVERS=tavily
```

---

## 7. Google Workspace (OAuth)

Connects Gmail, Calendar, Drive, and Contacts as MCP servers.

### One-time setup

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services** → **Credentials**
2. Create an **OAuth 2.0 Client** (Desktop application type)
3. Enable the Gmail, Calendar, Drive, and People APIs
4. Add to `.env`:
   ```
   GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-client-secret
   GOOGLE_OAUTH_REDIRECT=https://yourdomain.com/oauth/google/callback
   ```
5. Restart the app. The in-app **Settings → Connections → Sign in with Google** button will handle the OAuth flow and persist the refresh token automatically.

Alternatively, run the CLI script for a headless setup:

```bash
docker compose exec app python scripts/google_oauth.py
# Follow the prompts, paste the auth code
# It prints GOOGLE_REFRESH_TOKEN — add it to .env
```

---

## 8. Telegram (Proactive Delivery)

When an n8n watcher fires, SPEDA composes a message and delivers it to your Telegram.

### Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → get the token
2. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-your-bot-token
   ```
3. Restart the app
4. In the desktop client: **Settings → Automations → Connect Telegram**
5. Tap the deep link → tap **Start** in Telegram → the chat ID is captured automatically

No public webhook needed — the backend polls `getUpdates` during a 90-second connect window.

---

## 9. Desktop Client Build

The desktop client (Heartbreaker) is an Electron + React app. Build an installer pointed at your server:

### Default build (SPEDA)

```powershell
powershell -File build-app.ps1 -ApiBase https://yourdomain.com -ApiKey your-api-key
```

### Agent-branded build

```powershell
# Build as Ultron (gray, academic research)
powershell -File build-app.ps1 -Agent ultron -ApiBase https://yourdomain.com -ApiKey your-key

# Build as Centurion (red, cyber security)
powershell -File build-app.ps1 -Agent centurion -ApiBase https://yourdomain.com -ApiKey your-key
```

Available agents: `speda`, `ultron`, `centurion`, `sentinel`, `atomix`, `nightcrawler`, `optimus`

The installer is output to `packages/heartbreaker/dist/`. The API base and key are baked into the build — the installed app talks to your server out of the box.

**In-app agent switching:** The built app defaults to the chosen agent but includes a **dropdown switcher** in the sidebar header to switch between all agents at runtime (live color morph, re-scoped sessions).

### Dev mode

```bash
# Run the SPEDA build in dev mode
npm run heartbreaker:dev

# Run as a specific agent
# (PowerShell)
$env:VITE_AGENT='centurion'; npm run heartbreaker:dev
# (Bash)
VITE_AGENT=centurion npm run heartbreaker:dev
```

---

## 10. CI/CD (GitHub Actions)

Every push to `main` that touches backend code auto-deploys to your server.

### Required GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret | Value |
|---|---|
| `SSH_HOST` | Your server's hostname or IP |
| `SSH_USER` | The deploy user on the server |
| `SSH_KEY` | That user's **private** SSH key (the public half goes in `~/.ssh/authorized_keys` on the server) |
| `SSH_PORT` | SSH port (optional, defaults to 22) |
| `DEPLOY_PATH` | Absolute path to the cloned repo on the server (e.g., `/home/deploy/speda-mark6`) |

### Generate a deploy key

```bash
# On your local machine
ssh-keygen -t ed25519 -C "speda-deploy" -f ~/.ssh/speda_deploy

# Copy the PUBLIC key to the server
ssh-copy-id -i ~/.ssh/speda_deploy.pub user@your-server-ip

# The PRIVATE key (~/.ssh/speda_deploy) goes into the SSH_KEY GitHub Secret
```

### What the workflow does

1. **Validate** (Ubuntu runner): `uv sync --frozen --no-dev` → byte-compile → import smoke test
2. **Deploy** (SSH to your server): `git reset --hard origin/main` → `./deploy.sh` → health gate

The workflow only triggers on pushes that touch: `packages/api/**`, `packages/sandbox/**`, `docker-compose.yml`, `deploy.sh`, `Caddyfile`, or the workflow itself. Concurrency control ensures only one deploy runs at a time.

---

## 11. Docker Services Reference

| Service | Image | Port | Exposed to internet? | Purpose |
|---|---|---|---|---|
| **postgres** | `postgres:16-alpine` | `127.0.0.1:5432` | No (SSH tunnel) | Database |
| **app** | Built from `packages/api/Dockerfile` | `8000` | Yes (via Caddy) | API backend |
| **n8n** | `docker.n8n.io/n8nio/n8n:latest` | `127.0.0.1:5678` | No (SSH tunnel) | Automation engine |
| **sandbox** | Built from `packages/sandbox/Dockerfile` | internal `9000` | No | Isolated command execution |
| **caddy** | `caddy:2-alpine` | `80`, `443` | Yes | Reverse proxy + auto-HTTPS |

### Volumes

| Volume | Purpose |
|---|---|
| `postgres_data` | Database files (persistent) |
| `n8n_data` | n8n workflows + state (persistent) |
| `sandbox_workspace` | Sandbox command outputs (ephemeral) |
| `caddy_data` | TLS certificates (persistent) |
| `caddy_config` | Caddy state (persistent) |

### Sandbox security

The sandbox container is resource-capped and privilege-restricted:

- `mem_limit: 1g` — max 1 GB RAM
- `cpus: 1.0` — max 1 CPU core
- `pids_limit: 256` — max 256 processes
- `security_opt: no-new-privileges` — no privilege escalation
- No host mounts, no secrets, no public port
- Only the `app` service can reach it (internal compose network)

### Postgres admin access

The database is never exposed to the internet. To administer it:

```bash
# SSH tunnel
ssh -L 5432:127.0.0.1:5432 user@your-server-ip

# Then connect locally
psql -h localhost -U speda -d speda
```

### n8n editor access

Same pattern:

```bash
ssh -L 5678:127.0.0.1:5678 user@your-server-ip
# Open http://localhost:5678
```

---

## 12. Agent Roster & Tool Scoping

7 in-process agents share one backend, one database, one event loop. Each has a unique identity, voice, and tool allowlist. They are addressed by `agent_id` on every request.

| Agent | ID | Domain | Color | Tools |
|---|---|---|---|---|
| **SPEDA** | `speda` | Orchestrator | Cyan `#36abca` | All (unrestricted) |
| **Ultron** | `ultron` | Academic research | Gray `#8a93a6` | tavily, exa, arxiv, fetch, generate_document, search_history, Task |
| **Sentinel** | `sentinel` | Finance & budget | Amber `#d99c44` | alpha_vantage, tavily, exa, fetch, generate_document, search_history, manage_automations, Task |
| **NightCrawler** | `nightcrawler` | OSINT & surveillance | Purple `#9165e6` | tavily, exa, brave_search, fetch, playwright, arxiv, generate_document, search_history, manage_automations, Task |
| **Centurion** | `centurion` | Cyber security | Red `#d8483c` | cve_intelligence, tavily, exa, fetch, github, generate_document, search_history, manage_automations, Task |
| **Atomix** | `atomix` | Personal health | Green `#3fae74` | tavily, exa, fetch, generate_document, search_history, google_calendar, Task |
| **Optimus** | `optimus` | Systems & code | Teal `#2eb6ac` | github, tavily, exa, fetch, run_command, deliver_file, generate_document, search_history, manage_automations, Task |

**Runtime-infrastructure skills** (`memory`, `read_skill`, `use_toolset`) are always available to every agent regardless of allowlist.

**Optimus** is the only agent with sandbox access (`run_command` / `deliver_file`).

**Sessions are scoped by agent** — Sentinel's conversation history never appears in Ultron's session list.

**Memory is shared** — all agents read/write the same owner memory files. Identity-safety is handled by framing the memory block as owner-knowledge ("this describes HIM, not you").

---

## 13. Environment Variable Reference

### Required

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(none)* | Anthropic Claude API key |
| `SPEDA_API_KEY` | `dev-key` | Service credential for all endpoints (`X-API-Key` header). **Change for production.** |

### Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///$HOME/.speda/speda.db` | Full connection string. Docker overrides via compose. |
| `POSTGRES_USER` | `speda` | Postgres role (compose interpolation) |
| `POSTGRES_PASSWORD` | `speda` | Postgres password. **Change for production.** Only takes effect on fresh volumes. |
| `POSTGRES_DB` | `speda` | Postgres database name |

### Auth & Security

| Variable | Default | Description |
|---|---|---|
| `N8N_SECRET` | `dev-n8n-secret` | Shared secret for n8n webhook trigger (`X-N8N-Secret`). **Change for production.** |
| `CORS_ALLOWED_ORIGINS` | *(empty)* | Comma-separated browser origins. Leave empty for desktop app. **Never `*`**. |
| `DEBUG` | `false` | Enables `/docs`, `/redoc`, `/openapi.json` + localhost CORS. **Never `true` in production.** |

### Domain & HTTPS

| Variable | Default | Description |
|---|---|---|
| `DOMAIN` | *(empty)* | Public domain for Caddy auto-HTTPS. Requires DNS A record + ports 80/443 open. |

### n8n Automation

| Variable | Default | Description |
|---|---|---|
| `N8N_API_URL` | `http://n8n:5678` | n8n REST API (internal compose network) |
| `N8N_API_KEY` | *(empty)* | n8n API key (create in n8n Settings → n8n API) |
| `SPEDA_CALLBACK_URL` | `http://app:8000` | URL n8n calls back to `/trigger`. Internal by default. |

### Telegram

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | *(empty)* | Manual fallback; normally captured via in-app connect flow |

### MCP Server Keys

| Variable | Default | Server | Agent(s) |
|---|---|---|---|
| `TAVILY_API_KEY` | *(empty)* | tavily | All |
| `EXA_API_KEY` | *(empty)* | exa | Research agents |
| `BRAVE_SEARCH_API_KEY` | *(empty)* | brave_search | NightCrawler |
| `ALPHA_VANTAGE_API_KEY` | *(empty)* | alpha_vantage | Sentinel |
| `GITHUB_TOKEN` | *(empty)* | github | Centurion, Optimus |
| `NOTION_API_KEY` | *(empty)* | notion | All |

### Google Workspace

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_CLIENT_ID` | *(empty)* | OAuth 2.0 Desktop client ID |
| `GOOGLE_CLIENT_SECRET` | *(empty)* | OAuth client secret |
| `GOOGLE_REFRESH_TOKEN` | *(empty)* | Persistent refresh token (saved by OAuth flow or CLI script) |
| `GOOGLE_OAUTH_REDIRECT` | `http://localhost:8000/oauth/google/callback` | Override for production domain |

### Multi-Provider LLM Routing

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | *(empty)* | Enables `openai:*` model refs |
| `GEMINI_API_KEY` | *(empty)* | Enables `gemini:*` model refs |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Local Ollama endpoint |
| `LLM_MAIN_MODEL` | *(empty, uses profile)* | Override the user-facing model globally |
| `LLM_BACKGROUND_MODEL` | *(empty, uses profile)* | Override the background-task model |
| `LLM_FALLBACK_CHAIN` | *(empty)* | Comma-separated fallback providers (e.g., `openai:gpt-4o,ollama:llama3.1:8b`) |

### Cost Control

| Variable | Default | Description |
|---|---|---|
| `BUDGET_MODE` | `true` | Disables the Legion + enforces concise output. Survives restarts. |
| `LEGION_MODEL_OVERRIDE` | *(empty — automatic)* | Pin every Legion worker to one `provider:model`. Empty = provider-agnostic: cheap same-provider tier for low/medium-effort legionnaires, parent model for high. Legacy alias `SUB_AGENT_MODEL` still works. |
| `COMPACTION_ENABLED` | `true` | Summarize old turns on long chats (background, Haiku) |
| `COMPACTION_THRESHOLD_TOKENS` | `12000` | Token threshold to trigger compaction |
| `COMPACTION_KEEP_TOKENS` | `4000` | Keep recent N tokens verbatim |

### Prompt Caching

| Variable | Default | Description |
|---|---|---|
| `PROMPT_CACHE_TTL` | `1h` | System prompt + tools cache lifetime (`5m` or `1h`) |
| `PROMPT_CACHE_CONVERSATION_TTL` | `5m` | Conversation history cache (cheaper write at 1.25x) |

### Tool Loading

| Variable | Default | Description |
|---|---|---|
| `MCP_ENABLED` | `tavily,google_gmail,google_calendar,notion` | Which MCP servers to connect at startup |
| `LAZY_TOOLS` | `true` | Only always-on tools in the prompt prefix |
| `ALWAYS_ON_SERVERS` | `tavily` | Tools loaded eagerly (no `use_toolset` needed) |

### Dead Zone (Offline)

| Variable | Default | Description |
|---|---|---|
| `DEAD_ZONE_MODE` | `auto` | `auto` = probe connectivity; `on` = force offline; `off` = always online |

### Misc

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Structured JSON log level |
| `TEMP_OUTPUTS_DIR` | `$HOME/.speda/outputs` | Temp file directory (24h cleanup via n8n) |
| `SANDBOX_URL` | `http://localhost:9000` | Sandbox execution container |
| `GPT_RESEARCHER_URL` | `http://localhost:8001` | GPT Researcher adapter (optional) |
| `SHANNON_URL` | `http://localhost:9000` | Shannon adapter (optional) |

---

## 14. Security Hardening Checklist

Before exposing the server to the internet:

- [ ] **Change `SPEDA_API_KEY`** from `dev-key` to a strong random value
- [ ] **Change `N8N_SECRET`** from `dev-n8n-secret` to a strong random value
- [ ] **Change `POSTGRES_PASSWORD`** from `speda` to a strong value (only on fresh volumes)
- [ ] **Set `DEBUG=false`** (disables `/docs`, `/redoc`, `/openapi.json`)
- [ ] **Set `DOMAIN`** so Caddy provisions HTTPS (API should never be plain HTTP on the internet)
- [ ] **Leave `CORS_ALLOWED_ORIGINS` empty** (the desktop app doesn't need CORS; never use `*`)
- [ ] **Verify ports**: Postgres (`5432`) and n8n (`5678`) are bound to `127.0.0.1` only
- [ ] **Firewall**: only ports 80, 443, and SSH open to the internet

### What's enforced automatically

- `AuthMiddleware`: every request requires a valid `X-API-Key` (constant-time comparison)
- `SecurityHeadersMiddleware`: HSTS, CSP `default-src 'none'`, `X-Frame-Options: DENY`, `nosniff`, server banner scrubbed
- Global exception handler: unhandled errors return generic `500` — no stack traces, paths, or SQL leak
- `/docs` and `/openapi.json` disabled outside `DEBUG`
- `POST /trigger/{agent_id}` additionally requires `X-N8N-Secret`

### Credential generation

```bash
# Strong API key
python -c "import secrets;print(secrets.token_urlsafe(48))"

# Strong database password
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

---

## 15. Cost Control

### Built-in mechanisms

| Mechanism | What it does | Config |
|---|---|---|
| **Prompt caching** | 1h cache on system prompt + tools (0.1x read after first turn) | `PROMPT_CACHE_TTL` |
| **Conversation compaction** | Summarizes old turns so long chats don't send the full transcript | `COMPACTION_*` |
| **Budget mode** | Disables the Legion + enforces concise output | `BUDGET_MODE=true` |
| **Per-agent tool scoping** | Specialists have smaller tool blocks (fewer prompt tokens) | Profile allowlists |
| **Lazy tool loading** | MCP tools only enter the prefix when needed | `LAZY_TOOLS=true` |
| **Cheap tier for grunt work** | Title gen, session log, compaction, and low/medium-effort legionnaires all use the provider's cheap model | automatic (`LEGION_MODEL_OVERRIDE` to pin) |

### Typical cost (Claude Sonnet 4.6)

| Usage | Turns/day | Monthly estimate |
|---|---|---|
| Light | 10 | ~$4-5 |
| Moderate | 25 | ~$10-15 |
| Heavy | 50+ | ~$25-40 |

---

## 16. Troubleshooting

### API won't start

```bash
docker compose logs app
```

Common causes:
- Missing `ANTHROPIC_API_KEY` in `.env`
- Postgres not healthy yet (the app waits for it, but check `docker compose logs postgres`)
- Python import error (check the logs for the traceback)

### MCP server skipped

Check the startup logs for:
```
mcp_skip  {"server": "alpha_vantage", "reason": "ALPHA_VANTAGE_API_KEY not set"}
```

This is expected — add the key to `.env` and restart.

### Can't reach n8n editor

n8n is localhost-only. Use an SSH tunnel:

```bash
ssh -L 5678:127.0.0.1:5678 user@your-server
# Then open http://localhost:5678
```

### Caddy not provisioning HTTPS

- DNS A record must point at the server
- Ports 80 and 443 must be open (check firewall)
- `DOMAIN` must be set in `.env`
- The domain profile must be active: `deploy.sh` handles this automatically

### Desktop app can't connect

- Verify the API is reachable: `curl https://yourdomain.com/health`
- Check the API key matches: the key baked into the build must match `SPEDA_API_KEY` in `.env`
- If HTTPS: verify the certificate is valid (Caddy handles this, but DNS must be correct)

### High token costs

- Enable budget mode: `BUDGET_MODE=true`
- Enable compaction: `COMPACTION_ENABLED=true`
- Leave `LEGION_MODEL_OVERRIDE` empty (default) — low/medium legionnaires then use the cheap tier automatically
- Use lazy tool loading: `LAZY_TOOLS=true`
- Review session lengths — very long conversations are the biggest cost driver even with compaction

### Resetting the database

```bash
docker compose down
docker volume rm speda-mark6_postgres_data
docker compose up -d
# Fresh database created on next boot
```

**Warning:** this deletes all sessions, messages, and memory. Export first if needed.
