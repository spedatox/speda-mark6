# SPEDA Mark VI

**Specialized Personal Executive Digital Assistant** — sixth iteration of the SPEDA series.

Single-user, ambient, proactive. Built on FastAPI + Claude (Anthropic) + PostgreSQL on the backend, and an Electron + React desktop app on the front. Deployed to Contabo. Designed for one person who needs an extension of their will, not a chatbot.

> *"You are not a product. You are not a service. You are an extension of a single person's will."*
> — `prompts/core/01_identity.md`

---

## Architecture

```
speda-mark6/
├── packages/
│   ├── api/          ← FastAPI backend: agentic loop, skills, memory, streaming
│   └── desktop/      ← Electron + React: dark UI, real-time typewriter, SVG rendering
├── speda.ps1         ← One-command launcher (Windows)
└── docker-compose.yml
```

Both packages run together. One command starts everything.

---

## Quick Start (Windows)

```powershell
# One command — starts backend + desktop app
speda
```

The `speda` command (registered via `speda.bat` → `speda.ps1`) starts the FastAPI backend, polls TCP port 8000 until ready, then launches the Electron desktop app.

### Manual start

```bash
# Backend
cd packages/api
uv sync
cp .env.example .env        # fill ANTHROPIC_API_KEY, SPEDA_API_KEY
docker compose up -d postgres
uv run uvicorn app.main:app --port 8000 --reload

# Desktop (separate terminal, from repo root)
npm install
npm run desktop:dev

# Web-only (no Electron)
npm run desktop:web:dev     # opens on localhost:5173
```

### Prerequisites

```
Python 3.11+    uv (https://docs.astral.sh/uv/)
Node 18+        Docker + Docker Compose
```

---

## Current Status

| Feature | Status | Notes |
|---------|--------|-------|
| **Real-time token streaming** | ✅ Live | Per-token SSE via Anthropic stream API |
| **Agentic loop** | ✅ Live | Runs until `end_turn`, 30-iteration safety cap |
| **Agent Skills system** | ✅ Live | Progressive disclosure, SKILL.md per capability |
| **Long-term memory (write)** | ✅ Live | Haiku extracts facts after every turn |
| **Long-term memory (recall)** | 🔧 Pending | Facts stored, recall injection not yet wired |
| **Session history** | ✅ Live | Multi-turn context, PostgreSQL persistence |
| **Auto session titles** | ✅ Live | Haiku generates title after first exchange |
| **Math / LaTeX rendering** | ✅ Live | KaTeX, currency-safe, code-safe |
| **SVG / HTML inline rendering** | ✅ Live | Draw-in animation, no chrome |
| **Markdown + GFM** | ✅ Live | Tables, code blocks, syntax highlighting |
| **Model selector** | ✅ Live | In-bar switcher, persisted to localStorage |
| **Two-file agent forking** | ✅ Live | Profile + identity = full rebrand |
| **Authentication** | ✅ Live | API key on all routes |
| **Document generation** | 🔧 Stubbed | PPTX/DOCX/PDF functions registered |
| **TTS / STT** | 🔧 Stubbed | Kokoro/Whisper not deployed |
| **Push notifications** | 🔧 Stubbed | FCM not configured |
| **MCP servers** | ⚠️ Config-only | 12 servers configured, SDK not wired |
| **Sub-agent tasks** | ⚠️ Config-only | Task tool registered, Agent SDK not wired |

---

## The Frontend

Dark, native-feeling Electron app. Key capabilities:

- **Typewriter streaming** — adaptive rAF engine with exponential catch-up. The further behind the buffer, the faster it types.
- **Inline rendering** — SVG diagrams draw themselves via `stroke-dashoffset` animation; HTML widgets render in a sandboxed iframe with no chrome. Hover to reveal Code/Copy.
- **Math** — KaTeX inline (`$E=mc^2$`) and display (`$$...$$`). Currency-safe (`$5M` stays plain text).
- **Working status** — shimmer text + dashed spinner with natural-language labels (*"Reviewing capabilities…"* not `read_skill`)
- **Model selector** — pill in the input bar, picks between Opus / Sonnet / Haiku
- **White breathing caret** — glow + sheen animation while streaming

---

## The Agent Skills System

Mirrors Anthropic's Agent Skills architecture: **progressive disclosure**.

The system prompt contains only a compact manifest (~100 tokens/skill):

```
- inline-rendering: Renders SVG/HTML as live previews in chat...
- generate-document: Creates downloadable PDF/DOCX/PPTX...
- system-info: Reports server health metrics...
```

Full instructions load on demand via `read_skill("skill-name")` — the model only pays context for what it's actually using.

### Adding a skill

1. Create `packages/api/app/skills/skill_docs/<name>/SKILL.md` with YAML frontmatter
2. Optionally back it with a Python `Skill` class in `app/skills/`
3. Register in `main.py` if it has a Python class

The manifest rebuilds automatically. No other changes needed.

---

## Forking for the Superior Six

An agent is exactly **two files**. Nothing else needs touching.

| File | Controls |
|------|----------|
| `packages/desktop/src/renderer/src/profile/speda.ts` | Name, model number, accent colour, tagline, avatar, welcome prompts |
| `packages/api/app/prompts/core/01_identity.md` | Personality, voice, boundaries, domain, owner context |

The backend display name is derived from the identity heading — edit the `.md` and the FastAPI title follows automatically.

### The Superior Six

| Agent | Domain |
|-------|--------|
| **SPEDA** | Executive orchestrator |
| **Ultron** | Academic |
| **Sentinel** | Financial |
| **Atomix** | Health |
| **Centurion** | Cybersecurity |
| **Nightcrawler** | Research |
| **Optimus** | Coding |

