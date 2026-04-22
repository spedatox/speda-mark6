# S.P.E.D.A. Mark VI

**Specialized Personal Executive Digital Assistant (S.P.E.D.A.)** — the orchestrator layer that makes ambient AI actually work.

Single-user, proactive, built on FastAPI, Claude (Anthropic), and PostgreSQL. Deployed to Contabo. Designed for humans who need parallel thinking, persistent memory, and tools that actually execute.

The **Superior Six** (Sentinel, NightCrawler, Ultron, Optimus, Unicron, Ratchet) are independent microservices forking this repo, each with their own identity and domain.

---

## ⚡ Quick Status

| Layer | Status | Notes |
|-------|--------|-------|
| **Agentic Loop** | ✅ Live | Full Claude integration, 30-iter cap, streaming |
| **Session Memory** | ✅ Live | 50-msg history, multi-turn context |
| **4-Tier Registry** | ✅ Live | Tasks, Skills, MCP, Adapters unified |
| **Authentication** | ✅ Live | API key + n8n dual-auth on all routes |
| **PostgreSQL ORM** | ✅ Live | 6 models, async SQLAlchemy |
| **WebSocket Agents** | ✅ Live | Superior Six presence tracking |
| **System Info Skill** | ✅ Live | `df`, `free`, `uptime` working |
| **Terminal Client** | ✅ Live | REPL, single-shot, pipe modes |
| **MCP SDK** | 🔧 Stubbed | 12 servers configured, zero connected |
| **Task Tool** | 🔧 Stubbed | Sub-agent spawner registered, placeholder execution |
| **Push Notifications** | 🔧 Stubbed | FCM not configured |
| **TTS/STT** | 🔧 Stubbed | Kokoro/Whisper services not deployed |
| **Document Gen** | 🔧 Stubbed | PPTX/DOCX/PDF not wired |
| **Deep Research** | ⚠️ Ready | gpt-researcher service missing |
| **Security Analysis** | ⚠️ Ready | Shannon service missing |

---

## 🎯 What Actually Works vs. Theatre

```
┌────────────────────────────────────────────────────────────────┐
│ ✅ LIVE: Claude talks → You listen → Tools execute → Done      │
├────────────────────────────────────────────────────────────────┤
│ 🔧 STUBBED: Registered, returns "not implemented" placeholder  │
├────────────────────────────────────────────────────────────────┤
│ ⚠️  READY: Endpoint exists, backing service doesn't             │
└────────────────────────────────────────────────────────────────┘
```

