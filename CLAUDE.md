# CLAUDE.md — SPEDA Mark VI

Read this file in full before touching a single file. This is not optional.

---

## What This Repo Is

This is `speda-mark-vi` — **Igor**, the backend core of SPEDA (Specialized Personal Executive Digital Assistant). It is a single-user, proactive ambient AI assistant. Component names: **Igor** = this backend; **Heartbreaker** = the desktop client (packages/heartbreaker); **The Legion** = the sub-agent worker system (wire name `Task`).

**Multi-tenant architecture.** SPEDA and five of the Superior Six — Sentinel, NightCrawler, Ultron, Centurion, Atomix — are **in-process agent profiles** inside this single backend, alongside **Orion**, the system's own maintenance & memory custodian. Each is an `AgentProfile` subclass with its own identity, model policy, tool allowlist, and prompt directory. They share one event loop, one database, one `CapabilityRegistry`, and one owner's memory. They are addressed by `agent_id` on every request. A separate `warroom` profile (a `SPEDAProfile` subclass) is the **House Party Protocol** command channel — the same brain and tools as SPEDA under a distinct `agent_id` so full-roster operations never bleed into the owner's day-to-day SPEDA session.

**Optimus is the single exception.** Optimus is a standalone, independently deployed framework. It connects back to this backend as an external WebSocket peer via `WebSocketManager`. It is not built here and does not run in-process. (Its in-process `optimus.py` profile is only the proxy/fallback stub: while the external peer is online, `/chat/optimus` turns and inter-agent dispatches route to it external-first.)

Deployment target: Contabo Cloud. Production-grade from day one.

---

## Non-Negotiable Architecture Rules

**1. Never put logic in routers.**
Routers call `orchestrator.run(context)` and stream the result. Zero business logic. Zero system prompt construction. Zero tool registration. If you are writing more than 10 lines of non-trivial code in a router, you are doing it wrong.

**2. System prompt is owned exclusively by `AgentOrchestrator`.**
`build_system_prompt()` lives in `app/core/orchestrator.py`. Nowhere else. Never in a router, never in a service, never inline.

**3. `AgentContext` is the single source of truth for request state.**
Every module that needs user, session, DB, model, or timezone information receives it via `AgentContext`. No module-level globals. No ad-hoc dicts. No `context={"timezone": str}`.

**4. The agentic loop handles all stop reasons explicitly.**
- `end_turn` — Claude is done. Return response to user.
- `tool_use` — Execute tool(s), append results, continue loop.
- `max_tokens` — Response was truncated. Retry with higher max_tokens.
- `pause_turn` — Server tool loop hit its iteration limit. Continue the conversation.

The loop runs until `end_turn`. It never breaks on `tool_use`. It never breaks after N iterations unless the safety guard (Rule 4a) fires.

**4a. The agentic loop has a hard safety guard of 30 tool_use iterations.**
If the loop exceeds 30 iterations, yield an `ERROR` SSEEvent and terminate gracefully. This is not a feature limit — it is a safety guard against runaway loops caused by tool errors or unexpected model behaviour.

**5. `CapabilityRegistry` is the only entity that knows what tools exist.**
`AgentOrchestrator` calls `registry.list_tools()`. It never hardcodes tool definitions. Adding a new capability = drop a file into `skills/`, `mcp/`, or `adapters/` and register at startup. The orchestrator never changes.

**6. No module-level globals. Everything lives on `app.state`.**
Routers access via `request.app.state`. Initialized in the lifespan handler. In that order.

**7. Memory extraction and title generation are always `BackgroundTask`s.**
Never inside the SSE generator. Never blocking the stream.

**8. Anthropic `tool_use` / `tool_result` content block format exclusively.**
Never OpenAI wire format. Never hardcoded tool call IDs.

**9. All research and retrieval skills must be annotated read-only.**
This enables true parallel tool execution. Tavily, Exa, arXiv, Alpha Vantage, Brave Search, Fetch — all read-only annotated.

**10. Zero identity strings in core. All identity lives in `app/profiles/`.**
Agent name, personality, system prompt template, tool allowlist, and model policy live in `app/profiles/{agent_id}.py`. The engine is untouched by identity. Model IDs (`claude-sonnet-4-6`, `claude-haiku-4-5-20251001`) live exclusively in individual profile files under `app/profiles/`. They must not appear in `config.py`, `orchestrator.py`, or any core module.

