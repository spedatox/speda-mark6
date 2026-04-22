# SPEDA Mark VI

**Specialized Personal Executive Digital Assistant — backend core.**

Single-user, proactive ambient AI built on FastAPI, Claude (Anthropic), and PostgreSQL. SPEDA is the orchestrator. The Superior Six (Sentinel, NightCrawler, Ultron, Optimus, Unicron, Ratchet) are separate microservices that fork this repo and swap identity profiles.

Deployment target: Contabo Cloud.

---

## Status

| Layer | Component | Status |
|---|---|---|
| Core | Agentic loop, orchestrator, SSE streaming | ✅ Live |
| Core | Session management + conversation history | ✅ Live |
| Core | CapabilityRegistry (4-tier) | ✅ Live |
| Core | AgentContext — single source of truth for request state | ✅ Live |
| Core | WebSocket agent connections (Superior Six) | ✅ Live |
| Auth | API key middleware (`X-API-Key`) | ✅ Live |
| Auth | n8n dual auth (`X-N8N-Secret`) | ✅ Live |
| DB | PostgreSQL — all 6 ORM models | ✅ Live |
| Profiles | SPEDA identity, system prompt, model allocation | ✅ Live |
| Skill | `system_info` — disk / memory / uptime | ✅ Live |
| Skill | `text_to_speech` (Kokoro TTS) | 🔧 Stub — Kokoro not deployed |
| Skill | `speech_to_text` (Whisper STT) | 🔧 Stub — Whisper not deployed |
| Skill | `send_push_notification` (FCM) | 🔧 Stub — FCM not configured |
| Skill | `generate_document` (PPTX/DOCX/PDF) | 🔧 Stub — not implemented |
| MCP | All 12 server configs registered | 🔧 Stub — SDK not wired |
| Adapter | `deep_research` (gpt-researcher) | ⚠️ HTTP ready — service not deployed |
| Adapter | `security_analysis` (Shannon) | ⚠️ HTTP ready — service not deployed |
| Task | Sub-agent spawning (Tier 0) | 🔧 Stub — Agent SDK not wired |
| Background | Memory extraction, session title generation | 🔧 Stub — never invoked |

---

## Architecture

```
POST /chat  ──▶  APIKeyMiddleware  ──▶  ChatRouter  ──▶  AgentOrchestrator
                                                               │
                                            ┌──────────────────┤
                                            │                  │
                                     build_system_prompt   agentic loop
                                       (profile only)      (30-iter cap)
                                                               │
                                                     CapabilityRegistry.execute()
                                                               │
                                         ┌─────────────────────────────────────┐
                                         │ Tier 0   Task (sub-agent spawner)   │
                                         │ Tier 1   Python Skills              │
                                         │ Tier 2   MCP Servers (12)           │
                                         │ Tier 3   OSS Adapters               │
                                         └─────────────────────────────────────┘
```

Three transport channels — do not confuse them:

| Channel | Protocol | Used For |
|---|---|---|
| `POST /chat` | HTTP + SSE | Flutter user chat — streams response |
| `WS /ws` | WebSocket | Flutter real-time / voice (low-latency) |
| `WS /agents/ws/{id}` | WebSocket | Superior Six agent connections only |

Two trigger sources:

| Source | Auth | `triggered_by` | `output_mode` |
|---|---|---|---|
| User (Flutter) | `X-API-Key` | `"user"` | `"respond"` |
| n8n automation | `X-API-Key` + `X-N8N-Secret` | `"n8n"` | `"push"` or `"silent"` |

---

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Server status + tool count |
| `POST` | `/chat` | API key | User chat — SSE stream |
| `WS` | `/ws` | API key | Flutter WebSocket chat |
| `POST` | `/trigger/{agent_id}` | API key + n8n secret | n8n webhook trigger |
| `GET` | `/agents` | API key | List online Superior Six agents |
| `WS` | `/agents/ws/{agent_id}` | API key | Agent WebSocket connection |
| `DELETE` | `/admin/outputs` | API key | Clean `/tmp/speda_outputs/` (called by n8n daily) |

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Docker + Docker Compose

### Run locally

```bash
# 1. Clone
git clone https://github.com/spedatox/speda-mark6.git
cd speda-mark6

# 2. Install dependencies
uv sync

# 3. Create .env
cp .env.example .env
# Fill in ANTHROPIC_API_KEY and SPEDA_API_KEY

# 4. Start Postgres
docker compose up -d postgres

# 5. Start the server
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# 6. Test
curl http://localhost:8000/health
# {"status":"ok","tools_registered":8}
```

### Terminal client

```bash
# Interactive REPL
.venv/bin/python speda.py

# Single shot
.venv/bin/python speda.py "check system status"

# Piped
echo "what time is it?" | .venv/bin/python speda.py
```

REPL commands: `new` — start fresh session | `exit` — quit

### Docker (full stack)

```bash
cp .env.example .env  # fill in keys
docker compose up
```

---

## Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...
SPEDA_API_KEY=your-api-key

# Optional — defaults work for local dev
DATABASE_URL=postgresql+asyncpg://speda:speda@localhost:5432/speda
N8N_SECRET=your-n8n-secret
LOG_LEVEL=INFO

# MCP servers — skip to degrade gracefully
NOTION_API_KEY=
BRAVE_SEARCH_API_KEY=
TAVILY_API_KEY=
EXA_API_KEY=
GITHUB_TOKEN=
ALPHA_VANTAGE_API_KEY=