**Live components:**
- Agentic orchestration (Claude loops until `end_turn`)
- Session persistence (50-msg history per user)
- Capability registry (all 4 tiers unified under one `tools` array)
- Auth enforcement (401 on every unauth'd request)
- WebSocket Superior Six connections
- System info queries (works immediately)

**Stubbed (placeholder responses):**
- MCP SDK — 12 servers configured in code, zero actually connected
- Sub-agent tasks — registered, Agent SDK not wired
- FCM push — n8n triggers fire anyway, outputs go nowhere
- TTS/STT — services don't exist on Contabo
- Document generation — function signatures only

---

## 🚀 The Critical Path (3 Unblocks)

### 1️⃣ MCP SDK Integration → **Unlocks 12 Servers at Once**

**File:** `app/mcp/client.py` (currently empty)

**Why it matters:** 12 MCP servers are configured but unplugged. Once `mcp.ClientSession` is wired:

```
Notion queries → Gmail/Calendar access → Brave/Tavily search
GitHub repos → arXiv abstracts → CVE lookups → Filesystem I/O
ALL LIVE SIMULTANEOUSLY
```

**What needs to happen:**
1. Instantiate `mcp.ClientSession` per server
2. Handle stdio transports (Brave, GitHub, Tavily, Fetch, etc.)
3. Handle HTTP/SSE transports (Notion, Google, Alpha Vantage)
4. Route tool responses back into `CapabilityRegistry.execute()`

Once this lands, SPEDA goes from "isolated but coherent" to "integrated everywhere."

### 2️⃣ Sub-Agent Task Execution → **Unlocks Parallel Research**

**Function:** `app/core/registry.py:_execute_task()` (currently returns placeholder)

**Why it matters:** Wire the Anthropic Agent SDK to spawn sub-agents in parallel:

```
SPEDA spawns Ratchet for research
    ├─ Ratchet spawns Nightcrawler for deep dives
    ├─ Parallel execution with independent context
    └─ Results synthesized back into main conversation

Multi-agent CITADEL vision becomes real.
```

### 3️⃣ Push Notifications → **Unlocks n8n Output Mode**

**File:** `app/skills/notifications.py` + Firebase Cloud Messaging

**Why it matters:** n8n webhooks fire into `POST /trigger/{agent_id}`, but `output_mode="push"` has nowhere to push.

```
Configure FCM → Flutter app registers token → Payloads arrive instantly
Removes polling tax from architecture.
```

---

## 📦 Installation & Quick Start

### Prerequisites

```bash
Python 3.11+
uv (https://docs.astral.sh/uv/)
Docker + Docker Compose
```

### Local Dev (5 minutes)

```bash
# Clone
git clone https://github.com/spedatox/speda-mark6.git
cd speda-mark6

# Dependencies
uv sync

# Environment
cp .env.example .env
# Fill: ANTHROPIC_API_KEY, SPEDA_API_KEY

# Database
docker compose up -d postgres

# Server
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# Verify
curl http://localhost:8000/health
# {"status":"ok","tools_registered":8}
```

### Terminal Client

Three modes. One binary.

```bash
# Interactive REPL — persistent session
.venv/bin/python speda.py
# Type commands. 'new' for fresh session, 'exit' to quit.

# Single shot — one command, stream, exit
.venv/bin/python speda.py "check system status"

# Piped — read from stdin
echo "what time is it?" | .venv/bin/python speda.py
```

### Full Stack (Docker)

```bash
cp .env.example .env
docker compose up
# Postgres up. SPEDA on :8000. Done.
```

---

## 🔌 Transport Channels (Do Not Confuse)

```
POST /chat
├─ HTTP + Server-Sent Events
├─ Flutter user chat
└─ Streams full response back to client
    
WS /ws
├─ WebSocket (bidirectional)
├─ Flutter real-time, voice input
└─ Low-latency mode (not yet fully wired)

WS /agents/ws/{id}
├─ WebSocket (agent-only)
├─ Superior Six internal comms
└─ Direct agent connections
```

### Trigger Sources

| Source | Auth | Behavior | Output Mode |
|--------|------|----------|-------------|
| **User** (Flutter) | `X-API-Key` | Interactive | Always `"respond"` |
| **n8n** | `X-API-Key` + `X-N8N-Secret` | Automation | `"push"` / `"silent"` / `"respond"` |

---

## 📡 Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET` | `/health` | None | Server alive + tool count |
| `POST` | `/chat` | API key | User message, SSE response |
| `WS` | `/ws` | API key | Real-time bidirectional chat |
| `POST` | `/trigger/{agent_id}` | API key + n8n secret | n8n webhook trigger |
| `GET` | `/agents` | API key | List online Superior Six agents |
| `WS` | `/agents/ws/{agent_id}` | API key | Connect to agent directly |
| `DELETE` | `/admin/outputs` | API key | Nuke `/tmp/speda_outputs/` |

---

## 🎛️ The Capability Stack (4 Tiers)

Claude sees one unified `tools` array. Registry routes execution.

### Tier 0: Tasks
Built-in sub-agent spawner. Runs before everything else.
- **Status:** Registered, placeholder execution
- **Unlock:** Wire Anthropic Agent SDK

### Tier 1: Python Skills
Sync functions in `app/skills/`. Add new skill = new file + register in `main.py`.
- **Status:** 1/5 live (system_info)
- **Stubbed:** TTS, STT, documents, notifications

### Tier 2: MCP Servers
Model Context Protocol integrations. 12 configured.

| Server | Transport | Auth | Status |
|--------|-----------|------|--------|
| **Notion** | HTTP/SSE | `NOTION_API_KEY` | Config only |
| **Google** (Gmail/Calendar) | HTTP/SSE | OAuth 2.1 | Config only |
| **Brave Search** | stdio | `BRAVE_SEARCH_API_KEY` | Config only |
| **Tavily** | stdio | `TAVILY_API_KEY` | Config only |
| **Exa** | stdio | `EXA_API_KEY` | Config only |
| **GitHub** | stdio | `GITHUB_TOKEN` | Config only |
| **Alpha Vantage** | HTTP/SSE | `ALPHA_VANTAGE_API_KEY` | Config only |
| **Fetch** | stdio | — | Config only |
| **Filesystem** | stdio | — (sandboxed `/tmp/speda_outputs`) | Config only |
| **arXiv** | stdio | — | Config only |
| **CVE Intelligence** | stdio | — | Config only |
| **Playwright** | HTTP | — (internal only) | Config only |

**All 12 need MCP SDK wiring in one file.**

### Tier 3: OSS Adapters
HTTP wrappers around external services.
- `deep_research` — wraps gpt-researcher (service not deployed)
- `security_analysis` — wraps Shannon (service not deployed)

---

## 🔧 Forking for Superior Six

Each agent is an independent microservice. Fork the repo, swap one file:

```bash
# For Sentinel
cp app/profiles/speda.py app/profiles/sentinel.py

# Edit:
# - name = "Sentinel"
# - domain = "cybersecurity"
# - system_prompt_template = "You are Sentinel, a..."
# - allocate_model() = custom logic (e.g., Opus for critical decisions)
```

**Everything else stays identical.** Same engine. Different identity.

Each agent:
- Runs on its own port
- Connects back via WebSocket to SPEDA's registry
- Independent database (or read-isolated shared DB)
- Triggered independently via n8n

---

## 🔐 Environment Variables

### Required

```bash
ANTHROPIC_API_KEY=sk-ant-...
SPEDA_API_KEY=your-api-key-here
DATABASE_URL=postgresql+asyncpg://speda:speda@localhost:5432/speda  # optional
N8N_SECRET=your-n8n-webhook-secret
LOG_LEVEL=INFO
```

### MCP Servers (Optional)

```bash
NOTION_API_KEY=
BRAVE_SEARCH_API_KEY=
TAVILY_API_KEY=
EXA_API_KEY=
GITHUB_TOKEN=
ALPHA_VANTAGE_API_KEY=
```

### OSS Adapters (Optional)

```bash
GPT_RESEARCHER_URL=http://localhost:8001
SHANNON_URL=http://localhost:9000
```

---

## 📁 Project Structure

```
speda-mark-vi/
├── speda.py                     # Terminal client (REPL + single-shot)
├── app/
│   ├── main.py                  # Lifespan handler, app factory
│   ├── config.py                # Settings, structured logging
│   ├── database.py              # Async SQLAlchemy, migrations
│   ├── middleware/auth.py       # API key validation (all routes)
│   ├── profiles/
│   │   ├── base.py              # AgentProfile ABC
│   │   └── speda.py             # SPEDA identity — fork this for Superior Six
│   ├── core/
│   │   ├── orchestrator.py      # Agentic loop, system prompt, 30-iter cap
│   │   ├── context.py           # AgentContext (single source of truth)
│   │   ├── registry.py          # CapabilityRegistry (Tiers 0–3 routing)
│   │   ├── agent_registry.py    # Superior Six tracking
│   │   └── session_manager.py   # Session lifecycle, history
│   ├── routers/
│   │   ├── chat.py              # POST /chat, WS /ws
│   │   ├── trigger.py           # POST /trigger/{agent_id}
│   │   ├── agents.py            # GET /agents, WS /agents/ws/{id}
│   │   ├── admin.py             # DELETE /admin/outputs
│   │   └── health.py            # GET /health
│   ├── skills/                  # Tier 1 — Python skills
│   ├── mcp/                     # Tier 2 — MCP client + config
│   ├── adapters/                # Tier 3 — OSS wrappers
│   ├── models/                  # SQLAlchemy ORM (6 tables)
│   ├── schemas/                 # Pydantic request/response
│   ├── services/                # Anthropic client, memory, n8n
│   └── websocket/               # WebSocket manager
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## ⚙️ Non-Negotiable Rules

*Enforced in `CLAUDE.md`. These aren't guidelines.*

```
✋ No logic in routers.
   → Routers call orchestrator.run(), stream response. That's it.

✋ System prompt lives in one place.
   → AgentOrchestrator.build_system_prompt(). Nowhere else.

✋ AgentContext is gospel.
   → Single source of truth for all request state.

✋ Agentic loop runs until end_turn.
   → Never break early on tool_use. Claude decides.

✋ CapabilityRegistry owns tool knowledge.
   → Only entity that knows what tools exist + how to route them.

✋ No module-level globals.
   → Everything on app.state.

✋ Model IDs live in profiles.
   → app/profiles/speda.py is the source of truth.

✋ Tool descriptions are 3–4 sentences minimum.
   → Claude deserves context.

✋ Every endpoint requires auth.
   → No exceptions.

✋ n8n owns scheduling.
   → No APScheduler, no cron, no timers. Webhooks only.
```

---

## 📋 Roadmap

### Phase 1: Integration (Now)
- [ ] Wire MCP SDK → unlocks 12 servers
- [ ] Wire Agent SDK → unlocks sub-agents  
- [ ] Configure FCM → unlocks push output

### Phase 2: Ambient Capabilities
- [ ] Deploy Kokoro TTS on Contabo
- [ ] Deploy Whisper STT on Contabo
- [ ] Implement document generation
- [ ] Implement memory extraction
- [ ] Wire WebSocket bidirectional loop

### Phase 3: Superior Six Autonomy
- [ ] Deploy all 6 microservices
- [ ] Deploy gpt-researcher
- [ ] Deploy Shannon
- [ ] Activate House Party Protocol (inter-agent comms)

---

## 🤝 Contributing

Fork. Branch off `main`. Follow the non-negotiable rules. Open a PR.

**Priority contributions:**
1. MCP SDK wiring (biggest unlock)
2. Agent SDK integration
3. FCM push notifications
4. Tests (we have almost none)

---

## 📜 License

MIT. Use it. Fork it. Break it. Fix it.

---

## ❓ Questions?

Read `CLAUDE.md` first. Then open an issue. Then email.

---

**Built by Ahmet Erol Bayrak.** 