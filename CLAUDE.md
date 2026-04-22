# CLAUDE.md — SPEDA Mark VI

Read this file in full before touching a single file. This is not optional.

---

## What This Repo Is

This is `speda-mark-vi` — the backend core of SPEDA (Specialized Personal Executive Digital Assistant). It is a single-user, proactive ambient AI assistant. SPEDA is the orchestrator. The Superior Six (Sentinel, NightCrawler, Ultron, Optimus, Unicron, Ratchet) are separate microservices that fork this repo and swap identity profiles. They are not built here.

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

**10. The repo is rebrandable. Zero identity strings in core.**
Agent name, personality, system prompt template, and model policy live in `app/profiles/`. The fork swaps the profile. The engine is untouched. Model IDs (`claude-sonnet-4-6`, `claude-haiku-4-5-20251001`) live exclusively in `app/profiles/speda.py`. They must not appear in `config.py`, `orchestrator.py`, or any core module.

**11. Every tool description is a minimum of 3–4 sentences.**
State: what the tool does, when to use it, when NOT to use it, and what it returns. This is the most critical factor in Claude's tool selection accuracy per Anthropic's own documentation. A one-line description makes a good tool unusable. Enforce this at skill authoring time, not at runtime.

**12. All endpoints require authentication.**
A single API key (`X-API-Key` header) is validated by middleware before any router logic runs. The key lives in `.env` as `SPEDA_API_KEY`. The n8n trigger endpoint uses its own separate shared secret (`X-N8N-Secret`) in addition. There is no public endpoint.

---

## Transport Channels

Three distinct communication channels exist. Do not confuse them.

| Channel | Protocol | Used For |
|---------|----------|----------|
| `POST /chat` | HTTP + SSE | User sends a message; response streams back to Flutter |
| `WS /ws` | WebSocket | Flutter real-time chat (bidirectional, for voice/low-latency) |
| `websocket/manager.py` | WebSocket | Superior Six agent connections ONLY — presence, dispatch, results |

`WebSocketManager` manages agent connections. It is not used for Flutter user sessions. If you are writing Flutter chat logic, you are using either `POST /chat` (SSE) or `WS /ws` — not `WebSocketManager`.

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

```
speda-mark-vi/
├── CLAUDE.md
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── app/
    ├── main.py
    ├── config.py
    ├── database.py
    ├── middleware/
    │   └── auth.py              # API key validation — applied to all routes
    ├── profiles/
    │   ├── base.py              # AgentProfile ABC — name, system prompt template, model policy
    │   └── speda.py             # SPEDA identity — fork this for Superior Six
    ├── core/
    │   ├── orchestrator.py      # AgentOrchestrator — owns the agentic loop + system prompt
    │   ├── context.py           # AgentContext dataclass
    │   ├── registry.py          # CapabilityRegistry — all four tiers unified
    │   ├── agent_registry.py    # AgentRegistry — WebSocket-based agent presence
    │   └── session_manager.py   # SessionManager — session lifecycle + history loading
    ├── routers/
    │   ├── chat.py              # POST /chat (SSE), WS /ws (WebSocket) — Flutter user-facing
    │   ├── trigger.py           # POST /trigger/{agent_id} — n8n webhook
    │   ├── agents.py            # GET /agents — registry status
    │   ├── admin.py             # DELETE /admin/outputs — temp file cleanup (called by n8n)
    │   └── health.py
    ├── skills/
    │   ├── base.py              # Skill ABC
    │   ├── tts.py               # Kokoro TTS
    │   ├── stt.py               # Whisper STT
    │   ├── notifications.py     # Flutter push
    │   ├── documents.py         # PPTX / DOCX / PDF generation
    │   └── system.py
    ├── mcp/
    │   ├── client.py            # MCPClient base
    │   └── servers.py           # All MCP server registrations
    ├── adapters/
    │   ├── base.py              # OSSAdapter ABC
    │   ├── gpt_researcher.py
    │   └── shannon.py
    ├── services/
    │   ├── anthropic_client.py
    │   ├── memory.py
    │   └── n8n.py               # Webhook auth, trigger formatting
    ├── models/
    │   ├── user.py
    │   ├── session.py
    │   ├── message.py
    │   ├── agent.py
    │   ├── tool_call.py
    │   └── notification.py
    ├── schemas/
    │   ├── chat.py
    │   ├── sse.py
    │   ├── agent.py
    │   └── trigger.py
    └── websocket/
        ├── manager.py           # WebSocketManager — Superior Six agent connections
        └── protocol.py          # WebSocket message type definitions (no startup step)
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

`triggered_by` has exactly three values: `"user"`, `"n8n"`, `"agent"`. There is no `"schedule"` value — n8n is the catch-all for everything automated including scheduled jobs.

`request_id` propagates through every log statement, every tool call record, and every SSE event. It is the only way to trace a request through a multi-tool, multi-sub-agent execution.

---

## SessionManager Contract

```python
class SessionManager:
    async def get_or_create(self, user_id: int, triggered_by: str) -> Session
    async def close(self, session_id: int) -> None
    async def load_history(self, session_id: int) -> list[dict]  # Anthropic messages format