**11. Every tool description is a minimum of 3–4 sentences.**
State: what the tool does, when to use it, when NOT to use it, and what it returns. This is the most critical factor in Claude's tool selection accuracy per Anthropic's own documentation. A one-line description makes a good tool unusable. Enforce this at skill authoring time, not at runtime.

**12. All endpoints require authentication.**
`AuthMiddleware` validates the **`X-API-Key`** header on every request before any router logic runs, comparing it in constant time against `SPEDA_API_KEY` (from the environment). The n8n trigger endpoint additionally validates `X-N8N-Secret`. The only unauthenticated paths are `/health` and `/oauth/google/callback`. Interactive docs (`/docs`, `/redoc`, `/openapi.json`) are disabled outside `DEBUG`. There is no public data endpoint.

---

## Transport Channels

Three distinct communication channels exist. Do not confuse them.

| Channel | Protocol | Used For |
|---------|----------|----------|
| `POST /chat/{agent_id}` | HTTP + SSE | User sends a message to a specific agent; response streams back |
| `WS /ws` | WebSocket | Flutter real-time chat (bidirectional, for voice/low-latency) |
| `websocket/manager.py` | WebSocket | **Optimus ONLY** — external peer presence, dispatch, results |

`WebSocketManager` manages the Optimus external connection. It is not used for in-process agents (Sentinel, NightCrawler, Ultron, Centurion, Atomix, Orion are profiles, not sockets) and not used for Flutter user sessions. If you are writing Flutter chat logic, use either `POST /chat/{agent_id}` (SSE) or `WS /ws` — not `WebSocketManager`.

---

## Output Modes

`output_mode` is set at context construction time and controls what the orchestrator does with the result. The orchestrator always yields `SSEEvent` — the router decides what to do with it based on this field.

| output_mode | Behaviour |
|-------------|-----------|
| `"respond"` | Stream SSE back to Flutter — user is waiting for a response |
| `"push"` | Silent processing; result delivered as Flutter push notification |
| `"silent"` | Background execution; result stored to DB only — no notification |

User-triggered requests always use `"respond"`. n8n specifies `"push"` or `"silent"` in the trigger payload. The orchestrator loop is identical for all three.

---

## Build Order

Build in this exact sequence. Nothing is built before what it depends on exists.

| Phase | Module | Notes |
|-------|--------|-------|
| 1 | `app/config.py`, `app/database.py` | Everything else imports from here |
| 2 | `app/models/` | All ORM models |
| 3 | `app/schemas/` | Pydantic schemas |
| 3.5 | `app/middleware/auth.py` | API key middleware — protects all routes |
| 4 | `app/services/anthropic_client.py` | Orchestrator depends on this |
| 5 | `app/skills/base.py` + all Python Skills | Registry depends on these |
| 6 | `app/mcp/client.py` + `app/mcp/servers.py` | Registry depends on these |
| 7 | `app/adapters/base.py` + all OSS Adapters | Registry depends on these |
| 8 | `app/core/registry.py` (CapabilityRegistry) | Orchestrator depends on this |
| 9 | `app/core/context.py` (AgentContext) | Orchestrator depends on this |
| 9.5 | `app/core/session_manager.py` (SessionManager) | AgentContext construction depends on this |
| 10 | `app/websocket/manager.py` (WebSocketManager) + `app/websocket/protocol.py` | AgentRegistry depends on this |
| 11 | `app/core/agent_registry.py` (AgentRegistry) | Routers depend on this |
| 12 | `app/core/orchestrator.py` (AgentOrchestrator) | Routers depend on this |
| 13 | `app/services/memory.py`, `app/services/n8n.py` | Background services |
| 14 | `app/routers/` (chat, trigger, agents, health, admin) | Depend on everything above |
| 15 | `app/main.py` (lifespan + app factory) | Assembled last |

---

## Directory Structure

This tree reflects the codebase as actually built — it has grown well past the
original Phase-1 skeleton (Legion, News, Telegram, Automations, the detached
turn runner, external-peer proxying) but every addition still lives inside the
tier/profile/service boundaries the rules above define. If you add a module,
add it here too — this list is the contract, not a historical snapshot.

