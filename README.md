# SPEDA Mark VI

**Specialized Personal Executive Digital Assistant** — a single-user, proactive, ambient AI system. FastAPI agentic backend, multi-provider LLM routing, n8n-driven automations with Telegram delivery, and an Electron desktop client.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Electron](https://img.shields.io/badge/Electron-React%2018-47848F?logo=electron&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)

---

## Overview

SPEDA is not a chatbot product — it is the engine for a personal executive assistant built around three design commitments:

- **Single-user by design.** One owner, one API key, persistent memory of who that owner is. No tenancy, no accounts.
- **Proactive, not reactive.** n8n acts as the system's senses: watchers poll pages, feeds, and schedules, and when one fires, SPEDA composes a message and delivers it to the owner's Telegram — unprompted.
- **Rebrandable core.** Identity (name, persona, model policy, prompts) lives entirely in `app/profiles/` and `app/prompts/`. The engine contains zero identity strings, so the same codebase forks into the *Superior Six* — domain-specialized sibling agents (Sentinel, NightCrawler, Ultron, Optimus, Unicron, Ratchet) — by swapping two files.

The architectural contract for the entire system is codified in [`CLAUDE.md`](CLAUDE.md) and enforced in review.

---

## System Architecture

```
                        ┌──────────────────────────── server (Docker) ───────────────────────────┐
┌─────────────────┐     │  ┌────────────────────────────┐   ┌──────────┐   ┌──────────────────┐  │
│  Desktop client │     │  │  app (FastAPI :8000)       │   │ postgres │   │ n8n (:5678)      │  │
│  Electron+React │◀───▶│  │  ┌──────────────────────┐  │◀─▶│ sessions │   │ watchers:        │  │
│  SSE / WebSocket│     │  │  │ AgentOrchestrator    │  │   │ memory   │   │ cron · web · rss │  │
└─────────────────┘     │  │  │  agentic loop        │  │   └──────────┘   └────────┬─────────┘  │
                        │  │  └──────────┬───────────┘  │                           │            │
┌─────────────────┐     │  │  ┌──────────▼───────────┐  │   ┌──────────┐   POST /trigger/speda   │
│  Telegram (push)│◀────│──│  │ CapabilityRegistry   │  │◀─▶│ sandbox  │            │            │
└─────────────────┘     │  │  │  4-tier tool routing │  │   │ isolated │◀───────────┘            │
                        │  │  └──────────────────────┘  │   │ executor │                         │
                        │  └────────────────────────────┘   └──────────┘                         │
                        └────────────────────────────────────────────────────────────────────────┘
```

**Request flow.** Routers contain no business logic: every request constructs an `AgentContext` and calls `orchestrator.run(context)`, which streams `SSEEvent`s. The agentic loop handles all stop reasons explicitly (`end_turn`, `tool_use`, `max_tokens`, `pause_turn`), executes tool calls in parallel, and runs until completion with a hard 30-iteration safety guard.

**Monorepo layout.**

| Package | Role |
|---|---|
| `packages/api` | FastAPI backend — orchestrator, capability registry, skills, memory, automations |
| `packages/heartbreaker` | Primary desktop UI (Electron + React) — liquid-glass HUD design language |
| `packages/desktop` | Neutral UI template — the base that Superior Six forks re-skin |
| `packages/sandbox` | Isolated command-execution container ("capable computer") — no host mounts, resource-capped |

---

## Capability Stack

The model sees a single unified tools array; `CapabilityRegistry` is the only component that knows the difference. Registration order at startup is fixed: Tier 0 → 1 → 2 → 3.

| Tier | Type | Examples |
|---|---|---|
| 0 | Task sub-agents | Parallel research workers on an isolated loop and a separate rate-limit pool |
| 1 | Python skills | memory, document generation (PDF/DOCX/PPTX), TTS/STT, sandbox execution, history search, budget mode, **automation management** |
| 2 | MCP servers | Tavily, Exa, Notion, GitHub, Brave, Alpha Vantage, arXiv, Fetch, Google Workspace (OAuth) |
| 3 | OSS adapters | gpt-researcher, Shannon — wrapped over HTTP |

Two cost-control mechanisms are built into the registry:

- **Lazy tool loading (progressive disclosure).** Only always-on servers occupy the prompt prefix; everything else is listed in a compact catalog and pulled in on demand via `use_toolset`. Combined with 1-hour prompt caching, the static prefix is effectively written once and read forever.
- **Budget mode.** A hard, runtime-toggleable frugality switch: sub-agents unregistered, concise-output directive injected. Persists across restarts.

---

## Multi-Provider LLM Routing

All model calls go through a single `LLMClient` ([`services/llm_client.py`](packages/api/app/services/llm_client.py)). Model references are `provider:model` strings — bare names route to Anthropic for backward compatibility.

| Provider | Reference format | Transport |
|---|---|---|
| Anthropic (primary) | `claude-sonnet-4-6` | Native SDK, pass-through with prompt caching intact |
| OpenAI | `openai:gpt-5.1` | Chat Completions |
| Google Gemini | `gemini:gemini-2.5-flash` | OpenAI-compatibility endpoint |
| Ollama (local, dev) | `ollama:llama3.1:8b` | Local `/v1`, installed models listed live |

The wire boundary is the only place translation happens — internally the entire system speaks Anthropic content-block format exclusively. Responses from every provider are normalized to identical shapes (content blocks, stop reasons, usage), so the orchestrator, sub-agents, and background services are provider-agnostic. A configurable **fallback chain** (`LLM_FALLBACK_CHAIN`) retries the next provider on failure; the UI model picker groups available models by provider and only shows providers with configured credentials.

---

## Proactive Automations

n8n is the sole scheduling organ — the backend contains no internal scheduler. SPEDA operates as a **control plane** over n8n's REST API and composes workflows itself:

```
"Track this page for a month and tell me when results are up."
        │
        ▼
manage_automations tool → structured spec (kind, url, look_for, duration_days, intent)
        │
        ▼
Composer assembles validated n8n JSON from a pinned block library → POST to n8n → activate
        │                                  (watcher fires)
        ▼
n8n → POST /trigger/speda → orchestrator composes the message → Telegram delivery
```

- **Watcher kinds:** `web_watch` (change/keyword detection with edge-triggering — one ping when the keyword appears, not one per poll), `rss_watch`, `schedule` (cron briefings), `webhook` (inbound URL).
- **Time-boxing:** "for a month" becomes an expiry gate enforced inside the workflow itself; expired watchers self-deactivate.
- **Management:** in conversation (create/list/pause/resume/delete) or in **Settings → Automations** (pipeline status, Telegram connect, per-watcher toggle/delete).
- **Telegram linking:** one-time deep-link flow — tap *Start*, the chat id is captured automatically.

---

## Memory

A two-layer system gives SPEDA genuine continuity:

1. **File-based memory** (Anthropic memory pattern): `owner.md`, `current.md`, `dossier.md`, `log.md`, `history.md` — readable and editable by the agent through a memory tool, injected into the system prompt as a cached block.
2. **Background consolidation:** after each exchange, Haiku-class background tasks update the session log, refresh the recency snapshot, and maintain a behavioural dossier. A one-time importer mines facts from exported chat history (`/admin/import-chats` → `/admin/index-history`).

Memory work never blocks the stream — extraction and title generation are background tasks by rule.

---

## Getting Started

### Prerequisites

| Tool | Version |
|---|---|
| Python | 3.11+ (managed via [uv](https://docs.astral.sh/uv/)) |
| Node.js | 18+ |
| Docker | with Compose v2 |

### Configure

```bash
cp .env.example packages/api/.env
# Required: ANTHROPIC_API_KEY, SPEDA_API_KEY
# Optional: provider keys, MCP keys, N8N_API_KEY, TELEGRAM_BOT_TOKEN
```

### Run — development

```bash
# Backend (SQLite by default — no services required)
cd packages/api
uv sync
uv run uvicorn app.main:app --port 8000 --reload

# UI (repo root, separate terminal)
npm install
npm run heartbreaker:dev        # primary UI
npm run heartbreaker:web:dev    # browser-only, no Electron
```

On Windows, `speda.ps1` starts backend and UI with one command.

### Run — full stack (Docker)

```bash
docker compose up -d            # postgres + app + sandbox + n8n
```

n8n's editor is exposed on `:5678` — create the owner account, generate an API key (*Settings → n8n API*), and set `N8N_API_KEY` in `.env`.

### Deploy — production

```bash
./deploy.sh                     # one-shot: stack + domain/TLS (Caddy) + memory import + app build
```

See [`DEPLOY.md`](DEPLOY.md) for the full runbook (server provisioning, domain setup, SQLite → Postgres migration, desktop packaging).

---

## Configuration Reference

All settings load from `packages/api/.env` via pydantic-settings. Key variables:

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Primary model provider |
| `SPEDA_API_KEY` | yes | API auth — `X-API-Key` on every endpoint |
| `DATABASE_URL` | no | Defaults to local SQLite; `postgresql+asyncpg://…` in production |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` / `OLLAMA_BASE_URL` | no | Additional LLM providers |
| `LLM_MAIN_MODEL` / `LLM_BACKGROUND_MODEL` | no | Override the profile's model policy (`provider:model`) |
| `LLM_FALLBACK_CHAIN` | no | Comma-separated providers tried in order on failure |
| `N8N_API_URL` / `N8N_API_KEY` | no | Automation control plane |
| `N8N_SECRET` | yes* | Shared secret for inbound `/trigger` calls |
| `TELEGRAM_BOT_TOKEN` | no | Proactive delivery channel |
| `MCP_ENABLED` / `ALWAYS_ON_SERVERS` | no | Which MCP servers connect / sit in the prompt prefix |
| `BUDGET_MODE` | no | Default frugality state (runtime-toggleable) |
| `TAVILY_API_KEY`, `NOTION_API_KEY`, `GITHUB_TOKEN`, … | no | Per-MCP credentials — servers degrade gracefully when absent |

See [`.env.example`](.env.example) for the complete annotated list.

---

## HTTP API

All endpoints require `X-API-Key` (middleware-enforced) unless noted.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + registered tool count *(unauthenticated)* |
| `POST` | `/chat` | User message → SSE stream |
| `WS` | `/ws` | WebSocket chat |
| `GET` | `/models` | Available models across configured providers |
| `GET/PATCH/DELETE` | `/sessions…` | Session list, history, rename, delete |
| `GET/POST` | `/budget-mode` | Read / toggle budget mode |
| `GET/POST` | `/connections…` | MCP server status + live toggling; Google OAuth flow |
| `GET/POST/DELETE` | `/automations…` | Watcher list, toggle, delete; pipeline status; Telegram connect |
| `POST` | `/trigger/{agent_id}` | n8n callback — additionally requires `X-N8N-Secret` |
| `GET` | `/files/{name}` | Download generated artifacts |
| `POST` | `/admin/import-chats` · `/admin/index-history` | Chat-history import and memory mining |
| `DELETE` | `/admin/outputs` | Temp-file cleanup (called by n8n daily) |
| `GET` | `/agents` · `WS /agents/ws/{id}` | Superior Six presence registry |

---

## Extending

**Add a skill.** Drop a `SKILL.md` (YAML frontmatter) into `app/skills/skill_docs/<name>/` — the prompt manifest rebuilds automatically. Back it with a `Skill` subclass in `app/skills/` and register it in `main.py` if it needs code. Tool descriptions follow a strict authoring rule: what it does, when to use it, when *not* to, and what it returns.

**Add an MCP server.** One entry in `app/mcp/servers.py`. STDIO for local servers; HTTP/OAuth only for officially managed remotes.

**Fork an agent.** An agent is exactly two files:

| File | Controls |
|---|---|
| `packages/api/app/prompts/core/01_identity.md` | Personality, voice, boundaries, domain — backend display name derives from its heading |
| `packages/<ui-fork>/src/renderer/src/profile/config.ts` | Name, accent, tagline, suggested prompts |

---

## Engineering Rules

The non-negotiables, enforced via [`CLAUDE.md`](CLAUDE.md):

- Routers contain zero business logic — construct context, call `orchestrator.run()`, stream.
- The system prompt is owned exclusively by `AgentOrchestrator`.
- `AgentContext` is the single source of truth for request state; no module-level globals — everything lives on `app.state`.
- The agentic loop never breaks early on `tool_use`; it runs to `end_turn` under a 30-iteration guard.
- Model IDs live only in `app/profiles/`; identity strings never enter core modules.
- Anthropic content-block wire format exclusively; provider translation happens only at the LLM-client boundary.
- All scheduling belongs to n8n. The backend never grows a cron.
- Every endpoint authenticates. Generated files are temporary (`24h` cleanup via n8n).

---

## Project Structure

```
speda-mark6/
├── packages/
│   ├── api/app/
│   │   ├── core/            # orchestrator, capability registry, context, sessions
│   │   ├── services/        # llm_client (multi-provider), telegram, n8n, memory
│   │   ├── automations/     # workflow composer + manager (n8n control plane)
│   │   ├── skills/          # Tier-1 skills + SKILL.md docs
│   │   ├── mcp/             # Tier-2 server registry
│   │   ├── adapters/        # Tier-3 OSS adapters
│   │   ├── profiles/        # identity + model policy (fork point)
│   │   ├── prompts/         # file-based system prompt assembly
│   │   ├── routers/         # thin HTTP/WS surface
│   │   └── models/          # SQLAlchemy ORM
│   ├── heartbreaker/        # primary Electron UI
│   ├── desktop/             # neutral UI template (fork base)
│   └── sandbox/             # isolated execution container
├── docker-compose.yml       # postgres · app · sandbox · n8n · caddy
├── deploy.sh / DEPLOY.md    # production runbook
├── CLAUDE.md                # architectural contract
└── speda.ps1                # Windows dev launcher
```

---

## Development

```bash
npm run heartbreaker:typecheck   # UI type safety
npm run heartbreaker:build       # production build
uv run pytest                    # backend tests (packages/api)
```

---

## Status & Roadmap

The core platform is operational: agentic loop, four-tier capabilities, multi-provider routing, file-based memory, proactive automations with Telegram delivery, and production deployment. In progress:

- Voice pipeline hardening (Kokoro TTS / Whisper STT services)
- Superior Six rollout — per-agent deployments on the rebrandable core
- House Party Protocol — coordinated multi-agent operation (deliberately parked until all six agents are live)

---

**Author:** Ahmet Erol Bayrak ([@spedatox](https://github.com/spedatox)) · Private project — not licensed for redistribution.
