# SPEDA Mark VI — Technical Reference

The engineering companion to the [README](../README.md): the full capability
catalog, HTTP API, configuration, and the architectural rules. The binding
architectural contract is [`CLAUDE.md`](../CLAUDE.md).

---

## Architecture

```
                        ┌──────────────────── server (Docker / Contabo) ─────────────────┐
┌─────────────────┐     │  ┌────────────────────────────┐  ┌──────────┐  ┌─────────────┐ │
│  Heartbreaker   │     │  │  app (FastAPI :8000)       │  │ postgres │  │ n8n (:5678) │ │
│  Electron+React │◀───▶│  │   AgentOrchestrator        │◀▶│ sessions │  │ watchers:   │ │
│  SSE / WebSocket│     │  │   agentic loop             │  │ memory   │  │ cron·web·rss│ │
└─────────────────┘     │  │   CapabilityRegistry       │  └──────────┘  └──────┬──────┘ │
                        │  └───────────┬────────────────┘                       │        │
┌─────────────────┐     │  ┌───────────▼────────┐  ┌──────────┐   POST /trigger/{agent}  │
│  Telegram (push)│◀────│  │ TurnRegistry       │  │ sandbox  │◀─────────┘               │
│  per-agent bots │     │  │ detached turns     │  │ isolated │                          │
└─────────────────┘     │  └────────────────────┘  └──────────┘                          │
                        └────────────────────────────────────────────────────────────────┘
        ⚒️ The Forge (standalone) ──WS──▶ /agents/ws/optimus   (Optimus's execution engine)
```

**Request flow.** Routers construct an `AgentContext` and hand off — every chat
turn runs in a **detached task** (`TurnRegistry`) decoupled from the HTTP request,
streams `SSEEvent`s, persists itself, and survives client disconnects. The
agentic loop handles every stop reason explicitly (`end_turn`, `tool_use`,
`max_tokens`, `pause_turn`), runs tools in parallel, and terminates under a hard
30-iteration guard.

**Monorepo layout.**

| Package | Role |
|---|---|
| `packages/api` | FastAPI backend — orchestrator, capability registry, skills, memory, automations, news desk, turn runner |
| `packages/heartbreaker` | **Primary UI** (Electron + React) — the fluid-glass HUD |
| `packages/desktop` | Neutral UI template — the base forks re-skin (never themed) |
| `packages/sandbox` | Isolated command-execution container ("capable computer") |
| *(separate repo)* The Forge | Optimus's Mark II execution engine — connects back as a WebSocket peer |

---

## Capability stack

The model sees one unified tools array; `CapabilityRegistry` is the only
component that knows the tiers apart. Startup registration order is fixed:
Tier 0 → 1 → 2 → 3.

### Tier 0 — The Legion
`Task` deploys The Legion: isolated, context-isolated worker agents
(legionnaires — scout / researcher / analyst / judge / general) for heavy
parallel research, with bounded recursion. Worker models resolve
provider-agnostically: low/medium-effort legionnaires run on the cheap tier of
the PARENT chat model's provider (Anthropic → Haiku, keeping the separate rate
pool; zai → glm-air; …), high-effort inherit the parent model. Pin all workers
with `LEGION_MODEL_OVERRIDE` (legacy alias `SUB_AGENT_MODEL`). Background
workers (`run_in_background`) return a ticket retrievable via `legion_status`.

### Tier 1 — Python skills

**Agent network**
- `dispatch_agent` — delegate a task to another agent; runs their full loop and
  returns the result in-turn. Set `background: true` to spawn it detached and
  keep working (a ticket comes back immediately).
- `dispatch_status` — check on background dispatches you launched (running / done
  / result).