```
speda-mark-vi/
├── CLAUDE.md
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── app/
    ├── main.py                  # lifespan + app factory — assembled last
    ├── config.py                # Settings (env-backed)
    ├── config_schema.py         # Declarative schema for the owner-facing /config UI
    ├── database.py
    ├── middleware/
    │   ├── auth.py              # X-API-Key validation — applied to all routes
    │   └── security.py
    ├── profiles/
    │   ├── base.py              # AgentProfile ABC — agent_id, domain, tool_allowlist, model policy
    │   ├── registry.py          # ProfileRegistry — loads all enabled profiles, lookup by agent_id
    │   ├── speda.py             # SPEDA orchestrator profile
    │   ├── sentinel.py          # Sentinel — finance & budget intelligence
    │   ├── nightcrawler.py      # NightCrawler — OSINT, web surveillance, research
    │   ├── ultron.py            # Ultron — academic research, knowledge synthesis
    │   ├── centurion.py         # Centurion — cyber security (owns cve_intelligence)
    │   ├── atomix.py            # Atomix — personal health (the owner's health, not system health)
    │   ├── optimus.py           # Optimus — proxy/fallback stub for the external Forge peer
    │   ├── orion.py             # Orion — Mark VI maintenance: memory custodian & host ops
    │   └── warroom.py           # War Room — House Party Protocol session-scope alias (SPEDAProfile subclass)
    ├── prompts/
    │   ├── loader.py            # Prompt assembly
    │   ├── core/                # Common sections: formatting, memory protocol, agent network, output rules
    │   └── agents/               # Per-agent identity/voice/boundary prompts (one dir per agent_id)
    ├── core/
    │   ├── orchestrator.py      # AgentOrchestrator — owns the agentic loop + system prompt
    │   ├── context.py           # AgentContext dataclass
    │   ├── registry.py          # CapabilityRegistry — all four tiers unified
    │   ├── agent_registry.py    # AgentRegistry — WebSocket-based agent presence
    │   ├── session_manager.py   # SessionManager — session lifecycle + history loading
    │   ├── turn_runner.py       # TurnRegistry — detached turns survive client disconnects (BgOps)
    │   ├── dispatch.py          # Agent-to-agent dispatch primitive — direct AND House Party broadcast
    │   ├── external_proxy.py    # ExternalAgentProxy — wired peer proxy for Optimus/Forge (app.state.agent_proxy)
    │   ├── external_chat.py     # Superseded by external_proxy.py — not constructed anywhere; confirm before touching
    │   ├── runtime_state.py     # Persisted runtime flags (budget mode, House Party engaged) — not a scheduler
    │   ├── surface.py           # Client-surface annotation for the live user turn
    │   └── files.py             # Generated-file bookkeeping under /tmp/speda_outputs/
    ├── legion/                  # The Legion (D-SA1–D-SA5) — Tier 0, registered before all other tiers
    │   ├── roster.py            # Legionnaire definitions (scout/researcher/analyst/judge/general)
    │   └── runner.py
    ├── news/                    # NightCrawler's briefing pipeline
    │   ├── collector.py
    │   ├── dedup.py
    │   ├── escalate.py
    │   └── feeds.py
    ├── telegram/                # Telegram channel (per-agent bots, in/out of the same orchestrator loop)
    │   ├── client.py
    │   ├── gateway.py           # Inbound update → normal orchestrator run
    │   ├── linking.py
    │   ├── registry.py
    │   └── renderer.py
    ├── automations/             # n8n-facing composer/manager (no internal scheduler — n8n still owns cron)
    │   ├── composer.py
    │   └── manager.py
    ├── routers/
    │   ├── chat.py              # POST /chat[/{agent_id}] (SSE), WS /ws — Flutter user-facing
    │   ├── trigger.py           # POST /trigger/{agent_id} — n8n webhook
    │   ├── agents.py            # GET /agents, House Party toggle, agent WebSocket presence
    │   ├── admin.py             # DELETE /admin/outputs — temp file cleanup (called by n8n)
    │   ├── automations.py
    │   ├── config.py            # Owner-facing settings UI, backed by config_schema.py
    │   ├── connections.py       # OAuth connections (Google, Notion)
    │   ├── files.py
    │   ├── import_chats.py
    │   ├── memory.py
    │   ├── news.py
    │   ├── telegram.py
    │   └── health.py
    ├── skills/
    │   ├── base.py              # Skill ABC
    │   ├── memory.py            # Memory tool + recall_for_context/recall_sessions_for_context + MemoryRecallCache
    │   ├── dispatch.py          # dispatch_agent / house_party tools
    │   ├── osint.py             # NightCrawler's threat-intel lookups (IP/URL/hash/breach/dark-web/crypto)
    │   ├── navigation.py
    │   ├── news.py
    │   ├── system_ops.py
    │   ├── documents.py         # PPTX / DOCX / PDF generation
    │   ├── tts.py                # Kokoro TTS
    │   ├── stt.py                # Whisper STT
    │   ├── notifications.py     # Flutter push
    │   ├── legion.py             # The Legion's Task-tool surface
    │   ├── automations.py, budget.py, health_data.py, read_skill.py, sandbox.py,
    │   │   save_file.py, search_history.py, semantic_search.py, telegram.py,
    │   │   toolsets.py, system.py
    │   └── fonts/, skill_docs/  # static assets, not modules
    ├── mcp/
    │   ├── client.py            # MCPClient base
    │   ├── servers.py           # All MCP server registrations
    │   └── google_rest.py       # Google Workspace REST bridge (OAuth 2.1, HTTP/SSE per Security section)
    ├── adapters/
    │   ├── base.py              # OSSAdapter ABC
    │   ├── gpt_researcher.py
    │   └── shannon.py
    ├── services/
    │   ├── anthropic_client.py, llm_client.py   # Multi-provider LLM routing (llm_client) + model catalog
    │   ├── memory.py, memory_store.py           # Post-turn tasks (title/recap/compaction), revision log
    │   ├── attachments.py       # Upload text extraction + build_user_content (turn content-block assembly)
    │   ├── chat_history.py      # rows_from_messages — stored-message → UI-row shaping
    │   ├── errors.py            # friendly_provider_error — cross-provider error translation
    │   ├── welcome.py           # Home-screen welcome remark + WelcomeCache
    │   ├── compaction.py, embeddings.py, embedding_indexer.py, history_indexer.py
    │   ├── health.py            # Atomix health-sample ingestion
    │   ├── forge_peer.py, sandbox_launcher.py   # External backend (Forge) process/session bridge
    │   ├── pending_asks.py      # Permission asks relayed from external peers
    │   ├── telegram.py
    │   └── n8n.py, n8n_api.py   # Webhook auth, trigger formatting
    ├── models/                  # ORM models — one file per table (user, session, message, agent,
    │                            # agent_message, automation, health_sample, memory/memory_file/memory_revision,
    │                            # message_embedding, news_item/news_quota/news_watch, notification, tool_call)
    ├── schemas/
    │   ├── chat.py, sse.py, agent.py, trigger.py, health.py
    ├── websocket/
    │   ├── manager.py           # WebSocketManager — Optimus/Forge external peer connections ONLY
    │   └── protocol.py          # WebSocket message type definitions (no startup step)
    └── templates/               # (currently empty)
```