```

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

## Sub-Agent Policy (D-SA1 through D-SA5)

- `Task` tool is registered at startup as Tier 0, before all other tiers. It is a Core MVP feature, not a later addition.
- SPEDA decides when to spawn sub-agents. The user does not configure this.
- Single loop for: lookups, reminders, calendar actions, short questions, any task completable in 1–3 tool calls.
- Sub-agents for: research, briefings, multi-source synthesis, any task requiring 3+ independent sources.
- Effort levels: research workers `"medium"` · synthesis `"high"` · pre-filter `"low"` · judge `"low"`
- Verification sub-agent runs on briefings and reports only. Not on routine actions.
- When sub-agents are spawned, SPEDA informs the user which workers ran. One sentence per worker.

---

## Model Allocation (D-C4)

| Context | Model |
|---------|-------|
| User-facing interactive response | claude-sonnet-4-6 |
| Background monitoring, pre-filter, classification | claude-haiku-4-5-20251001 |
| Agent-to-agent subtasks | claude-haiku-4-5-20251001 (Sonnet if complexity demands) |
| House Party Protocol (future) | claude-sonnet-4-6 across all agents |

SPEDA governs model allocation. Agents do not decide independently. These model IDs live in `app/profiles/speda.py` only. The profile is loaded at startup, attached to `app.state`, and its `allocate_model()` method is called at context construction time.

---

## Security

- **API key auth:** All endpoints require `X-API-Key` header. Validated in `app/middleware/auth.py` before routing.
- **n8n trigger auth:** `POST /trigger/{agent_id}` additionally validates `X-N8N-Secret`. Both checks must pass.
- **Playwright MCP (`@playwright/mcp`):** CVE-2025-9611 (CSRF vulnerability). Must run in an isolated Docker container. Never expose the Playwright MCP port to the public network. Internal Contabo network only. Apply to both NightCrawler and Optimus deployments.
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
- Do not give agents direct access to each other. All inter-agent comms route through the backend.
- Do not put identity strings (agent name, persona, model policy) in core modules. They belong in `app/profiles/`.
- Do not implement House Party Protocol. It is parked until all six agents are operational.
- Do not use `break` after the first tool call. The loop runs until `end_turn`.
- Do not store generated files permanently. `/tmp/speda_outputs/` with 24-hour cleanup via n8n → `DELETE /admin/outputs`.
- Do not run Playwright MCP without container isolation. CVE-2025-9611. Internal network only.
- Do not write one-line tool descriptions. Minimum 3–4 sentences per Rule 11.
- Do not hardcode model IDs outside of `app/profiles/speda.py`.
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