**House Party Protocol:** all six active simultaneously. Reserved for high-stakes operations only.

---

## Capability Stack (4 Tiers)

Claude sees one unified `tools` array. The registry routes execution.

### Tier 0 — Task tool
Sub-agent spawner. Registered. Agent SDK execution pending.

### Tier 1 — Python Skills

| Skill | Status |
|-------|--------|
| `read_skill` | ✅ Live — progressive disclosure meta-tool |
| `system_info` | ✅ Live — disk, memory, uptime |
| `generate_document` | 🔧 Stubbed |
| `text_to_speech` | 🔧 Stubbed — Kokoro pending |
| `speech_to_text` | 🔧 Stubbed — Whisper pending |
| `send_push_notification` | 🔧 Stubbed — FCM pending |

### Tier 2 — MCP Servers
12 servers configured. One file to wire them all: `app/mcp/client.py`.

Notion, Gmail/Calendar, Brave Search, Tavily, Exa, GitHub, Alpha Vantage, Fetch, Filesystem, arXiv, CVE Intelligence, Playwright.

### Tier 3 — OSS Adapters
- `gpt-researcher` — deep research (service not deployed)
- `Shannon` — security analysis (service not deployed)

---

## Memory System

After every chat turn, a background Haiku call extracts up to 5 facts about the user:

```json
["User is building SPEDA on Contabo",
 "User prefers direct, dry responses",
 "User codename is Spedatox"]
```

These are stored in the `memories` table. **Recall is the next milestone** — facts will be injected into the system prompt as `## What you know about the owner`, giving SPEDA genuine continuity across sessions.

---

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET` | `/health` | None | Server alive + tool count |
| `GET` | `/models` | API key | Available model list |
| `POST` | `/chat` | API key | User message → SSE stream |
| `GET` | `/sessions` | API key | Session list |
| `GET` | `/sessions/{id}/messages` | API key | Session history |
| `DELETE` | `/sessions/{id}` | API key | Delete session |
| `WS` | `/ws` | API key | WebSocket chat |
| `POST` | `/trigger/{agent_id}` | API key + n8n secret | n8n automation trigger |
| `GET` | `/agents` | API key | Online Superior Six list |
| `WS` | `/agents/ws/{id}` | API key | Agent direct connection |
| `DELETE` | `/admin/outputs` | API key | Clear `/tmp/speda_outputs/` |

---

## Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...
SPEDA_API_KEY=your-key
DATABASE_URL=postgresql+asyncpg://speda:speda@localhost:5432/speda

# Optional — n8n automation
N8N_SECRET=your-n8n-secret

# Optional — MCP servers
NOTION_API_KEY=
BRAVE_SEARCH_API_KEY=
TAVILY_API_KEY=
EXA_API_KEY=
GITHUB_TOKEN=
ALPHA_VANTAGE_API_KEY=

# Optional — OSS adapters
GPT_RESEARCHER_URL=http://localhost:8001
SHANNON_URL=http://localhost:9000
```

---

## Non-Negotiable Rules

*Enforced in `CLAUDE.md`.*

- No logic in routers — routers call `orchestrator.run()` and stream. That's it.
- System prompt lives in one place — `AgentOrchestrator.build_system_prompt()`.
- `AgentContext` is gospel — single source of truth for all request state.
- Agentic loop runs until `end_turn` — never break early on `tool_use`.
- Model IDs live in profiles — `profiles/speda.py` is the source of truth.
- No module-level globals — everything on `app.state`.
- Every endpoint requires auth — no exceptions.

---

## Roadmap

### Now
- [ ] Memory recall — inject stored facts into system prompt
- [ ] Wire MCP SDK — unlocks all 12 servers at once
- [ ] Wire Agent SDK — unlocks parallel sub-agent execution

### Next
- [ ] Deploy Kokoro TTS + Whisper STT on Contabo
- [ ] FCM push notifications
- [ ] Memory deduplication + management UI

### Later
- [ ] Deploy all Superior Six microservices
- [ ] Deploy gpt-researcher + Shannon
- [ ] House Party Protocol (inter-agent comms)

---

## Project Structure

```
speda-mark6/
├── speda.ps1                          # Windows launcher
├── packages/
│   ├── api/
│   │   └── app/
│   │       ├── main.py                # Lifespan, app factory
│   │       ├── core/
│   │       │   ├── orchestrator.py    # Agentic loop, real-time streaming
│   │       │   ├── registry.py        # 4-tier capability routing
│   │       │   └── session_manager.py
│   │       ├── profiles/
│   │       │   ├── base.py            # AgentProfile ABC
│   │       │   └── speda.py           # Active profile (fork for Superior Six)
│   │       ├── prompts/
│   │       │   ├── loader.py          # File-based prompt assembly
│   │       │   └── core/
│   │       │       └── 01_identity.md # ← FORK THIS for personality
│   │       ├── skills/
│   │       │   ├── read_skill.py      # Progressive disclosure meta-tool
│   │       │   └── skill_docs/        # SKILL.md per capability
│   │       └── services/
│   │           └── memory.py          # Background fact extraction + titles
│   └── desktop/
│       └── src/renderer/src/
│           ├── profile/
│           │   └── speda.ts           # ← FORK THIS for branding
│           ├── components/
│           │   ├── Message.tsx        # Typewriter + KaTeX + SVG rendering
│           │   ├── WidgetFrame.tsx    # Inline HTML/SVG previews
│           │   └── InputBar.tsx       # Input + model selector
│           └── theme/base.css         # Dark theme + animations
├── docker-compose.yml
└── .env.example
```

---

**Built by Ahmet Erol Bayrak** (`Spedatox`)