---

## Capability Tiers

| Tier | Type | When to use |
|------|------|-------------|
| 0 | Task (SDK built-in) | Parallel multi-source tasks requiring context isolation — registered FIRST at startup, before all other tiers |
| 1 | Python Skill | We own the logic; pure Python; low-latency required |
| 2 | MCP Server | Third-party integration with existing MCP server |
| 3 | OSS Adapter | Full OSS application wrapped via HTTP or subprocess |

Claude sees all four tiers identically in the tools array. The registry is the only entity that knows the difference.

**Startup registration order:** Tier 0 (Task tool) → Tier 1 (Skills) → Tier 2 (MCP) → Tier 3 (Adapters). This order is non-negotiable.

---

## AgentContext Contract

```python
@dataclass
class AgentContext:
    agent_id: str                             # which agent is running — "speda", "sentinel", etc.
    user_id: int
    session_id: int
    request_id: str                           # UUID, generated at context construction, in every log line
    triggered_by: Literal["user", "n8n", "agent"]
    trigger_payload: dict                     # raw trigger data, unmodified
    output_mode: Literal["respond", "push", "silent"]
    model: str                                # set by profile.allocate_model() — never hardcoded here
    system_prompt: str                        # built by AgentOrchestrator, injected here
    conversation_history: list[dict]          # Anthropic messages format
    db: AsyncSession
    timezone: str
```

`agent_id` is the discriminator that selects the profile, scopes sessions, scopes automations, and filters tool allowlists. It is the first field resolved at context construction time.

`triggered_by` has exactly three values: `"user"`, `"n8n"`, `"agent"`. There is no `"schedule"` value — n8n is the catch-all for everything automated including scheduled jobs.

`request_id` propagates through every log statement, every tool call record, and every SSE event. It is the only way to trace a request through a multi-tool, multi-worker execution.

---

## SessionManager Contract

