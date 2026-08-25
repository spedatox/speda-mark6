# CLAUDE.md — Speda Mark VI

Read this file in full before touching a single file. This is not optional.

This is the **repo-wide contract**: the rules that hold across every package and
the boundaries you may not cross. It is not an inventory. Each package carries
its own doc for what lives inside it, and every module's docstring says why it
exists — those are the detail, this is the law.

| Package | Doc | What it is |
|---|---|---|
| `packages/igor` | [`IGOR.md`](packages/igor/IGOR.md) | **Igor** — the backend core. One FastAPI process: event loop, database, memory, orchestrator, every capability tier |
| `packages/heartbreaker` | `HEARTBREAKER.md` | **Heartbreaker** — the desktop client, full roster, Stark command deck |
| `packages/striker` | `STRIKER.md` | **Striker** ("Speda Mark VI Core") — the single-agent public build. Same backend, Speda only, calm theme |
| `packages/speda-go` | `README.md` | **Speda GO** — the mobile client. Never "Heartbreaker mobile"; the Kotlin id `com.speda.heartbreaker` stays as-is on purpose (renaming it orphans every installed app's Keystore data) |
| `packages/browser` | [`docs/BROWSER.md`](docs/BROWSER.md) | Playwright in its own container — the render fallback and the owner's portal logins |
| `packages/sandbox` | — | Command execution sidecar |

Deployment target: Contabo Cloud, via GitOps — **push to main rewrites the
server.** Code lands in production before any migration you were planning to run
by hand. Write every change so both sides of that window work.

---

## What This Is

Speda (Specialized Personal Executive Digital Assistant) is a single-user,
proactive ambient AI assistant. It serves one owner.

**Multi-tenant, single process.** Speda and five of the Superior Six — Sentinel,
NightCrawler, Ultron, Centurion, Atomix — are **in-process agent profiles**
inside Igor, alongside **Orion**, the system's own maintenance and memory
custodian. Each is an `AgentProfile` subclass with its own identity, model
policy, tool allowlist and prompt directory. They share one event loop, one
database, one `CapabilityRegistry` and one owner's memory. They are addressed by
`agent_id` on every request.

A separate `warroom` profile (a `SPEDAProfile` subclass) is the **House Party
Protocol** command channel — the same brain and tools as Speda under a distinct
`agent_id`, so full-roster operations never bleed into the owner's day-to-day
Speda session.

**Optimus is the single exception.** Optimus is ONE agent whose identity, memory,
prompts and session history live here — but its work can execute on a separate
machine (the Forge peer), reached as an external WebSocket peer. The peer is
stateless per turn: which machine a turn runs on is a transport detail, decided
in exactly one place (`app/core/peer_routing.py`), never a second Optimus. The
in-process `optimus.py` profile is the proxy/fallback; while the external peer is
online, `/chat/optimus` turns and inter-agent dispatches route to it
external-first.

---

## Non-Negotiable Architecture Rules

**1. Never put logic in routers.**
Routers call `orchestrator.run(context)` and stream the result. Zero business
logic. Zero system prompt construction. Zero tool registration. If you are
writing more than 10 lines of non-trivial code in a router, you are doing it
wrong.

**2. The system prompt is owned exclusively by `AgentOrchestrator`.**
`build_system_prompt()` lives in `app/core/orchestrator.py`. Nowhere else. Never
in a router, never in a service, never inline.

**3. `AgentContext` is the single source of truth for request state.**
Every module that needs user, session, DB, model or timezone information receives
it via `AgentContext`. No module-level globals. No ad-hoc dicts. No
`context={"timezone": str}`.

**4. The agentic loop handles all stop reasons explicitly.**

| Stop reason | Behaviour |
|---|---|
| `end_turn` | Claude is done. Return the response. |
| `tool_use` | Execute tool(s), append results, **continue the loop**. |
| `max_tokens` | Truncated. Retry with a higher `max_tokens`. |
| `pause_turn` | Server tool loop hit its limit. Continue the conversation. |

The loop runs until `end_turn`. It never breaks on `tool_use`. It never breaks
after N iterations unless the safety guard fires.

**4a. The loop has a hard safety guard of 200 tool_use iterations**
(`MAX_TOOL_ITERATIONS`). Past it, yield an `ERROR` SSEEvent and terminate
gracefully. This is not a feature limit — it is a last-resort backstop against a
tool that always errors or a model that never reaches `end_turn`, raised well
above anything a real task should ever need so it stays out of the way of
legitimate long-horizon work.

**5. `CapabilityRegistry` is the only entity that knows what tools exist.**
The orchestrator calls `registry.list_tools()` and never hardcodes a tool
definition. Adding a capability = drop a file into `skills/`, `mcp/` or
`adapters/` and register it at startup. The orchestrator does not change.

**6. No module-level globals. Everything lives on `app.state`.**
Routers reach it via `request.app.state`. Initialised in the lifespan handler, in
order.

**7. Post-turn work is never done inside the SSE generator.**
Memory extraction, titles, recaps, compaction and embedding are durable
background jobs (`app/services/task_queue.py`) — committed to `background_jobs`
before they run, drained inline, recovered at startup. Never blocking the stream.

**8. Anthropic `tool_use` / `tool_result` content block format exclusively.**
Never OpenAI wire format. Never hardcoded tool call IDs.

**9. All research and retrieval capabilities are annotated read-only.**
This is what enables true parallel tool execution. Search, fetch, page reads,
market data, recall — read-only annotated.

**10. Zero identity strings in core. All identity lives in `app/profiles/`.**
Agent name, personality, system prompt sections, tool allowlist and model policy
live in `app/profiles/{agent_id}.py`. The engine is untouched by identity.
**Model IDs live exclusively in profile files** — never in `config.py`,
`orchestrator.py` or any core module.

**11. Every tool description is a minimum of 3–4 sentences.**
State what the tool does, when to use it, when NOT to use it, and what it
returns. Per Anthropic's own guidance this is the single largest factor in tool
selection accuracy. A one-line description makes a good tool unusable. Enforce it
at authoring time, not at runtime.

**12. All endpoints require authentication.**
`AuthMiddleware` validates **`X-API-Key`** on every request before any router
logic runs, in constant time against `SPEDA_API_KEY`. Unauthenticated paths are
exactly: `/health`, the OAuth callbacks
(`/oauth/{google,notion,microsoft}/callback`), and
`/telegram/webhook/{agent_id}`. The callbacks are exempt for one structural
reason — the provider sends the OWNER'S BROWSER there, and a browser navigation
cannot attach a header; **adding an OAuth provider means adding its callback to
that list**, or consent succeeds and the redirect lands on a 401 that looks like
the provider's fault. The Telegram webhook authenticates with
`X-Telegram-Bot-Api-Secret-Token` plus an owner-id allowlist instead. The n8n
trigger and the probes additionally validate `X-N8N-Secret`. `/docs`, `/redoc`
and `/openapi.json` are disabled outside `DEBUG`.

**13. Past tense is a claim, and it needs a receipt.**
Never say you did something you did not do. This is not advisory: prompt text
alone was tried and failed, so `app/services/claim_audit.py` checks assistant
messages asserting a side effect against the tools the turn actually ran.

**14. There is one clock.** `app/core/clock.py` is the only place that knows what
time it is. Never call `datetime.now()` in a feature module.

---

## Memory

Full contract: [`docs/MEMORY_ARCHITECTURE_V4.md`](docs/MEMORY_ARCHITECTURE_V4.md).
The prompt-side protocol every agent reads is `app/prompts/core/08_memory.md`;
keep the two in step.

**15. The documents under `/memories` ARE the record.**
They are not derived and nothing rebuilds them: what is written is what exists.
`owner.md` and `current.md` are the exception — Orion composes those from the
observation record, with citations verified mechanically
(`services/memory_compose.py`).

**16. Memory is a TREE of small documents, split by TOPIC — never by index key.**
One question, one file. A file answers one question about one thing, so an agent
opens the file the task is about and pays for nothing else. Split `wellness` into
profile / program / gym / log; do **not** split a ledger into one file per month
— that turns "compare July with August" into N reads and cuts a repayment
schedule in half.

**17. Every document declares its shape, and the shape is enforced.**
`app/services/memory_spec.py` is the grammar, `memory_schema.check_write` the
gate, `memory_verify` the report. A write that invents a section, writes above
the H1, drops a required section or grows an injected file past its cap is
**refused**, with the rule named and the fix stated. Checks are delta-only — a
document with pre-existing problems must stay editable, or nothing could ever be
repaired. **When a document legitimately grows a new section, widen its spec in
the same commit.** A spec that lags its document turns the verifier into noise,
and a noisy verifier gets ignored.

**18. An agent writes its own domain, and only its own.**
`finance/` is Sentinel's, `wellness/` Atomix's, `academic/` Ultron's, `cybersec/`
Centurion's, `ops/` Orion's. A write to someone else's document is refused by the
tool, not merely discouraged — hand it over with `dispatch_agent`. The owner and
Orion are exempt: the first is ground truth, the second is the custodian.

**19. A fact that stops being true acquires an end date. It is never deleted.**
Deletion is for what was never true. Something that stopped being true is
history: give it a `valid_until`, link a changed value with `superseded_by`, and
let it stay findable.

**20. Recall is HYBRID, and both halves are maintained together.**
`search_memory` and `recall_conversations` each run a vector pass (cosine over
L2-normalised embeddings) AND a keyword pass (BM25 over FTS5), fused by
Reciprocal Rank Fusion in `services/lexical.py`. Vector-only recall reliably
loses the rare literal token — a course code, a name, an amount, a Turkish word —
which is exactly what a recall query is usually built around. Anything that adds
a searchable row writes BOTH indexes, and both backfills run off the same job; an
index only one path maintains is an index that silently goes stale. Turkish text
is folded to ASCII on both sides before it reaches FTS5 (`ı`→`i` in particular:
it is a distinct letter, not a diacritic, so no tokeniser setting will do it for
you).

**21. The record is separate from the documents, and both are written.**
`record_observation` makes an individual fact findable by meaning, with its
source, its date and its place on the evidence ladder (`explicit` → `deductive` →
`inductive` → `contradiction`). Anything above `explicit` must cite the
`source_ids` it rests on; an uncited deduction is rejected. Record the
observation *and* write the document line — they answer different questions.

### 22. The pattern loop — observe, induce, pre-empt

A pattern is only worth anything **before** it fires. Noticing afterwards that he
missed a third deadline the same way is a diary entry; noticing while the plan is
being built is the point. The loop has three parts and all three are
load-bearing:

| Part | Where | Rule |
|---|---|---|
| **Observe** | `record_observation` | already happens — facts accumulate with sources and dates |
| **Induce** | level `inductive`, with `pattern_type` + `confidence` + 2+ `source_ids` | one event is a fact, two a coincidence; **a pattern is what survives the third** |
| **Pre-empt** | `/memories/patterns.md`, injected every turn | the line carries the COUNTERMEASURE, not just the finding |

- `/memories/patterns.md` is injected into every agent's prompt on every turn.
  That is deliberate and it is the whole design: a pattern that has to be
  searched for arrives too late by construction.
- Every line is `- [YYYY-MM-DD, agent_id, confidence] the pattern → the move`.
  **The arrow is required and the write is refused without it**
  (`memory_schema._patterns_violations`). A pattern with no countermeasure is an
  observation, and observations already have a home.
- Confidence governs how hard an agent leans: `high` pre-empt, `medium` build the
  counter in, `low` a hypothesis under watch. What the owner says now outranks
  any pattern — a pattern predicts, it does not overrule the man it is about.
- It is **not** a dossier member. The dossier records what he likes, dislikes and
  forbids — claims true because he said so. A pattern is induced, carries a
  confidence and can be wrong; filing a fallible inference beside a binding
  prohibition is how the two stop being told apart.
- `patterns.md` holds patterns about the OWNER. A pattern about a person or about
  a thing in an agent's domain — how a lecturer sets exams, which month spending
  overshoots — is induced identically but lives in **that subject's file**.
- Two paths feed it: any agent that notices a repeat mid-turn, and **Orion's
  nightly audit Pass 3a** — the only reader of the whole record in one sitting,
  working from the surprisal ranking rather than the top of the table.
- A stale pattern is worse than no pattern, because it silently steers the whole
  roster. Contradicting evidence lowers the confidence or removes the line.

The governing prompt is `app/prompts/core/11_patterns.md`, loaded by every
profile.

---

## Transport Channels

Three distinct channels. Do not confuse them.

| Channel | Protocol | Used for |
|---|---|---|
| `POST /chat/{agent_id}` | HTTP + SSE | A user message to a specific agent; the response streams back |
| `WS /ws` | WebSocket | Client real-time chat (bidirectional — voice, low latency) |
| `websocket/manager.py` | WebSocket | **The external peer ONLY** — Optimus/Forge presence, dispatch, results |

`WebSocketManager` is not used for in-process agents (they are profiles, not
sockets) and not for client chat sessions.

---

## Output Modes

`output_mode` is set at context construction and decides what the caller does
with the result. The orchestrator always yields `SSEEvent`; the loop is identical
for all three.

| `output_mode` | Behaviour |
|---|---|
| `respond` | Stream SSE back — the user is waiting |
| `push` | Silent processing; delivered as a push notification |
| `silent` | Background execution; stored to the DB only |

User-triggered requests always use `respond`. n8n specifies `push` or `silent` in
the trigger payload.

---

## Capability Tiers

| Tier | Type | When |
|---|---|---|
| 0 | The Legion (wire name `Task`) | Parallel multi-source work needing context isolation |
| 1 | Python Skill | We own the logic; pure Python; low latency |
| 2 | MCP Server | Third-party integration with an existing MCP server |
| 3 | OSS Adapter | A full OSS application wrapped over HTTP or subprocess |

Claude sees all four tiers identically in the tools array. The registry is the
only entity that knows the difference.

**Registration order at startup is non-negotiable:** Tier 0 → Tier 1 → Tier 2 →
Tier 3. Within Tier 1, `read_skill` and `tool_search` register first — they are
the progressive-disclosure meta-tools, and a `deferred` tool nobody can look up
is a tool that does not exist.

**Two flags shape what an agent sees:**

- `restricted_to` — the skill is registered only for named agents (`system_ops`
  is Orion's; the training generator is Atomix's).
- `deferred` — the schema loads on demand through `tool_search` instead of
  sitting in every prompt prefix. Use it for long Rule-11 descriptions that
  overlap with each other.

A capability that cannot run must not be registered at all. Tools advertising a
dead dependency cost a turn to discover, every turn — which is why the browser
skills are skipped entirely when `BROWSER_URL` is unset.

---

## Contracts

### AgentContext

```python
@dataclass
class AgentContext:
    agent_id: str                             # selects the profile; scopes sessions, automations, tools
    user_id: int
    session_id: int
    request_id: str                           # UUID, in every log line and SSE event
    triggered_by: Literal["user", "n8n", "agent"]
    trigger_payload: dict                     # raw trigger data, unmodified
    output_mode: Literal["respond", "push", "silent"]
    model: str                                # set by profile.allocate_model() — never hardcoded
    system_prompt: str                        # built by AgentOrchestrator
    conversation_history: list[dict]          # Anthropic messages format
    db: AsyncSession
    timezone: str = "UTC"
    extra: dict = field(default_factory=dict)
```

`agent_id` is the first field resolved. `request_id` propagates through every log
statement, tool call record, SSE event and background job — it is the only way to
trace a request through a multi-tool, multi-worker execution.

**`triggered_by` has exactly three values.** There is no `"schedule"` — n8n is
the catch-all for everything automated, scheduled jobs included. Do not add a
fourth.

### SessionManager

```python
class SessionManager:
    async def get_or_create(self, user_id: int, agent_id: str, triggered_by: str) -> Session
    async def close(self, session_id: int) -> None
    async def load_history(self, session_id: int) -> list[dict]
    async def list_sessions(self, user_id: int, agent_id: str) -> list[Session]
```

Sessions are scoped by `(user_id, agent_id)` — Sentinel's history never appears
in Ultron's session list. `agent_id` is required on every creation and listing
call.

---

## Layering

Import direction is one-way. A module may import from the layers above it and
never from the layers below.

```
config, database, clock
  → models → schemas
  → services (llm client, memory, lexical, embeddings, …)
  → skills / mcp / adapters / legion
  → core (registry → context → session_manager → orchestrator)
  → routers
  → main (lifespan + app factory, assembled last)
```

Middleware sits beside routers; `profiles/` sits beside core and is imported by
it, never the reverse. A service that imports a router, or core that reads a
profile's model ID, is the bug — not the layering.

---

## n8n Integration

n8n is the sole scheduling organ. **The backend never schedules anything.**

- The backend exposes `POST /trigger/{agent_id}`.
- n8n authenticates with `X-N8N-Secret` in addition to `X-API-Key`.
- `triggered_by="n8n"`; `output_mode` comes from the payload.
- The orchestrator loop is identical regardless of trigger source.

```json
{"type": "cron", "job": "morning_brief", "output_mode": "push"}
{"type": "agent_signal", "from": "Sentinel", "event": "budget_exceeded", "output_mode": "silent"}
```

The `background_jobs` queue (`services/task_queue.py`) is **not** a scheduler and
must not become one. It owns *whether work happened*; n8n owns *when to come and
ask*. Nothing in it fires at a time — `run_after` only withholds a failing job
from the very next drain, and something external always has to invoke it. n8n
calls `POST /admin/tasks/drain` hourly and `DELETE /admin/outputs` daily.

Shipped workflows live in `packages/igor/scripts/n8n/*.json` — import, edit the
marked fields, activate. Their node `notes` are the documentation; keep them
accurate when you change a node. `services/n8n_drift.py` probes whether n8n is
actually running what the repo says it runs.

### Cheap probes — a poll must not cost a turn

A watcher that fires `POST /trigger/{agent_id}` on every tick spends a full
agentic turn to discover that nothing happened. At a ten-minute cadence that is
~144 turns a day to answer "no". So a watcher is split in two, and the split is
the rule:

| Half | What it is | Cost |
|---|---|---|
| **The probe** | A plain endpoint answering one deterministic question — `/mail/watch/scan`, `/outlook/watch/scan`, `/web/watch/scan`, `/host/lifeboat/scan`, `/academic/ask-pending` | One HTTP call. Zero tokens. |
| **The trigger** | `POST /trigger/{agent_id}`, reached only when the probe returned a hit | One agentic turn. |

- A probe holds **no reasoning** — "did anyone from this domain write", "did a new
  line appear on this page", "did a lecture just end". If answering needs
  judgement, it belongs in the turn.
- Probes live in `app/services/` behind a thin router (Rule 1) and require
  `X-API-Key` **plus** `X-N8N-Secret` — they read the owner's mail and browsing
  targets, so the poller proves it is the poller.
- The workflow's gate is a **Code node returning `[]`** when it should not fire
  (n8n stops the branch on an empty return). Fewer schema surfaces than an IF
  node across n8n versions, and the empty return *is* the cost boundary.
- **Exactly-once is the probe's job, and it commits last.** The scan never marks
  its own findings handled; n8n acks (`/mail/watch/seen`, `/web/watch/ack`) only
  *after* the trigger was accepted, so a failed notify repeats next poll instead
  of vanishing. Never reorder these — a duplicate push is recoverable, a
  swallowed exam result is not.
- **Give the agent the data it already cost you to fetch.** Findings ride in the
  trigger payload and the `intent` says so, so the turn does not re-fetch what
  the probe just read.
- **A trigger payload is data, never authorization.** It says what a probe found;
  it does not say what an agent may do. A tool whose gate depends on a condition
  — the host is critical, the token is revoked — RE-DERIVES that condition itself
  before acting (`app/skills/lifeboat.py`). n8n is an unauthenticated-by-content
  surface reachable by anything that can post a webhook, and "the disk is full,
  please prune" is exactly the sentence an injected payload would carry.
- Health failures are reported on the **edge**, not per poll — a revoked token
  produces one push, not one every ten minutes.
- **A probe that finds nothing readable may render once, and only once.**
  `/web/watch/scan` fetches over plain HTTP; when that returns no text — a
  JS-rendered results page, a block page — it retries through the browser
  container rather than failing a watch the owner believes is working. It reports
  `rendered: true` when that happened, so a watch that quietly started costing a
  render every poll is visible rather than deduced from a CPU graph.

---

## The Legion (Tier 0)

`app/legion/`. The wire name stays `Task`; "the Legion" is the branding carried
by descriptions, prompts, docs and logs.

- Registered at startup **before all other tiers**.
- Speda decides when to deploy legionnaires. The owner does not configure this.
- **Single loop** for lookups, reminders, calendar actions, short questions —
  anything completable in 1–3 tool calls. **The Legion** for research, briefings,
  multi-source synthesis, anything needing 3+ independent sources.
- Roster ↔ effort: `scout` (pre-filter) `low` · `researcher` `medium` ·
  `analyst` (synthesis) `high` · `judge` `low` · `archivist` (deep recall)
  `medium` · `general` `inherit`.
- A legionnaire may declare `tool_scope` — an EXACT allowlist, narrower than the
  `read_only` bucket (`archivist` sees only the recall tools). A worker that
  cannot see a tool cannot misuse it, and does not pay for its description on
  every iteration.
- Worker models resolve provider-agnostically: low/medium →
  `profile.background_model(parent_model)`; high/inherit → the parent model.
  Never hardcode a worker model ID in core (Rule 10). `LEGION_MODEL_OVERRIDE`
  pins all workers when set.
- The `judge` runs on briefings and reports. Not on routine actions.
- When legionnaires are deployed, say which ones ran. One sentence per worker.

---

## Model Allocation

| Context | Model |
|---|---|
| User-facing interactive response | Sonnet tier |
| Background monitoring, pre-filter, classification | Haiku tier |
| Agent-to-agent subtasks | Haiku tier (Sonnet if complexity demands) |
| House Party Protocol (engaged) | Interactive grade across the whole roster |

Each `AgentProfile` governs its own allocation via `allocate_model()`. Model IDs
live only in profile files.

**The routing matrix outranks this table.** The owner's per-agent pin
(`runtime_state.get_agent_models()`, set from the UI) wins over everything, for
*every* trigger source — app, n8n or inter-agent. The table is what an
**unpinned** agent falls back to.

**Never cross providers on the engine's own initiative.** `background_model()`
derives the cheap tier from the model the turn is actually running on, via
`cheap_tier()` — same provider or nothing. If a provider declares no cheap tier,
the model in hand is used unchanged. No code path may substitute an Anthropic
model for one the owner routed elsewhere, and **no module outside
`app/profiles/` may read `haiku_model` / `sonnet_model` directly** — that read is
what silently pulled background jobs back onto Anthropic.

---

## House Party Protocol

The all-hands mode that rallies every in-process agent at once, gated by a
passphrase rather than a build flag.

- **State:** one persisted runtime flag, `runtime_state.get_house_party()` /
  `set_house_party()`. Process-wide, like budget mode — not per session.
- **Engaging it:** `POST /agents/house-party` (owner, desktop-only, passphrase
  compared in constant time) or the `house_party` tool (`app/skills/dispatch.py`,
  same gate, only on the owner's explicit instruction — never at an agent's own
  initiative). Standing down needs no passphrase.
- **While engaged:** `app/core/dispatch.py`'s broadcast primitive
  (`kind="broadcast", protocol="house_party"`) becomes available; model policy
  escalates to full interactive grade for every agent's subtasks; the
  orchestrator injects a `## HOUSE PARTY PROTOCOL — ACTIVE` block. Outside the
  protocol, broadcast is refused and dispatch stays strictly one-to-one.
- **War Room** (`agent_id="warroom"`) is the session scope it plays out in. It
  has `dispatch_target=False`: agents dispatch to `speda`, never to `warroom`.
- **Do not add a second engage path.** The passphrase gate is the only
  authorization boundary and both entry points must go through it.

---

## The Browser

Playwright in its own container (`packages/browser`), reached through
`app/services/browser.py`. Full contract: [`docs/BROWSER.md`](docs/BROWSER.md).

- **It is plan B, not last resort.** `fetch`, Tavily, Exa and the news reader all
  speak HTTP and take what the server says, which is nothing on a page whose
  content arrives by JavaScript. `browse_page` renders. It costs seconds where a
  fetch costs milliseconds, so it goes second — but a page the owner can read in
  Chrome must never be a page their assistant cannot read.
- **A portal is an account, not a scraping target.** Saved logins are records in
  runtime state; the container keeps the COOKIES, this side keeps the
  credentials, and the two never swap jobs. Sessions persist per profile via
  `storage_state`, so a portal signed into in September still works in November.
- **The three tools split by verb.** `browse_page` reads (read-only,
  parallel-safe per Rule 9). `browser_act` clicks, fills and downloads, keyed by
  a `session_id` the model passes back to stay on the same tab. `portal_login`
  authenticates — **by portal NAME**. Anything a page hands back as a download is
  pulled across and registered through `app/core/files.py`, so it reaches the
  owner as a file card rather than a path they cannot open.
- All three are `deferred`; registration is skipped entirely when `BROWSER_URL`
  is unset.

---

## Security

- **API key auth** on every endpoint (Rule 12). Constant-time compare, before
  routing.
- **The browser container** never runs in the API container and is never
  published — no host port, no Caddy site, internal network only. This is
  CVE-2025-9611's rule applied to every Playwright surface, plus a second reason
  of its own: the container holds live session cookies for the owner's portals.
  `BROWSER_TOKEN` gates every call on top of the network boundary, because a
  shared Docker network makes a service reachable, not authorized. It mounts no
  host path, holds no `.env`, cannot reach the database, and runs as `pwuser`
  with all capabilities dropped.
- **A password must never reach a model.** Portal credentials live in runtime
  state beside the OAuth refresh tokens, are masked on every read the UI
  performs, and travel app → container → page at login time only. Nothing in a
  completion, the message table, the embedding index or the memory pipeline may
  ever contain one. **Do not write a tool that takes a password as an argument,
  and never ask the owner for one in chat.**
- **MCP transport:** STDIO for local servers. HTTP/SSE only for officially
  managed remote servers (Google Workspace, Notion, Microsoft Graph) with
  OAuth 2.1. No community servers on public ports.
- **Lockdown Protocol** (`app/services/lockdown.py`) drops external inbound
  traffic the moment the flag flips, and removes exactly the rules it added on
  stand-down. **It stops deploys. That is the point — know it before you engage
  it.** And it gives its orders over SSH to the port it is sealing, so **a seal
  and its exemptions must land in ONE host command** — every `host_bridge.run()`
  is a fresh connection, and the second one dials a port the first one already
  closed. Rules are built inside the `SPEDA_LOCKDOWN` chain while nothing jumps
  to it and go live on the last line; the first exemption is `$SSH_CLIENT`, read
  on the host, because a Docker subnet we inferred is a guess and this is the one
  place a wrong guess bricks the server.

---

## Observability

- Every request gets a UUID `request_id` at context construction.
- It is attached to `AgentContext` and propagated through every log statement,
  tool call record, SSE event and background job.
- Structured JSON logs: `timestamp`, `level`, `request_id`, `module`, `message`.
- `INFO` in production; `DEBUG` via `LOG_LEVEL=DEBUG`.
- **Never `print()`.** Use the `logging` module configured in `app/config.py`.

---

## What Not To Do

- Do not write system prompt logic in any router.
- Do not hardcode a tool definition in the orchestrator.
- Do not use OpenAI wire format for tool calls.
- **Do not add internal scheduling logic.** n8n handles all of it. The task queue
  is durability, not timing.
- Do not give agents direct access to each other. In-process agents dispatch
  through `app/core/dispatch.py`; the external peer goes through
  `WebSocketManager`.
- Do not put identity strings — name, persona, model policy, tool allowlist — in
  core modules.
- Do not add a House Party engage path outside `POST /agents/house-party` and the
  `house_party` tool.
- Do not `break` after the first tool call. The loop runs until `end_turn`.
- Do not store generated files permanently. `/tmp/speda_outputs/`, cleaned via
  n8n → `DELETE /admin/outputs`.
- Do not run Playwright anywhere but its own container. Internal network only,
  never a published port.
- Do not write a tool that takes a password as an argument.
- Do not write a one-line tool description (Rule 11).
- Do not hardcode model IDs outside `app/profiles/`.
- Do not add a fourth value to `triggered_by`.
- Do not write a memory file by hand where a spec governs it — widen the spec, or
  use the verb that owns the shape.
- Do not "tidy while you are in there". The last thing that rewrote memory
  wholesale destroyed a financial ledger.

---

## Keeping This File Honest

This document rots the moment it describes something that is no longer true, and
a rotted contract is worse than none: the next session builds against the wrong
model with full confidence. So:

- **A rule change ships in the same commit as the code that changes it.**
- **Never add an exhaustive file listing here.** It cannot survive a week of
  work. Module docstrings are the inventory; this file is the law.
- When you find this file disagreeing with the code, the code wins — **say so and
  fix the file**, rather than writing around it.
