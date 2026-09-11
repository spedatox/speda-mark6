# Igor

The backend. One FastAPI process: every agent, the memory system, the tool registry, the orchestrator loop, and the database. Every other package in this repo is a client of this one.

---

## Contents

- [Directory structure](#directory-structure)
- [Layering](#layering)
- [Local development](#local-development)
- [Core contracts](#core-contracts)
- [Projects](#projects)
- [Voice mode is a presentation brief](#voice-mode-is-a-presentation-brief)
- [Thinking](#thinking)
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
| `app/legion/` | Tier-0 anonymous workers, including Forge-backed coding and security roles |
| `app/execution/` | Heavy worker backends; adapts Igor's model client to Forge without a peer identity |
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

The mandatory write admission, typed finance projections and durable Orion controller
are documented in [Memory enforcement](../../docs/MEMORY_ENFORCEMENT.md).
The executable memory hierarchy, lifecycle, write gateway, evidence intake and
Orion audit contract are documented in [Memory contract](../../docs/MEMORY_CONTRACT.md).
Topic documents remain authoritative; `current.md` is the time-aware projection
of versioned state records. Semantic review coverage is stored explicitly and
must not be confused with the structural verifier's result.

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
async def list_sessions(db, user_id, agent_id, limit=500, project_id=None)
async def close(db, session_id)
async def load_history(db, session_id) -> list[dict]
async def truncate(db, session_id, keep) -> int
async def save_message(db, session_id, role, content) -> Message
```

`agent_id` defaults to `"speda"` — it's optional, not required. `project_id`
narrows `list_sessions` to one project's chats; left `None`, every chat comes
back, project ones included.

---

## Projects

A project is a named workspace owning its own chats, standing instructions and
knowledge base — `app/models/project.py` (`Project`, `ProjectFile`),
`app/routers/projects.py`, `app/services/projects.py`.

**Isolation is the contract, and it is the same one chat history has.** A project
carries an `agent_id`; every listing filters on it and every id-addressed route
passes through `_owned()`, which 404s on a cross-agent read — deliberately
indistinguishable from a bad id, because a client has no business learning that
another agent has a project N. There is no route that returns a project without
an agent to check it against, and that absence is the point. `build_project_block`
re-checks ownership even though its only caller already did.

**A chat's project is fixed at birth.** `sessions.project_id` is written once, on
the turn that creates the session, and `ChatRequest.project_id` is ignored
thereafter — the history was produced under one set of standing instructions and
would be misrepresented under another. The chat router validates the project
belongs to the addressed agent before stamping it; the orchestrator then reads
the id off the SESSION, never off the request body.

**Where the block sits.** `build_project_block` renders instructions + knowledge
as one system block, appended after the episodic block and deliberately NOT
`_cache`-flagged — all four Anthropic breakpoints are already spent, and the
block is byte-stable across every turn in the project, so the conversation
breakpoint behind it caches it for free.

**Knowledge files store extracted TEXT, not bytes** — the same reasoning as chat
attachments (one representation that reaches all six providers identically), and
it means the budget can be counted in characters before anything is put in front
of a model. Files go in newest-first until `projects_knowledge_max_chars` runs
out; the ones that did not fit are still NAMED in the block, so the model knows
the base is larger than what it can see.

Failure handling is where the knowledge path diverges from the chat path.
`services/attachments.extract_body` returns `(body, note)`; a chat attachment
degrades to the note (a failed turn is worse than a turn that knows a file was
unreadable), while a knowledge upload is REFUSED with it — storing "could not be
read" as knowledge means every later turn in the project reads that sentence as
a fact about the subject.

Settings: `projects_enabled`, `projects_max_files`, `projects_file_max_chars`,
`projects_knowledge_max_chars`, `projects_instructions_max_chars` — all in
`config.py` and the Projects group of `config_schema.py`.

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

## Thinking

`_ReasoningFilter` (`services/llm_client.py`) used to keep a model's reasoning
out of its answer by throwing it away — DeepSeek's own `reasoning_content`
delta field, and the `<think>…</think>` tags GLM/Ollama/generic OpenAI-compat
proxies inline into `content`, both collected only for a debug log line. It now
hands that text back instead: a new `SSEEventType.THINKING` streams it to the
client live, gated by `settings.thinking_visible_enabled` (the master switch —
off reproduces the old silent-discard behavior exactly), and `turn_runner`
folds the accumulated text into the same `_speda_meta` block that already
carries tool/file metadata (`thinking`, `thinkingRedacted`), so a reloaded
transcript can still show what a turn thought through.

**How hard a model thinks is set per model, across every provider.**
`llm_client.THINKING_LEVELS` — `none | low | medium | high` — is the one
vocabulary; `resolve_thinking_level(model_ref)` answers it for any model as the
owner's pin (`runtime_state.model_thinking`, set from the thinking control on
each row of the clients' model picker) or `settings.thinking_default_effort`
for the rest. It is keyed by MODEL, not by agent: "this model burns a minute on
a trivial turn" is a property of the model, and resolving server-side means
Telegram and scheduled runs honour it, not just the client that set it.

`thinking_request_kwargs` then speaks each provider's own dialect. Anthropic
gets its native pair; everything else gets one `reasoning_effort` key that
`_to_openai_params`/`_to_responses_params` translate on the way out:

| Provider | `none` becomes | how a level is carried |
|---|---|---|
| anthropic | no thinking requested | `adaptive` + `output_config.effort`; Haiku has no adaptive mode, so its level becomes one of the three `budget_tokens` settings |
| openai | `minimal` | `reasoning_effort` — literal `none` must never be sent: it caused the every-other-message 401 post-GA, and the gpt-5 generation rejects it outright |
| gemini | `none` on Flash/Lite, `low` on Pro | Pro *refuses* the value rather than ignoring it, so it would 400 every turn |
| vertex | `low` | three tiers only |
| zai | thinking disabled | GLM's control is binary, so `none`/`low` switch it off and `medium`/`high` leave it on |
| deepseek | thinking disabled | forced off whenever tools are present regardless — see the known limitation below |
| ollama, nvidia | — | no reasoning knob this backend can reach; `supports_thinking()` is false and the clients grey the control out rather than hiding it |

`anthropic_thinking_enabled` remains as a second, Anthropic-only gate: it
predates per-model levels and is the deployment-wide way to keep Anthropic
turns cheap regardless of what any model is pinned to. Streaming Anthropic
thinking deltas reuses
the same tagged-pump shape `_OpenAICompatStream` already uses for the
compat-provider path (`_AnthropicTaggedStream`, constructed only when a call
actually requests thinking — every other Anthropic call, including Legion and
background tasks, gets the raw SDK stream unchanged).

Two things this does NOT need, and deliberately doesn't do: strip old thinking
blocks from history before a later turn (Anthropic's own "Preserved Thinking"
mechanism handles cross-model and stale-prefix cases without our help — see
platform.claude.com/docs/en/build-with-claude/preserved-thinking — and the
codebase's existing prompt-cache discipline of stable, append-only history
already satisfies its prefix-unchanged requirement), or persist raw signed
thinking blocks to the database (`blocks_to_dicts` already carries them through
verbatim for the LIVE in-memory tool loop within one turn, which is the only
place Anthropic's API actually requires it — persisting only the display text
in `_speda_meta.thinking` is enough for a reload to show the same panel).

**Known limitation, not fixed here:** DeepSeek's API rejects `tool_choice` in
thinking mode and requires `reasoning_content` to round-trip through history
once a tool call enters it — which this codebase's agentic loop deliberately
does not do (Rule: chain-of-thought is dropped between turns). So DeepSeek
thinking stays forced off whenever tools are present
(`_to_openai_params`'s deepseek branch), which in practice is every interactive
chat turn — the infra skills (`memory`, `read_skill`, `use_toolset`,
`tool_search`) are always available regardless of an agent's tool allowlist.

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