```python
class SessionManager:
    async def get_or_create(self, user_id: int, agent_id: str, triggered_by: str) -> Session
    async def close(self, session_id: int) -> None
    async def load_history(self, session_id: int) -> list[dict]  # Anthropic messages format
    async def list_sessions(self, user_id: int, agent_id: str) -> list[Session]
```

Sessions are scoped by `(user_id, agent_id)` — Sentinel's conversation history never appears in Ultron's session list. `agent_id` is a required parameter for all session creation and listing calls.

SessionManager lives at Phase 9.5 in the build order. AgentContext construction depends on it. It is injected into `app.state` in the lifespan handler alongside the orchestrator and registry.

---

## n8n Integration

n8n is the sole scheduling and automation organ. The backend never manages schedules internally.

- The backend exposes `POST /trigger/{agent_id}`
- n8n authenticates via shared secret in the `X-N8N-Secret` header
- `triggered_by="n8n"` is set on the AgentContext
- `output_mode` is specified by n8n in the trigger payload: `"push"` or `"silent"`
- The orchestrator loop is identical regardless of trigger source

Example n8n trigger payloads:
```json
{"type": "cron", "job": "morning_brief", "output_mode": "push"}
{"type": "watchdog", "from": "NightCrawler", "event": "market_alert", "ticker": "AAPL", "output_mode": "push"}
{"type": "agent_signal", "from": "Sentinel", "event": "budget_exceeded", "output_mode": "silent"}
```

**Temp file cleanup:** n8n runs a daily scheduled workflow that calls `DELETE /admin/outputs`. This endpoint clears files in `/tmp/speda_outputs/` older than 24 hours. The endpoint requires the `X-API-Key` header. Do not implement any other cleanup mechanism.

Do not implement any internal scheduler. Do not add cron logic to the backend. Ever.

---

## The Legion (D-SA1 through D-SA5)

The Legion is the sub-agent worker system (`app/legion/`). Wire name of the tool stays `Task` — "The Legion" is the branding carried by descriptions, prompts, docs, and logs.

- The Legion is registered at startup as Tier 0, before all other tiers. It is a Core MVP feature, not a later addition.
- SPEDA decides when to deploy legionnaires. The user does not configure this.
- Single loop for: lookups, reminders, calendar actions, short questions, any task completable in 1–3 tool calls.
- The Legion for: research, briefings, multi-source synthesis, any task requiring 3+ independent sources.
- Legionnaires ↔ effort: `scout` (pre-filter) `"low"` · `researcher` `"medium"` · `analyst` (synthesis) `"high"` · `judge` `"low"` · `general` `"inherit"`.
- Worker models resolve provider-agnostically: low/medium effort → `profile.background_model(parent_model)` (cheap tier, same provider); high/inherit → the parent model. Never hardcode worker model IDs in core (Rule 10). `LEGION_MODEL_OVERRIDE` (legacy alias `SUB_AGENT_MODEL`) pins all workers when set.
- The judge legionnaire runs on briefings and reports only. Not on routine actions.
- When legionnaires are deployed, SPEDA informs the user which workers ran. One sentence per worker.

---

## Model Allocation (D-C4)

| Context | Model |
|---------|-------|
| User-facing interactive response | claude-sonnet-4-6 |
| Background monitoring, pre-filter, classification | claude-haiku-4-5-20251001 |
| Agent-to-agent subtasks | claude-haiku-4-5-20251001 (Sonnet if complexity demands) |
| House Party Protocol (engaged) | claude-sonnet-4-6 across all agents — full interactive grade, not the background tier |

Each agent's `AgentProfile` governs its own model allocation via `allocate_model()`. Model IDs live exclusively in individual profile files under `app/profiles/`. The `ProfileRegistry` loads all profiles at startup and attaches them to `app.state`. The orchestrator resolves the correct profile from `context.agent_id` and calls its `allocate_model()` at context construction time.

---

## House Party Protocol

