<img src="docs/images (1).png" alt="S.P.E.D.A. Mark VI" width="140" />
# S.P.E.D.A. Mark VI

**Specialized Personal Executive Digital Assistant Mark VI** — a single-user, proactive, ambient AI system. FastAPI agentic backend, multi-provider LLM routing, n8n-driven automations with Telegram delivery, and an Electron desktop client.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Electron](https://img.shields.io/badge/Electron-React%2018-47848F?logo=electron&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)

---

## Overview

SPEDA is not a chatbot product — it is the engine for a personal executive assistant built around three design commitments:

- **Single-user by design.** One owner, one API key, persistent memory of who that owner is. No tenancy, no accounts.
- **Proactive, not reactive.** n8n acts as the system's senses: watchers poll pages, feeds, and schedules, and when one fires, SPEDA composes a message and delivers it to the owner's Telegram — unprompted.
- **Rebrandable core.** Identity (name, persona, model policy, prompts) lives entirely in `app/profiles/` and `app/prompts/`. The engine contains zero identity strings, so the same codebase forks into the *Superior Six* — domain-specialized sibling agents — by swapping two files.

The architectural contract for the entire system is codified in [`CLAUDE.md`](CLAUDE.md) and enforced in review.

---

## Multi-Tenant Agent Suite

SPEDA hosts a multi-tenant suite of in-process agent profiles. Each agent has a dedicated `AgentProfile` subclass containing its unique personality, system prompt template, model policy, tool allowlist, and custom visual accent theme.

| Agent | Signature Hex | Domain / Specialization | Role & Capabilities |
|---|---|---|---|
| **SPEDA** | `#5b6472` | Core Orchestrator | Central proactive companion, automation manager, and routing hub. |
| **Sentinel** | `#5b6472` | Finance & Budget Intelligence | Analyzes markets, tracks portfolios, manages frugality states, and controls cost metrics. |
| **NightCrawler** | `#9165e6` | OSINT, Web Surveillance & Research | Lawful public web search, threat intelligence tracking, onion surveillance, and web monitoring. |
| **Ultron** | `#4a90e2` | Academic Research & Knowledge Synthesis | Synthesis of scientific papers, literature searches, and complex analytical overviews. |
| **Centurion** | `#e25c5c` | Cyber Security | Vulnerability assessments, CVE analysis, port scanning, and threat intelligence. |
| **Atomix** | `#2ecc71` | Personal Health & Wellness | Personal health tracking, wellness monitoring, and exercise/nutrition logging. |
| **Optimus** | `#2eb6ac` | Systems, Code & Infrastructure | Connects to a standalone Claude Code-class peer via WebSockets for direct filesystem operations. Falls back to in-process profile when offline. |
| **WarRoom** | `#e25c5c` | Mission Command Channel | Active only during House Party Protocol as the central command node. |

---

## Inter-Agent Communication & House Party Protocol

Agents are not isolated; they communicate through a rich inter-agent dispatch framework:

- **Dynamic Routing (`dispatch_agent`):** Any agent can delegate a task to another specialist agent in-turn. The target agent runs its own reasoning loop and tools, reporting the final text back. Multiple agents can be dispatched concurrently in a single turn.
- **Shared Network Channel (`read_agent_channel`):** A shared, chronological transcript of all inter-agent traffic across the suite. Agents read this to maintain awareness and prevent redundant executions.
- **House Party Protocol (`house_party`):** A passphrase-gated all-hands mode for critical operations. When engaged:
  1. SPEDA becomes the mission commander, prompting the owner for confirmation.
  2. The UI renders the custom authorization window via a fenced `hpp-warning` block and shifts the desktop client into the **War Room** dashboard.
  3. SPEDA broadcasts tasks to the entire roster in parallel, relaxing domain boundaries and running all agents at full model grade (Sonnet-class).
  4. The state persists across restarts until stood down.

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
|Heartbreaker: Mark VI UI `packages/heartbreaker` | Primary desktop UI (Electron + React) — liquid-glass HUD design language |
| `packages/desktop` | Neutral UI template — the base that Superior Six forks re-skin |
| `packages/sandbox` | Isolated command-execution container ("capable computer") — no host mounts, resource-capped |

---

## Capability Stack

The model sees a single unified tools array; `CapabilityRegistry` is the only component that knows the difference. Registration order at startup is fixed: Tier 0 → 1 → 2 → 3.

```
┌────────────────────────────────────────────────────────────────────────┐
│                          Capability Registry                           │
├───────────────┬──────────────────────┬──────────────────┬──────────────┤
│ Tier 0: Task  │ Tier 1: Python       │ Tier 2: MCP      │ Tier 3: OSS  │
│ Sub-Agents    │ Skills (16 + OSINT)  │ Servers (12)     │ Adapters (2) │
└───────────────┴──────────────────────┴──────────────────┴──────────────┘
```

### Tier 0: Task Sub-Agents
- **Task Tool (`Task`):** Spawns isolated, billed sub-agents for heavy research tasks. Operates on a separate rate-limit pool (Haiku-class by default) and limits recursion.

### Tier 1: Core Python Skills
SPEDA executes local Python logic for core operations:
- **Automation Control (`manage_automations`):** Composition plane for n8n watchers (`web_watch`, `rss_watch`, `schedule`, `webhook`).
- **Passphrase-Gated Roster Command (`house_party`):** Triggers House Party Protocol and shifts client UI to the War Room.
- **Inter-Agent Routing (`dispatch_agent`):** Routes tasks to specialist profiles within a turn.
- **Shared Transcript Logs (`read_agent_channel`):** Accesses chronological inter-agent dispatch records.
- **Memory File Management (`memory`):** Multi-file Markdown virtual filesystem (`owner.md`, `current.md`, `dossier.md`, `preferences.md`, `log.md`, `projects.md`) using Anthropic's memory patterns.
- **Progressive Manifest Reader (`read_skill`):** Lazily retrieves full `SKILL.md` markdown guides to optimize context size.
- **Lazy-Load Trigger (`use_toolset`):** Dynamically mounts inactive MCP servers at runtime.
- **Secure Sandbox Execution (`run_command`):** Runs shell commands in an isolated Linux container with Python 3.12, pip, git, and a persistent `/workspace`.
- **Sandbox File Delivery (`deliver_file`):** Transfers files from the sandbox workspace to chat download cards.
- **Branded File Creator (`save_file`):** Writes code or text files (.html, .py, .js, .css, .json, .yaml, .csv, .env) as download cards.
- **Branded Document Generator (`generate_document`):** Compiles accent-branded A4 PDFs, DOCX, and PPTX slideshows. Includes custom DejaVu Sans family Unicode fonts for Turkish rendering.
- **Literal History Search (`search_history`):** Scans database messages by keywords and date ranges.
- **Semantic Vector Recall (`recall_conversations`):** Cosine similarity search over brute-force L2-normalized embeddings of history messages.
- **FCM Push Notifications (`send_push_notification`):** Surfacing background watcher updates to Android Flutter client.
- **Speech-to-Text (`speech_to_text`):** Audio transcription via Whisper STT.
- **Text-to-Speech (`text_to_speech`):** Speech synthesis via Kokoro TTS.
- **System Metrics Tracker (`system_info`):** Reports host server disk/memory usage and uptime.
- **Budget Control (`set_budget_mode`):** Toggles runtime frugality state.

### Tier 1: OSINT Intelligence Suite
A dedicated security and open-source intelligence library:
- `ip_geolocate`: IP geolocation queries using ip-api.
- `ip_reputation`: Reputation checks via AbuseIPDB and VirusTotal.
- `urlhaus_lookup`: Threat analysis on suspicious links via abuse.ch URLhaus.
- `threatfox_lookup`: Threat IOC lookups via abuse.ch ThreatFox.
- `malwarebazaar_lookup`: Malware file hash query via abuse.ch MalwareBazaar.
- `pwned_password_check`: Password compromise checking via HaveIBeenPwned API.
- `darkweb_search`: Search engine query over Onion resources via Ahmia.
- `otx_lookup`: AlienVault OTX threat indicator check.
- `shodan_lookup`: Direct query to Shodan API for port/service footprinting.
- `email_discovery`: Discover compromised emails and breach origins.
- `crypto_trace`: Track blockchain transaction flows.
- `intelx_search`: Query Intelligence X for credentials, dumps, or darkweb archives.

### Tier 2: Model Context Protocol (MCP) Servers
- **Notion (`notion`):** Subprocess-based connection authed with dynamic OAuth bearer tokens.
- **Google Workspace (`google_gmail`, `google_calendar`):** Custom standard Google REST API clients featuring automated OAuth token self-refreshing.
- **Brave Search (`brave_search`):** Web search integration.
- **Fetch (`fetch`):** Converts Web URLs directly to clean Markdown.
- **Alpha Vantage (`alpha_vantage`):** Financial data and market analysis.
- **Tavily (`tavily`):** High-quality web search engine.
- **Exa (`exa`):** Neural search and document retrieval.
- **GitHub (`github`):** Repository management, code browsing, and commits.
- **Filesystem (`filesystem`):** Sandboxed directory access scoped to the outputs directory.
- **arXiv (`arxiv`):** Academic literature and preprint search.
- **CVE Intelligence (`cve_intelligence`):** Vulnerability database lookup.
- **Playwright (`playwright`):** Isolated browser automation (Docker internal only).

### Tier 3: OSS Adapters
- **Deep Research (`deep_research`):** Multi-source web research engine powered by GPT-Researcher.
- **Security Analysis (`security_analysis`):** Automated vulnerability scanning and reconnaissance tool via Shannon security toolkit.

### Cost Control & Lazy Loading
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