- `read_agent_channel` — the shared, chronological inter-agent transcript.
- `house_party` — engage/stand-down the House Party Protocol (engage is
  passphrase-gated via the app's authorization window; the tool stands down).

**News desk**
- `news_headlines` — read the always-on RSS store (deduped, cross-outlet).
- `news_watch` — manage breaking-news keyword alerts.
- `news_deep_dive` — Tier-2 NewsData.io analysis (quota-budgeted).
- `read_article` — free full-text extraction from an article URL.

**Memory & recall**
- `memory` — structured Markdown memory filesystem (owner, current, dossier,
  projects, log, …).
- `search_history` — literal keyword/date search over stored messages.
- `recall_conversations` — semantic vector recall over history embeddings.

**Execution & files**
- `run_command` — shell in the isolated Linux sandbox (Python 3.12, pip, git,
  persistent `/workspace`); local launcher or Docker.
- `deliver_file` — send a sandbox file to a chat download card.
- `save_file` — write code/text files as download cards.
- `generate_document` — branded A4 PDF / DOCX / PPTX (Turkish-ready fonts).

**Automation & system**
- `manage_automations` — compose/list/toggle n8n watchers.
- `use_toolset` — lazy-mount an inactive MCP server at runtime.
- `read_skill` — progressive-disclosure loader for full `SKILL.md` guides.
- `set_budget_mode` — runtime frugality toggle.
- `system_info` — host disk/memory/uptime.
- `send_push_notification` — surface a background result to the mobile client.
- `speech_to_text` (Whisper) · `text_to_speech` (Kokoro).

**OSINT suite** — `ip_geolocate`, `ip_reputation`, `urlhaus_lookup`,
`threatfox_lookup`, `malwarebazaar_lookup`, `pwned_password_check`,
`darkweb_search`, `otx_lookup`, `shodan_lookup`, `email_discovery`,
`crypto_trace`, `intelx_search`.

### Tier 2 — MCP servers
Notion · Google Gmail & Calendar (self-refreshing OAuth) · Brave Search · Fetch ·
Alpha Vantage · Tavily · Exa · GitHub · Filesystem · arXiv · CVE Intelligence ·
Playwright (Docker-internal only).

### Tier 3 — OSS adapters
`deep_research` (GPT-Researcher) · `security_analysis` (Shannon).

### Cost control
Lazy progressive disclosure (only always-on servers occupy the prompt prefix) +
1-hour prompt caching means the static prefix is written once and read forever.
Budget mode stands the Legion down and injects a concise-output directive.

---

## Multi-provider LLM routing

All calls go through one `LLMClient`. Model refs are `provider:model` strings; a
bare name routes to Anthropic.

| Provider | Reference | Transport |
|---|---|---|
| Anthropic (primary) | `claude-sonnet-4-6` | Native SDK, prompt caching intact |
| OpenAI | `openai:gpt-5.1` | Chat Completions |
| Google Gemini | `gemini:gemini-2.5-flash` | OpenAI-compat endpoint |
| z.ai (GLM) | `zai:glm-4.6` | OpenAI-compat |
| DeepSeek | `deepseek:deepseek-v4-pro` | OpenAI-compat |
| NVIDIA NIM | `nvidia:meta/llama-3.1-405b-instruct` | OpenAI-compat |
| Ollama (local) | `ollama:llama3.1:8b` | Local `/v1`, models listed live |

Translation happens only at the wire boundary — internally everything speaks
Anthropic content-block format. `LLM_FALLBACK_CHAIN` retries the next provider on
failure; the UI picker only shows providers with configured credentials.

---

## HTTP API

All endpoints require `X-API-Key` (middleware) unless noted.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + tool count *(unauthenticated)* |
| `POST` | `/chat` · `/chat/{agent_id}` | User message → detached turn, SSE stream |
| `GET` | `/chat/active` | Turns currently running (re-attach discovery) |
| `GET` | `/chat/attach/{request_id}` | Re-attach to a live/just-finished turn (replay + tail) |
| `POST` | `/chat/cancel/{request_id}` | Cancel a running turn (persists the partial) |
| `WS` | `/ws` | WebSocket chat |
| `GET` | `/welcome/{agent_id}` | JARVIS welcome remark (cached, memory-aware) |
| `GET` | `/models` | Models across configured providers |
| `GET/PATCH/DELETE` | `/sessions…` | Session list, history, rename, delete |
| `GET/POST` | `/budget-mode` | Read / toggle budget mode |
| `GET/POST` | `/connections…` | MCP status + live toggle; Google/Notion OAuth |
| `GET/POST/DELETE` | `/automations…` | Watchers; pipeline status; Telegram connect |
| `POST` | `/news/poll` | n8n-driven Tier-1 collection *(also needs `X-N8N-Secret`)* |
| `GET` | `/news/items` | Stored headlines |
| `GET/POST` | `/news/watch` | Keyword watchlist |
| `GET` | `/agents` · `WS /agents/ws/{id}` | External peer registry (the Forge / Optimus) |
| `GET` | `/agents/comms` | Inter-agent traffic feed |
| `GET/POST` | `/agents/models` | Per-agent model pins |
| `GET/POST` | `/agents/house-party` | Protocol state / engage (passphrase) / stand-down |
| `POST` | `/trigger/{agent_id}` | n8n callback *(also needs `X-N8N-Secret`)* |
| `GET` | `/files/{name}` | Download generated artifacts |
| `POST` | `/admin/import-chats` · `/admin/index-history` | History import + memory mining |
| `DELETE` | `/admin/outputs` | Temp-file cleanup (n8n daily) |

---

## Configuration

Settings load from `packages/api/.env` via pydantic-settings, layered over
`~/.speda/.env` (managed overrides from the Settings UI). See
[`.env.example`](../.env.example) for the full annotated list.

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Primary model provider |
| `SPEDA_API_KEY` | yes | API auth (`X-API-Key`) |
| `N8N_SECRET` | yes* | Shared secret for `/trigger` and `/news/poll` |
| `DATABASE_URL` | no | SQLite default; `postgresql+asyncpg://…` in prod |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` / `ZAI_API_KEY` / `DEEPSEEK_API_KEY` / `NVIDIA_API_KEY` / `OLLAMA_BASE_URL` | no | Additional providers |
| `LLM_MAIN_MODEL` / `LLM_BACKGROUND_MODEL` / `LLM_FALLBACK_CHAIN` | no | Model policy overrides |
| `N8N_API_URL` / `N8N_API_KEY` | no | Automation control plane |
| `TELEGRAM_BOT_TOKEN` (+ per-agent tokens) | no | Proactive delivery; the bot fleet |
| `FORGE_DIR` / `FORGE_AUTOSTART` / `FORGE_WS_URL` / `FORGE_CELL_BACKEND` | no | The Forge (Optimus engine) launcher |
| `SANDBOX_URL` / `SANDBOX_AUTOSTART` / `SANDBOX_LOCAL_PORT` | no | run_command sandbox (local `:9100` or Docker) |
| `NEWSDATA_API_KEY` / `NEWS_*` | no | News desk Tier 2 + poll/retention/quota |
| `HOUSE_PARTY_PASSPHRASE` | no | Authorization secret for the protocol |
| `MCP_ENABLED` / `ALWAYS_ON_SERVERS` | no | Which MCP servers connect / prefix |
| `BUDGET_MODE` | no | Default frugality state |
| `TAVILY_API_KEY`, `NOTION_*`, `GITHUB_TOKEN`, OSINT keys, … | no | Per-integration credentials (degrade gracefully when absent) |

---

## Engineering rules (from `CLAUDE.md`)

- Routers hold zero business logic — build context, hand to the engine, stream.
- The system prompt is owned exclusively by `AgentOrchestrator`.
- `AgentContext` is the single source of truth; no module-level globals —
  everything lives on `app.state` via the lifespan.
- The agentic loop never breaks early on `tool_use`; it runs to `end_turn` under
  a 30-iteration guard.
- Model IDs live only in `app/profiles/`; identity strings never enter core.
- Anthropic content-block wire format only; provider translation is at the
  LLM-client boundary alone.
- All scheduling belongs to n8n. The backend never grows a cron.
- Every endpoint authenticates; generated files are temporary (24h n8n cleanup).
- Chat turns are detached (survive disconnects); background work never blocks the
  stream.

---

## Extending

**Add a skill.** Drop a `SKILL.md` (YAML frontmatter) into
`app/skills/skill_docs/<name>/` — the manifest rebuilds automatically. Back it
with a `Skill` subclass and register it in `main.py`. Descriptions must state:
what it does, when to use it, when *not* to, and what it returns.

**Add an MCP server.** One entry in `app/mcp/servers.py`. STDIO for local; HTTP +
OAuth only for officially managed remotes.

**Fork / brand an agent.** Identity is prompt + profile only. Backend personality
lives in `app/profiles/{agent}.py` + `app/prompts/agents/{agent}/`; the client
brand (name, accent, tagline) in `packages/heartbreaker/src/renderer/src/profile/brands.ts`.
Build a standalone branded installer with
`build-app.ps1 -Agent <name> -ApiBase <url> -ApiKey <key>`.

---

## Development

```bash
npm run heartbreaker:typecheck   # UI type safety
npm run heartbreaker:build       # production build
uv run pytest                    # backend tests (packages/api)
```
