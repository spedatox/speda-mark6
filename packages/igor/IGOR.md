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

## Voice mode is a presentation brief

A spoken turn is not "the reply, read aloud". `app/core/surface.py` stamps a
**presentation brief** onto the live turn's newest user message whenever
`ClientContext.voice` is set: the agent narrates, and the client's canvas
carries the evidence — a figure as a stat tile, a source as a cutting, a person
as a file — each authored by the agent as a fenced `kind | SCREEN TITLE` block
and placed in the reply at the point its narration reaches it. Position in the
stream is the cue track: the reply already streams token by token, so a window
written between two spoken sentences appears between those two being heard.

It rides the per-turn context line rather than the system prompt because voice
mode is toggled mid-conversation, and a system prefix that changed mid-session
would invalidate the cached prompt for every turn after it — the same discipline
the timestamp and the surface phrase follow.

Two things shape the brief, both read at call time so Settings edits land
without a restart:

| Input | What it decides |
|---|---|
| `settings.canvas_*` | Whether there is a board at all, how many windows a turn may open, and the spoken word budgets (`canvas_spoken_words`, `canvas_briefing_words`). Budgets are **targets, never truncation** — a reply cut off mid-sentence costs the same to synthesize as a finished one and is worth less. |
| `canvas_activity_after_ms` | How long a spoken turn may stay silent before the clients open a window showing what the machine is doing. A tool firing opens it immediately regardless. It exists because a working turn and a hung one were indistinguishable from the outside. |
| `Profile.canvas_brief` | What presenting looks like for THIS agent, appended to the generic brief. Identity, so it lives in `app/profiles/` (Rule 10): Sentinel turns every figure into a tile or a chart, NightCrawler gives every source its own window with the photo it came with. |

A window's picture has to come from somewhere, and the brief forbids inventing
one: an address the model made up renders as a hole. `browse_page` therefore
returns an **Images** section alongside its links — real addresses lifted off the
page, the page's own lead image first, capped by `browser_max_images`. That is
what an agent quotes into an `image:` line.

Board pictures go through `app/routers/media.py` rather than being loaded by the
client: the clients' CSP forbids remote images, and a client that fetched one
directly would announce the owner's IP to the very server a research board is
about. The proxy is `canvas_image_*`-gated, refuses anything that is not an
image, streams against a byte cap, follows no redirects, and resolves the target
host to confirm every address it maps to is publicly routable — without that
last check an authenticated caller could point it at the Docker network or a
cloud metadata endpoint.

With `canvas_enabled` off the brief degrades to the old one — still written for
the ear, no longer asked to present, because there is nowhere to present it.
The client-side half of the same settings is served on `GET /voice/status`, so
what the agent is asked to write and what the board draws come from one place.

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

**Automations are the exception the system prompt cannot win on its own.** A
stored `intent` sits immediately above the reply the model is about to write,
and a concrete `ÇIKTI: …` section beats an abstract rule twenty thousand tokens
earlier — which is why briefings kept firing in the wrong language. Two fixes,
both needed:

- `automation_intent._SYSTEM` composes new instructions in `agent_language`, not
  in the language of the owner's wish. What it writes is stored and re-read on
  every future firing, so composing in the wish's language pinned that
  automation to that language for good.
- `trigger_runner._language_clause()` restates the contract at the END of every
  automated seed, where recency is on its side, and says explicitly that the
  intent's own language is not a signal. This is what makes automations stored
  before the fix fire correctly without a migration.

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