Shipped (D-C4's "future" row above is live) — the all-hands mode that rallies every in-process agent at once, gated by a passphrase rather than a build flag.

- **State:** a single persisted runtime flag, `app/core/runtime_state.py::get_house_party()` / `set_house_party()`. Not a session or per-agent setting — it is process-wide, like budget mode.
- **Engaging it:**
  - Owner-driven: `POST /agents/house-party` (`app/routers/agents.py`). Engaging requires the `house_party_passphrase` (constant-time compare); standing down does not.
  - Agent-driven: the `house_party` tool (`app/skills/dispatch.py`) — same passphrase gate, invocable only on the owner's explicit instruction, never at an agent's own initiative.
- **What changes while engaged:**
  - `app/core/dispatch.py`'s broadcast primitive (`kind="broadcast", protocol="house_party"`) becomes available — fan `task` out to every in-process agent at once. It is refused outside the protocol; normal dispatch stays strictly direct, one-to-one.
  - Model policy escalates to full interactive grade (`profile.allocate_model("user")`) for every agent's subtasks, not the background tier — this is what makes House Party Protocol expensive and why it defaults off.
  - `AgentOrchestrator.build_system_prompt()` injects a `## HOUSE PARTY PROTOCOL — ACTIVE` block so every agent knows the mode is live and how to hand it back down.
- **War Room** (`app/profiles/warroom.py`, `agent_id="warroom"`) is the session-scope channel this plays out in — see "What This Repo Is" above. It is a `SPEDAProfile` subclass with `dispatch_target=False`: agents still dispatch to `speda`, never to `warroom` directly.
- Do not add a second engage path (e.g. a raw runtime-state write from a router or skill outside `agents.py`/`dispatch.py`) — the passphrase gate is the only authorization boundary and both entry points must go through it.

---

## Security

- **API key auth:** All endpoints require `X-API-Key` header. Validated in `app/middleware/auth.py` before routing.
- **n8n trigger auth:** `POST /trigger/{agent_id}` additionally validates `X-N8N-Secret`. Both checks must pass.
- **Playwright MCP (`@playwright/mcp`):** CVE-2025-9611 (CSRF vulnerability). Must run in an isolated Docker container. Never expose the Playwright MCP port to the public network. Internal Contabo network only. Applies to NightCrawler (in-process profile). Optimus manages its own Playwright isolation as a standalone deployment.
- **MCP transport:** STDIO for all local MCP servers (subprocess on Contabo). HTTP/SSE only for officially managed remote servers (Google Workspace, Notion) with OAuth 2.1. No community servers exposed on public ports.

---

## Observability

- Every request gets a UUID `request_id` at context construction time.
- `request_id` is attached to `AgentContext` and propagated through every log statement, tool call record, SSE event, and background task.
- Log format: structured JSON. Fields: `timestamp`, `level`, `request_id`, `module`, `message`.
- Log level: `INFO` in production. `DEBUG` enabled via `LOG_LEVEL=DEBUG` in `.env`.
- Do not use `print()`. Use the standard `logging` module configured in `app/config.py`.

---

## What Not To Do

- Do not break the build order. Phase N assumes Phase N-1 is complete and tested.
- Do not write system prompt logic in any router.
- Do not hardcode any tool definition in the orchestrator.
- Do not use OpenAI wire format for tool calls.
- Do not add internal scheduling logic. n8n handles all of that.
- Do not give agents direct access to each other. In-process agents dispatch through `app/core/dispatch.py` (the orchestrator-routed primitive); Optimus communicates only via WebSocket through `WebSocketManager`.
- Do not put identity strings (agent name, persona, model policy, tool allowlist) in core modules. They belong in `app/profiles/{agent_id}.py`.
- Do not add a House Party engage path outside `POST /agents/house-party` and the `house_party` tool — both are the only places the passphrase gate is enforced (see the House Party Protocol section).
- Do not use `break` after the first tool call. The loop runs until `end_turn`.
- Do not store generated files permanently. `/tmp/speda_outputs/` with 24-hour cleanup via n8n → `DELETE /admin/outputs`.
- Do not run Playwright MCP without container isolation. CVE-2025-9611. Internal network only.
- Do not write one-line tool descriptions. Minimum 3–4 sentences per Rule 11.
- Do not hardcode model IDs outside of `app/profiles/`. Each agent's model IDs live in its own profile file only.
- Do not add a fourth value to `triggered_by`. n8n covers all automated triggers including scheduled jobs.

---

## Done Signal for Phase 1

All of the following must pass before Phase 1 is considered complete:

1. `main.py` lifespan handler runs clean from top to bottom with zero errors.
2. All capabilities registered. All health checks pass (degraded adapters logged, not fatal).
3. WebSocket endpoint accepts a connection from a test agent and receives the registration handshake.
4. A full synthetic chat round-trip completes end-to-end: user message → `AgentOrchestrator.run()` → at least one tool call → tool result appended → `end_turn` → SSE stream closed cleanly.
5. Auth middleware rejects a request with a missing or invalid `X-API-Key` with HTTP 401.

All five. Not four.
