# Igor

The backend. One FastAPI process: every agent, the memory system, the tool registry, the orchestrator loop, and the database. Every other package in this repo is a client of this one.

---

## Contents

- [Directory structure](#directory-structure)
- [Layering](#layering)
- [Local development](#local-development)
- [Core contracts](#core-contracts)
- [Language](#language)
- [Authentication](#authentication)
- [Startup sequence](#startup-sequence)
- [Database](#database)
- [Testing](#testing)

---

## Directory structure

| Path | Contents |
|---|---|
| `app/core/` | The engine: request context, the agentic loop, the tool registry, session management, inter-agent dispatch, the external-peer bridge, the detached turn runner, process-local runtime flags |
| `app/models/` | SQLAlchemy ORM tables, one file per entity |
| `app/schemas/` | Pydantic request/response DTOs, one file per feature area |
| `app/services/` | Business logic and integrations with no HTTP or LLM-tool concerns — memory, embeddings, mail/web watching, Google/Microsoft auth, task queue, the named safety protocols |
| `app/skills/` | Tier-1 capabilities — one file per LLM-callable tool |
| `app/mcp/` | Tier-2 MCP client plumbing and the REST-backed pseudo-MCP servers for Gmail/Calendar/Outlook |
| `app/adapters/` | Tier-3 wrapped external agent runtimes |
| `app/legion/` | The Tier-0 parallel research worker system |
| `app/profiles/` | One `AgentProfile` subclass per persona — identity, tool allowlist, model policy, prompt branding |
| `app/routers/` | Thin FastAPI routers, one per feature surface, delegating to services/orchestrator |
| `app/middleware/` | API key auth, security headers |
| `app/prompts/` | Markdown system-prompt fragments — a shared policy stack plus per-persona identity blocks |
| `app/automations/` | n8n-backed scheduled/triggered workflow composition |
| `app/telegram/` | The bot fleet — one bot per agent, inbound gateway, message rendering |
| `app/news/` | RSS collection, dedup, escalation |
| `app/websocket/` | The external-peer WebSocket manager and message protocol |
| `app/config.py`, `app/database.py`, `app/main.py` | Settings, engine/session bootstrap, app factory and lifespan |

---

## Layering

Dependency direction is one-way in practice, enforced by convention rather than a lint rule:

```
config, database, clock
  → models
  → services
  → skills
  → core (registry, context, session_manager, orchestrator)
  → routers
```

Routers can reach directly into services (a router isn't an LLM tool call, so there's no need to route through a skill). `core/orchestrator.py` reaches up into two memory-recall helpers in `skills/memory.py` — a deliberate, narrow exception, not a pattern to extend. `core/registry.py` imports skill/adapter/profile *types* only under `TYPE_CHECKING`; actual instances are passed in, keeping the registry decoupled from what it registers.

Tool tiers register at startup in a fixed order: **Legion (Tier 0) → Skills (Tier 1) → MCP servers (Tier 2) → Adapters (Tier 3)**. A handful of infrastructure skills (`memory`, `read_skill`, `use_toolset`, `tool_search`) are always available regardless of an agent's tool allowlist.

---

## Local development

Dependency management is `uv`, via a workspace declared at the repo root.

```bash
# from the repo root
uv sync

# from packages/igor
uv run uvicorn app.main:app --reload
```

Configuration lives in `packages/igor/.env` (not committed) — copy `.env.example` from the repo root as a starting point. `SPEDA_API_KEY` is required; every request without a matching `X-API-Key` header is rejected.

`alembic` is a listed dependency but isn't wired up — there's no `alembic.ini` or `migrations/` directory. Schema management happens inside `app/database.py`: `init_db()` runs `Base.metadata.create_all` for new tables, then an idempotent, hand-written pass of additive `ALTER TABLE` / `CREATE INDEX IF NOT EXISTS` statements that runs on every startup, against both SQLite and Postgres. There is no separate migration step — starting the app applies pending schema changes.

`scripts/migrate_sqlite_to_postgres.py` moves data between the two supported backends.

---

## Core contracts

**`AgentContext`** (`app/core/context.py`) — a plain dataclass, the single source of truth for request state:

```python
@dataclass
class AgentContext:
    agent_id: str
    user_id: int
    session_id: int
    request_id: str
    triggered_by: Literal["user", "n8n", "agent"]
    trigger_payload: dict
    output_mode: Literal["respond", "push", "silent"]
    model: str
    system_prompt: str
    conversation_history: list[dict]
    db: AsyncSession
    timezone: str = "UTC"
    extra: dict = field(default_factory=dict)
```

**The agentic loop** (`app/core/orchestrator.py`) — `AgentOrchestrator.run()`, capped at `MAX_TOOL_ITERATIONS = 200` as a last-resort backstop. Stop reasons:

| Stop reason | Behavior |
|---|---|
| `end_turn` | Break — response already streamed |
| `tool_use` | Run every tool call concurrently, persist an audit row per call, append results, loop |
| `max_tokens` | Append a continue prompt, loop |
| `pause_turn` | Append a continue prompt, loop |
| Anything else | Log as unknown, break |

**`CapabilityRegistry`** (`app/core/registry.py`) — the single plug-in point for all four tool tiers. `execute()` routes by tier and memoizes read-only Tier-1 calls per turn; failures are never memoized.

**`SessionManager`** (`app/core/session_manager.py`) — key methods, all taking `db` as the first argument:

```python
async def get_or_create(db, user_id, triggered_by, model_used, agent_id="speda", session_id=None, channel="app") -> Session
async def list_sessions(db, user_id, agent_id, limit=500)
async def close(db, session_id)
async def load_history(db, session_id) -> list[dict]
async def truncate(db, session_id, keep) -> int
async def save_message(db, session_id, role, content) -> Message
```

`agent_id` defaults to `"speda"` — it's optional, not required.

---

## Language

One setting, `settings.agent_language` (`"tr"` / `"en"`), decides what the whole
system speaks. `app/services/language.py` is the only module that reads it.

| Surface | How it follows |
|---|---|
| What every agent WRITES | `prompts/core/15_language.md`, in every profile's `PROMPT_SECTIONS`, built with `language.name_of()` by `AgentOrchestrator.build_system_prompt` |
| Synthesis | `language.tts_locale()` — `settings.tts_locale` is an override for a regional variant, normally empty |
| Recognition | `language.stt_locale()` — same shape, `settings.stt_locale` overrides |
| Client chrome | The clients' own i18n dictionaries, moved by the same switch that PUTs `agent_language` |

The prompt section is the enforcement: not one word of the other language,
whatever language the owner, a tool result or a web page happens to be in.
Proper nouns, code, paths, identifiers and quoted material are never translated.

`language.detect_leak()` is the backstop — a lexical scan of finished prose
(code, URLs, paths, identifiers, numbers and quotes excised first) for function
words of the wrong language. Whether a leak can be *fixed* depends on the path:

- **Chat** — already streamed to his screen, so it is logged (`language_leak`)
  and reported on the SSE `DONE` event. Never rewritten; rewriting text he has
  read is worse than the leak.
- **Voice** — `tts.prepare_speech_text` calls `language.enforce()` before
  synthesis, so a leak is repaired before it is ever spoken.
- **Automation pushes** — `trigger_runner` does the same before the Telegram
  message goes out.

Repair is one cheap-model pass, gated on `settings.language_repair`, and
degrades to the untouched text on any failure.

---

## Authentication

`app/middleware/auth.py` checks `X-API-Key` against `settings.speda_api_key` with a constant-time comparison, on every path except:

- `/health`
- `/oauth/google/callback`, `/oauth/notion/callback`, `/oauth/microsoft/callback`
- `/telegram/webhook/*` (authenticated separately, via `X-Telegram-Bot-Api-Secret-Token` plus an owner-id allowlist)
- `/docs`, `/redoc`, `/openapi.json` — only when `settings.debug` is set, otherwise disabled entirely

`OPTIONS` requests always pass, for CORS preflight.

---

## Startup sequence

`app/main.py`'s lifespan handler, in order: initialize the database → construct the LLM client → build the profile registry and register every persona → build the Telegram bot registry for agents with it enabled → build the capability registry and register tools in tier order (Legion → Skills → MCP → Adapters) → run a non-fatal health check across registered capabilities → build the WebSocket manager, agent registry, external-peer proxy, and memory recall cache → build the session manager → build the orchestrator and wire the dispatcher → start the detached turn runner and the Telegram gateway → publish everything onto `app.state` → wire Legion/dispatch completion report hooks → best-effort start the sandbox launcher and Forge peer launcher → sweep dispatch tickets orphaned by a prior crash → recover background jobs left `"running"` at boot → reconcile Lockdown Protocol state if it was left engaged.

Shutdown reverses the pieces that need it: turn registry, dispatcher, Legion, Forge/sandbox launchers, Telegram polling, adapter/MCP disconnect, database close.

---

## Database

SQLite (via `aiosqlite`) is the default and production store — `WAL` journal mode, a 15-second busy timeout, and foreign keys on, because it's used as a genuinely concurrent store, not just a dev convenience. Postgres (via `asyncpg`) is supported for scaled deployments. All models subclass `Base` from `app/database.py`.

---

## Testing

```bash
# from packages/igor
uv run pytest
```

`asyncio_mode = "auto"` — async tests need no explicit marker. Roughly three dozen test modules cover the orchestrator, dispatch, the named safety protocols, Legion, House Party, reminders, memory, navigation, and config precedence. The CI pipeline does not currently run this suite — it does a dependency sync, a byte-compile pass, and an import smoke test. Running `pytest` locally before a change lands is a manual step, not an enforced gate.