# OSS Adapters
GPT_RESEARCHER_URL=http://localhost:8001
SHANNON_URL=http://localhost:9000
```

---

## Capability Tiers

| Tier | Type | How to add |
|---|---|---|
| 0 | Task (SDK built-in sub-agent spawner) | Registered once at startup, before all others |
| 1 | Python Skill | Drop a file in `app/skills/`, register in `main.py` |
| 2 | MCP Server | Add a `MCPClient` entry in `app/mcp/servers.py` |
| 3 | OSS Adapter | Drop a file in `app/adapters/`, register in `main.py` |

Claude sees all four tiers identically in the tools array. The registry is the only entity that knows the difference.

---

## MCP Servers (Tier 2)

12 servers are configured. All require the MCP SDK to be wired in `app/mcp/client.py` to function.

| Server | Transport | Key Required |
|---|---|---|
| Notion | HTTP/SSE | `NOTION_API_KEY` |
| Google Workspace (Gmail + Calendar) | HTTP/SSE | OAuth 2.1 |
| Brave Search | stdio | `BRAVE_SEARCH_API_KEY` |
| Fetch | stdio | — |
| Alpha Vantage | HTTP/SSE | `ALPHA_VANTAGE_API_KEY` |
| Tavily | stdio | `TAVILY_API_KEY` |
| Exa | stdio | `EXA_API_KEY` |
| GitHub | stdio | `GITHUB_TOKEN` |
| Filesystem | stdio | — (sandboxed to `/tmp/speda_outputs`) |
| arXiv | stdio | — |
| CVE Intelligence | stdio | — |
| Playwright | HTTP (isolated container) | CVE-2025-9611 — internal network only |

---

## Forking for the Superior Six

Each of the Superior Six agents forks this repo and swaps one file:

```bash
cp app/profiles/speda.py app/profiles/sentinel.py
# Edit: name, domain, system_prompt_template, allocate_model() policy
```

The engine — orchestrator, registry, session manager, routers — is never touched.

---

## What Needs to Be Built

### Next up

1. **MCP SDK integration** — wire `mcp.ClientSession` in `app/mcp/client.py` for stdio and HTTP transports. Unlocks all 12 MCP servers.
2. **Sub-agent Task tool** — wire Anthropic Agent SDK in `app/core/registry.py:_execute_task()`. Unlocks parallel research and multi-source synthesis.
3. **Push notification delivery** — implement FCM in `app/skills/notifications.py` and hook it into the trigger router for `output_mode="push"`.

### Then

4. **Kokoro TTS** — deploy on Contabo, wire HTTP client in `app/skills/tts.py`
5. **Whisper STT** — deploy on Contabo, wire HTTP client in `app/skills/stt.py`
6. **Document generation** — implement PPTX/DOCX/PDF in `app/skills/documents.py`
7. **Memory extraction** — implement `extract_memory()` in `app/services/memory.py`
8. **WebSocket chat** — full bidirectional orchestrator loop in `WS /ws`

### Later

9. Deploy gpt-researcher (deep research adapter)
10. Deploy Shannon (security analysis adapter)
11. House Party Protocol — inter-agent comms (parked until all six agents are live)

---

## Project Structure

```
speda-mark-vi/
├── speda.py                     # Terminal client (REPL + single-shot)
├── app/
│   ├── main.py                  # Lifespan handler + app factory
│   ├── config.py                # Settings + structured JSON logging
│   ├── database.py              # Async SQLAlchemy engine + default user seed
│   ├── middleware/auth.py       # API key validation — all routes
│   ├── profiles/
│   │   ├── base.py              # AgentProfile ABC
│   │   └── speda.py             # SPEDA identity — fork for Superior Six
│   ├── core/
│   │   ├── orchestrator.py      # Agentic loop + system prompt ownership
│   │   ├── context.py           # AgentContext — single source of truth
│   │   ├── registry.py          # CapabilityRegistry — all four tiers
│   │   ├── agent_registry.py    # Superior Six presence tracking
│   │   └── session_manager.py   # Session lifecycle + history
│   ├── routers/
│   │   ├── chat.py              # POST /chat (SSE), WS /ws
│   │   ├── trigger.py           # POST /trigger/{agent_id} — n8n
│   │   ├── agents.py            # GET /agents, WS /agents/ws/{id}
│   │   ├── admin.py             # DELETE /admin/outputs
│   │   └── health.py            # GET /health
│   ├── skills/                  # Tier 1 — Python skills
│   ├── mcp/                     # Tier 2 — MCP server client + registrations
│   ├── adapters/                # Tier 3 — OSS adapter wrappers
│   ├── models/                  # SQLAlchemy ORM models
│   ├── schemas/                 # Pydantic request/response schemas
│   ├── services/                # Anthropic client, memory, n8n
│   └── websocket/               # WebSocket manager + protocol
```

---

## Non-Negotiable Rules

Enforced in `CLAUDE.md`. Short version:

- No logic in routers. Routers call `orchestrator.run()` and stream.
- System prompt lives exclusively in `AgentOrchestrator.build_system_prompt()`.
- `AgentContext` is the single source of truth for all request state.
- The agentic loop runs until `end_turn`. Never break on `tool_use`.
- `CapabilityRegistry` is the only entity that knows what tools exist.
- No module-level globals. Everything on `app.state`.
- Model IDs live exclusively in `app/profiles/speda.py`.
- All tool descriptions are a minimum of 3–4 sentences.
- All endpoints require authentication.
- n8n handles all scheduling. No internal scheduler, ever.
