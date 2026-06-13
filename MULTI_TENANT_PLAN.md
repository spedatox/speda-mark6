# Multi-Tenant Architecture Migration Plan — SPEDA Mark VI

**Status:** APPROVED — implementation underway. Phase 0 (governance) and Phase 1 (identity threading) are **done**. Phases 2–5 pending.

**Scope:** Collapse the "fork-per-agent" Superior Six model into a single multi-tenant backend where all agents run as profiles inside one FastAPI process, sharing one event loop, one database, and one `CapabilityRegistry`, addressed by `agent_id`.

### Resolved decisions (all nine OQs)

| OQ | Decision |
|---|---|
| **OQ1** | `CLAUDE.md` amended — multi-tenant is the canonical model. ✅ done |
| **OQ2 / OQ3** | **Fully shared memory.** All in-process agents read/write the same memory files and facts. **No `agent_id` on `memory_files` or `memories`** — those tables are unchanged. The unique-constraint wrinkle disappears entirely. |
| **OQ4** | **WebSocket transport stays.** Optimus is a standalone external framework that connects back as a WebSocket peer. `websocket/manager.py`, `agent_registry.py`, `/agents/ws/{id}` are retained for Optimus only. |
| **OQ5** | **Six separate profile files** — the agents differ enough (model policy, allowlist, voice) to each warrant their own module. No data-driven factory. |
| **OQ8** | **Canonical roster:** in-process profiles = Sentinel, NightCrawler, Ultron, Centurion, Atomix (+ SPEDA orchestrator). Optimus = external WebSocket peer. (Replaces the README's Unicron/Ratchet/Optimus-in-list naming.) |
| **OQ6 / OQ7 / OQ9** | Deferred to Phase 5 (MCP concurrency, Skyfall licensing, rate-limit policy) — only relevant once multi-agent dispatch goes live. |

### Net effect on the schema delta

Because memory is fully shared, the only table that gains a column is **`automations`** (`agent_id`, default `"speda"`). `sessions.agent_id` already exists. `memory_files`, `memories`, `messages` are **untouched**.

---

## 1. Summary — Current vs. Proposed

### Current architecture (as built)

- **One profile, one process.** `app.state.profile` holds a single `SPEDAProfile`. `AgentOrchestrator.__init__` binds that one profile permanently (`self._profile`). `build_system_prompt()` reads `self._profile`.
- **`AgentContext` has no `agent_id`.** Request state carries `user_id`, `session_id`, `model`, etc., but the agent's identity is implicit — it is whatever single profile the process was started with.
- **Superior Six = external forks.** Each sibling agent is meant to be a *separate deployment* of this repo with a swapped `prompts/core/01_identity.md`. They connect *back* to a central backend over WebSocket for presence and task dispatch (`websocket/manager.py`, `core/agent_registry.py`, `routers/agents.py`, `websocket/protocol.py`).
- **Presence is socket-based.** `AgentRegistry._online` is an in-memory dict populated by WebSocket connect/disconnect; `agent_registry` table persists status for audit.
- **One shared registry, no per-agent scoping.** `CapabilityRegistry.list_tools()` already supports two filters (`active_servers` for lazy loading, `offline_only` for Dead Zone Protocol) but has **no per-agent allowlist**. Every agent would see every tool.
- **Sessions already carry `agent_id`** (`Session.agent_id`, default `"speda"`) — but nothing scopes queries by it, and `SessionManager.get_or_create` accepts it while `/chat` never sets it.

### Proposed architecture (multi-tenant)

- **N profiles, one process.** A `ProfileRegistry` on `app.state` holds all enabled agent profiles, looked up by `agent_id`. The orchestrator becomes *stateless w.r.t. identity* — it resolves the profile per request from `context.agent_id`.
- **`agent_id` becomes first-class request state** on `AgentContext` (Rule 3: single source of truth).
- **Superior Six = profiles, not forks.** Sentinel, NightCrawler, Ultron, Optimus, Centurion/Unicron, Atomix/Ratchet are profile + prompt-directory pairs inside this repo. Addressed via `/chat/{agent_id}` and the already-existing `/trigger/{agent_id}`.
- **Presence collapses to in-process lookup.** "Is agent X available?" becomes "is `agent_id` an enabled profile in the `ProfileRegistry`?" — a dict lookup, not a socket. The WebSocket agent transport is retired (or repurposed; see §7-OQ4).
- **Per-agent tool scoping is declarative.** Each profile declares a tool/server allowlist; `CapabilityRegistry.list_tools()` gains an allowlist filter alongside the two it already has. The registry stays the single source of truth (Rule 5); the profile only *declares*, the registry *filters*.
- **Inter-agent delegation is an in-process dispatch primitive** (House Party Protocol), routed through the orchestrator — never agent-to-agent directly (satisfies the existing "all inter-agent comms route through the backend" rule, just by function call instead of WebSocket).

### Why this is a net simplification

The current model requires six deployments, six databases, six env files, a WebSocket presence protocol, and cross-process task serialization — all to coordinate agents that serve **one** owner. Multi-tenancy replaces all of it with profile selection and in-process calls. The genuinely valuable invariants of the current design — identity lives in profiles, the engine is identity-free, the registry is the sole tool authority — are *preserved and strengthened*, because they were always about separation of concerns, not separation of processes.

---

## 2. Session & Memory Partitioning

This is the highest-leverage design decision and needs Ahmet's sign-off (§7-OQ2).

### Sessions — fully isolated per `(user_id, agent_id)`

Conversation history should be **fully partitioned by agent**. Sentinel's financial chats must not appear in Ultron's academic session list, and reconstructing history for an agent must only pull that agent's messages. `Session.agent_id` already exists; the change is to *scope every query by it* (listing, get-or-create-latest). `Message` needs no `agent_id` column — it derives the agent through its `session_id` FK (keep it normalized).

### Memory — hybrid: shared "owner" layer + per-agent "working" layer

Memory is where naive isolation hurts. There are two memory subsystems today:

- **`memory_files`** (the Anthropic memory pattern): `owner.md`, `current.md`, `dossier.md`, `log.md`, `history.md`, keyed by `(user_id, path)`.
- **`memories`** (extracted facts): per-`user_id` rows mined by background Haiku tasks.

If every agent gets fully isolated memory, each one re-learns who the owner is from zero — terrible continuity. If all memory is shared, agents bleed domain context into each other (Sentinel's budget log pollutes Ultron's voice). **Recommended split:**

| Memory file | Layer | Rationale |
|---|---|---|
| `owner.md` | **Shared** (all agents) | Who the owner *is* — identical for every agent serving him |
| `dossier.md` | **Shared** | Behavioural model of the owner — universal |
| `history.md` | **Shared** | Mined facts from past conversations — universal |
| `current.md` | **Per-agent** | "What's active right now" differs per domain |
| `log.md` | **Per-agent** | Session log is domain-specific |
| domain notes (new) | **Per-agent** | e.g. Sentinel's portfolio notes |

Mechanism: add a **nullable `agent_id`** to `memory_files`. `NULL` = shared/owner layer; a value = agent-scoped. The memory tool resolves a read by checking the agent-scoped row first, then falling back to the shared row. `memories` (extracted owner-facts) stays **shared** (`agent_id` nullable, default NULL) since extraction is about the owner, not the agent — but the column exists so a future domain-fact stream can scope itself.

**Tradeoff statement for the record:** Shared-owner + per-agent-working maximizes continuity *and* identity separation at the cost of a slightly more complex resolution path (two lookups with fallback) and a `NULL`-discriminator unique-constraint wrinkle (§4). The two rejected alternatives — *fully isolated* (clean code, amnesiac agents) and *fully shared* (simple, identity bleed) — are both worse for a personal-assistant suite.

---

## 3. CapabilityRegistry Scoping (per-agent tools)

**Declarative allowlist on the profile, applied by the registry.** Not runtime-computed.

- Each `AgentProfile` declares an allowlist — which skills, MCP servers, and toolsets it may use. Examples: Optimus → `sandbox`, `github`, `filesystem`; Sentinel → `alpha_vantage`, `tavily`, finance skills; NightCrawler → `tavily`, `exa`, `arxiv`, `playwright`. SPEDA (orchestrator) → broad/all.
- `CapabilityRegistry.list_tools()` gains a third filter parameter (an agent allowlist) beside the existing `active_servers` and `offline_only`. The registry remains the only component that enumerates tools (**Rule 5 preserved**); the profile only declares policy (**Rule 10 preserved** — capability policy is identity, lives in profiles).

**Interaction with the "3–4 sentence tool description" rule and context window:** No conflict — and it *helps*. Per-agent filtering *reduces* the tool count each agent sees, shrinking its prompt prefix. Combined with the existing lazy-loading (only always-on servers in-prefix, rest pulled via `use_toolset`), a scoped agent's cached prefix is smaller than today's. The authoring rule (each description ≥ 3–4 sentences) is unaffected; it governs how tools are written, not how many are exposed.

**Shared connections, filtered views:** MCP servers still connect once at startup (shared `_mcp_clients`) — connection cost is paid once. Each agent only *sees* its allowed subset in `list_tools()`. This is strictly better than per-fork connections.

---

## 4. Database Schema Diff (table/column level)

Single shared Postgres. `agent_id` as a discriminator column is sufficient — **no table needs to split into per-agent tables.**

| Table | Change | Notes |
|---|---|---|
| `sessions` | *(no new column — `agent_id` already exists)* | Add composite index `(user_id, agent_id, started_at)` for scoped listing |
| `messages` | **No change** | Agent derived via `session_id` FK; keep normalized |
| `memories` | **+ `agent_id` (String, nullable, indexed)** | NULL = shared owner-fact; value = domain-scoped fact |
| `memory_files` | **+ `agent_id` (String, nullable)**; **change unique constraint** `uq_memory_file_user_path` → `(user_id, agent_id, path)` | ⚠️ NULL-in-unique-key behavior differs across DBs (Postgres treats NULLs as distinct). Resolve by using a sentinel like `"_shared"` instead of NULL for the shared layer, so the constraint behaves predictably. **Decision needed (§7-OQ3).** |
| `agent_registry` | **Repurpose** from live presence → **agent roster/config** | Columns `status`/`last_seen`/`current_session_id` lose meaning in-process. Becomes `agent_id, display_name, domain, enabled, (optional) tool_allowlist`. Alternatively keep config in code profiles and drop this table. **Decision needed (§7-OQ4).** |
| `automations` | **+ `agent_id` (String, default `"speda"`)** | So a watcher belongs to the agent that created it and pushes through that agent's voice |
| `users` | No change | Single-owner model unchanged |
| `tool_calls`, `notifications` | Add `agent_id` only if per-agent audit/analytics is wanted | Optional; defer |

No destructive migrations: every change is additive (new nullable columns / new indexes) except the `memory_files` unique-constraint swap, which is the one migration needing care.

---

## 5. File-by-File Change List (descriptions only — no code)

### Core plumbing

- **`app/core/context.py`** — Add `agent_id: str` as a first-class field (Rule 3). It is the discriminator the orchestrator, session manager, registry filter, and memory layer all key off. (House-Party-only fields — `dispatch_chain`, `dispatch_depth` — are added in the final phase, not now; until then `extra` can hold them.)
- **`app/core/orchestrator.py`** — Remove the single bound `self._profile`. Resolve the profile per run from `context.agent_id` via the `ProfileRegistry` (injected instead of one profile). `build_system_prompt(context)` selects that agent's prompt sections + model policy. Pass the profile's tool allowlist into `registry.list_tools(...)`. **Rule 2 preserved** — the orchestrator still *owns* prompt construction; it just chooses which profile to build from. The 30-iteration guard is unchanged for the top-level loop.
- **`app/core/session_manager.py`** — `get_or_create` already accepts `agent_id`; add an agent-scoped "latest session" lookup and an agent-scoped `list_sessions(user_id, agent_id)`. History load is already session-scoped (no change). Ensure new sessions always stamp the real `agent_id`, not the default.
- **`app/core/registry.py`** — `list_tools()` gains an agent-allowlist filter parameter (third filter alongside `active_servers`, `offline_only`). Add a small helper to intersect the unified tool set with a profile's declared allowlist. No change to registration order or the shared-instance model.

### Profiles (the identity layer)

- **`app/profiles/base.py`** — `AgentProfile` ABC gains: `agent_id`, `domain`, a declarative `tool_allowlist`, and a per-agent prompt-section list (so each agent assembles from its own prompt directory). `allocate_model()` stays per-profile.
- **`app/profiles/speda.py`** — Becomes one profile among several: set `agent_id="speda"`, declare its (broad) allowlist. Model IDs stay here (**Rule 10**).
- **`app/prompts/core/`** — Today this is SPEDA's single identity set. Restructure so each agent has its own prompt directory (e.g. `prompts/agents/{agent_id}/01_identity.md …`) while shared policy sections (formatting, visual output, memory protocol) can remain common. The prompt loader resolves agent-specific first, shared as fallback.

### Routers (thin surface)

- **`app/routers/chat.py`** — Introduce the agent dimension: `/chat/{agent_id}` (recommended, explicit and cache-friendly) or an `agent_id` body field. Scope session listing/creation by `agent_id`; set `context.agent_id`; resolve `model` via the *agent's* profile, not a global one. `/sessions` listing becomes agent-scoped.
- **`app/routers/trigger.py`** — Already has `agent_id` in the path. Change: stop pulling a single `app.state.profile`; resolve the profile for `agent_id` from the `ProfileRegistry`, set `context.agent_id`, and (the recent push-delivery fix stays) deliver through that agent's voice. Unknown `agent_id` → 404.
- **`app/routers/agents.py`** — Repurpose. `GET /agents` lists *available profiles* from the `ProfileRegistry` (roster), not socket-online forks. The WebSocket endpoint `/agents/ws/{agent_id}` is retired (or kept only if external forks remain a supported mode — §7-OQ4).

### Presence/WebSocket (largely retired)

- **`app/core/agent_registry.py`**, **`app/websocket/manager.py`**, **`app/websocket/protocol.py`** — In pure multi-tenant mode these collapse: presence = profile-loaded check; dispatch = in-process call (§6). Recommend keeping them dormant/quarantined rather than deleting in the same phase, in case a hybrid (some external forks) is ever wanted. Their `app.state` wiring in `main.py` is removed once nothing references them.

### Models

- **`app/models/memory.py`** — add nullable indexed `agent_id`.
- **`app/models/memory_file.py`** — add `agent_id`; change unique constraint (§4).
- **`app/models/automation.py`** — add `agent_id` (default `"speda"`).
- **`app/models/agent.py`** — repurpose to roster/config or retire (§4 / §7-OQ4).
- **`app/models/session.py`** — add composite index only.

### Assembly & services

- **`app/main.py`** — Build a `ProfileRegistry` from all enabled profiles; put it on `app.state` in place of the single `app.state.profile`. Construct the orchestrator with the registry (not one profile). Remove `ws_manager`/`agent_registry` wiring once retired. MCP servers still connect once (shared).
- **`app/services/memory.py`** — Background fact extraction and file updates become agent-aware: owner-layer writes go to the shared files; working-layer writes (`current.md`, `log.md`, domain notes) go to the `(user, agent)` scope. Title generation is per-session (already agent-scoped via the session).
- **`app/mcp/servers.py`** — **No change** to registration (servers shared). Allowlist lives in profiles, applied by the registry.
- **Auth/`app/middleware/`** — Add the license checkpoint (§ Skyfall, below) as a middleware layer if/when Skyfall is real.

### Contract & docs

- **`CLAUDE.md`** — **Must be amended** (§7-OQ1): the opening "what this repo is" paragraph, the Transport Channels table row for `websocket/manager.py`, the "separate microservices that fork this repo" statement, and the inter-agent-comms rule all describe the old model. This is a governance change requiring explicit approval, not a silent edit.
- **`README.md`** — Update the architecture section and Superior Six description after approval.

### Frontend (out of backend scope, flagged)

- **`packages/heartbreaker` / `packages/desktop`** — Need an agent switcher, `/chat/{agent_id}` calls, and per-agent session lists. The "fork the UI per agent" model (two files) is also affected — multi-tenant suggests one UI with an agent selector. Flag for a separate frontend plan.

---

## 6. New Files

| File | Responsibility |
|---|---|
| `app/profiles/registry.py` | `ProfileRegistry` — loads/holds all enabled `AgentProfile`s, lookup by `agent_id`, lists the roster. Replaces the single `app.state.profile`. |
| `app/profiles/{sentinel,nightcrawler,ultron,optimus,…}.py` | One profile per agent: `agent_id`, domain, model policy, tool allowlist, prompt-section list. (Could be data-driven from one module + per-agent prompt dirs to avoid six near-identical files — §7-OQ5.) |
| `app/prompts/agents/{agent_id}/*.md` | Per-agent identity/voice/boundary prompt sections; shared policy sections stay in a common dir with loader fallback. |
| `app/core/dispatch.py` | **(Final phase only)** The in-process inter-agent dispatch primitive (§6-design). Houses the call-stack/cycle guard and depth/budget accounting. |
| `app/middleware/license.py` | **(If Skyfall is real)** The single license checkpoint middleware. |

---

## 6-design. House Party Protocol — In-Process Dispatch Design

> Per `CLAUDE.md`, House Party Protocol is **parked until all six agents are operational** and must not be implemented yet. This is the *design*; it is scheduled last (§8 Phase 5). The 1:1 *dispatch primitive* (one agent delegating to another) is the building block; "House Party" (all agents engaged at once) is the same primitive used broadly.

### Mechanism

Inter-agent delegation is exposed to a dispatching agent as a **tool** (e.g. `dispatch_to_agent(target_agent_id, task)`), routed through the orchestrator — never a direct agent-to-agent path. This satisfies the existing rule "all inter-agent comms route through the backend": the backend *is* the dispatcher.

A dispatch:
1. Validates `target_agent_id` exists in the `ProfileRegistry` and is enabled.
2. **Cycle guard:** reads the `dispatch_chain` from the calling `AgentContext` (e.g. `["speda"]`). If `target_agent_id` is already in the chain, the dispatch is refused and returned to the caller as a tool_result error ("Sentinel is already in this delegation chain") — this prevents Speda → Sentinel → Speda loops.
3. **Depth guard:** if `len(dispatch_chain)` ≥ a max depth (recommend **2**, i.e. Speda → X → Y at most), refuse. Prevents deep delegation trees.
4. Constructs a **new `AgentContext`** for the target: `triggered_by="agent"` (this value already exists in the enum — **no new `triggered_by` value needed**, respecting that rule), the target's `agent_id`, the target's model via its profile, an ephemeral or dedicated sub-session, and `dispatch_chain = caller_chain + [target_agent_id]`.
5. Runs the target agent's orchestrator loop **with its own fresh 30-iteration budget** — see below.
6. Returns the target's final `end_turn` text to the caller as the `dispatch_to_agent` tool_result. The caller's loop continues normally.

### Iteration-budget decision

The dispatched sub-conversation gets its **own** 30-iteration guard, **not** shared with the dispatcher. Sharing would let one deep delegation starve the parent's budget. Runaway cost is instead bounded by the **depth cap** (≤2) and an optional **total-dispatch cap per top-level request** (e.g. ≤5 dispatches). Recommendation in one line: *per-loop iteration budgets, bounded globally by depth + dispatch count.*

### Sequence — Speda → Sentinel → back to Speda

```
User → POST /chat/speda
  └─ Orchestrator builds SPEDA context (chain=["speda"]), runs loop
      └─ SPEDA decides it needs finance → emits tool_use: dispatch_to_agent("sentinel", "...")
          └─ dispatch.py: validate sentinel ∈ ProfileRegistry ✓
                           "sentinel" ∉ chain ✓ ; depth 1 < 2 ✓
             new context: agent_id="sentinel", triggered_by="agent",
                          model=Sentinel.allocate_model(), chain=["speda","sentinel"],
                          tools = registry.list_tools(allowlist=Sentinel.allowlist)
             run Sentinel's orchestrator loop (own 30-iter budget) → end_turn
          └─ Sentinel's final text returned as tool_result to SPEDA's loop
      └─ SPEDA continues with Sentinel's answer in context → composes reply → end_turn
  └─ SSE stream of SPEDA's reply → User
```

A "full House Party" is this same primitive invoked across several agents within one top-level request — concurrency for that case is addressed next.

---

## 6-concurrency. Concurrency Model

- **Single user → low steady-state concurrency.** Normal use is one owner talking to one agent; n8n triggers are occasional. FastAPI's async event loop is sufficient — **no task queue / worker pool is needed for v1.** The agentic loop is I/O-bound (awaiting LLM and tool HTTP), exactly what asyncio handles well, and parallel tool execution within a loop already uses `asyncio.gather`.
- **House Party (up to ~7 loops at once)** multiplies that I/O concurrency but stays I/O-bound — still fine for asyncio. The real constraints are:
  - **Provider rate limits.** Six Sonnet loops share the org's per-minute token/request pool and could trip 429s. Mitigation already partly designed: sub-agent/secondary work runs on Haiku, a separate pool. House Party should bias non-primary agents toward cheaper models and stagger calls. **Flag as a real operational limit, not a code bug.**
  - **Shared MCP client safety.** Concurrent tool calls from multiple agents to the *same* shared MCP client/STDIO session may not be safe to interleave. **Open question §7-OQ6** — verify the MCP client is concurrency-safe; if not, add a per-server async lock or per-call sessions.
  - **DB sessions.** Each agent loop must own its own `AsyncSession` (the recent trigger fix established this pattern — the background task owns a fresh session rather than reusing the request-scoped one). SQLite (dev default) serializes writes and is fine for single-agent dev; **House Party concurrency wants Postgres** (already the prod target).

Verdict: async concurrency is sufficient; the gating factors are provider rate limits and MCP client thread-safety, both addressable without a queue.

---

## 6-skyfall. Skyfall Protocol (License Checkpoint)

> **Note:** I found **no existing Skyfall/license implementation** anywhere in the repo (not in `CLAUDE.md`, config, middleware, or services). The following is a *design for where it should live*, not a description of existing code. If a Skyfall spec exists outside the repo, this section should be reconciled against it. **(§7-OQ7)**

- **Where:** a dedicated middleware layer, mirroring the existing `APIKeyMiddleware` — validated once per request, before routing, regardless of `agent_id`. Because all six agents share one process, **one middleware gate covers the whole suite**, which is the natural consequence (and convenience) of collapsing to one backend.
- **Single-point-of-failure risk (must be flagged):** in the old fork-per-agent world, a license-server outage would fail agents *independently*. In the collapsed world, a hard synchronous license check on every request means **one license-server outage bricks all six agents simultaneously.** This is the primary architectural downside of centralization and Ahmet should accept it explicitly.
- **Mitigation:** cache the license verdict with a TTL + grace window — fail-open within grace, fail-closed after — exactly the pattern the codebase already uses for the Dead Zone Protocol's cached connectivity probe (60 s cache in `registry.dead_zone_active`). This decouples request latency and uptime from the license server's availability.
- **Open question §7-OQ7:** is Skyfall licensed **per-agent** (each agent separately entitled) or **suite-wide** (one license for all)? Per-agent means the checkpoint must read `agent_id` and check per-agent entitlement; suite-wide means a single check. This changes whether the middleware is agent-aware.

---

## 7. Risks & Open Questions (need Ahmet's decision before implementation)

- **OQ1 — `CLAUDE.md` amendment (blocking).** Multi-tenancy contradicts the document's stated identity ("separate microservices that fork this repo"), the Transport Channels table, and the inter-agent-comms rule. `CLAUDE.md` is the non-negotiable contract; it must be formally amended first. **Nothing should be built until this is approved**, because half the rules being enforced in review assume the old model.
- **OQ2 — Memory partitioning split.** Approve the shared-owner / per-agent-working file split in §2 (or choose fully-isolated vs fully-shared). This is the one decision that's expensive to reverse after data accumulates.
- **OQ3 — `memory_files` unique constraint with a shared layer.** Use a `"_shared"` sentinel `agent_id` (predictable uniqueness) vs. nullable `agent_id` (NULL-distinctness varies by DB)? Recommend the sentinel.
- **OQ4 — Retire vs. keep the WebSocket agent transport.** Pure multi-tenant retires `websocket/manager.py`, `agent_registry.py`, and `/agents/ws/{id}`. Keep them only if you want to *also* support occasional external forked agents (hybrid). Recommend retire now, preserve in git history.
- **OQ5 — Six profile files vs. data-driven profiles.** Six near-identical profile modules, or one factory + per-agent prompt dirs + a roster table? Recommend data-driven to avoid duplication, but it depends on how divergent the agents' model policies/allowlists are.
- **OQ6 — MCP client concurrency safety.** Verify before House Party: can the shared MCP clients handle concurrent calls from multiple agent loops? If not, per-server lock or per-call session.
- **OQ7 — Skyfall reality & licensing granularity.** Does a Skyfall spec exist? Is it per-agent or suite-wide? (Determines middleware design.) Plus accept the centralization SPOF.
- **OQ8 — Agent roster naming.** README lists *Sentinel, NightCrawler, Ultron, Optimus, Unicron, Ratchet*; this task lists *…, Centurion, Atomix*. The canonical six must be fixed before profiles are authored.
- **OQ9 — Provider rate-limit budget for House Party.** Accept that all-six-at-once needs a model-tier/staggering policy to avoid 429s; decide the policy.

---

## 8. Recommended Implementation Order

**Do the multi-tenant skeleton *now*, before deep MCP/Agent-SDK wiring. Defer House Party to last.**

Rationale: the `agent_id` threading touches the core (`AgentContext`, orchestrator, session manager, routers). Doing it now — while SPEDA is the only live agent and the others are config-only — means near-zero migration cost (no six deployments, no cross-fork data to reconcile). If MCP/Agent-SDK are fully wired first, that tool-routing and sub-agent code gets reworked under multi-tenancy anyway. Build the scoping mechanism into the registry *now* so later MCP wiring lands in a multi-tenant-aware registry. House Party genuinely must be last (`CLAUDE.md` parks it until all six are operational).

- **Phase 0 — Governance.** Resolve OQ1 (amend `CLAUDE.md`), OQ2/OQ3 (memory model), OQ8 (canonical roster). No code.
- **Phase 1 — Identity threading (no behavior change for SPEDA).** Add `agent_id` to `AgentContext`; build `ProfileRegistry`; un-bind the orchestrator from a single profile; resolve profile per request. SPEDA-only still works end-to-end. Verify via the existing Phase-1 done-signal round-trip.
- **Phase 2 — Session & memory scoping.** Scope sessions by `(user_id, agent_id)`; add the schema columns/indexes (§4); implement the shared/working memory resolution in `services/memory.py`. Still effectively single-agent until profiles exist.
- **Phase 3 — Registry scoping + second profile.** Add the allowlist filter to `list_tools()`; declare allowlists on profiles; author *one* additional agent (e.g. Sentinel) as the proof that two agents coexist with different tools/voice in one process. Routers move to `/chat/{agent_id}`.
- **Phase 4 — Roster completion + frontend.** Author the remaining agents; UI agent switcher + per-agent session lists. Retire the WebSocket agent transport (OQ4).
- **Phase 5 — Inter-agent dispatch + House Party.** Build `dispatch.py` (cycle/depth/budget guards), the `dispatch_to_agent` tool, and only then enable concurrent multi-agent operation — after MCP concurrency (OQ6) and rate-limit policy (OQ9) are settled. Skyfall middleware (OQ7) lands before any of this is exposed beyond the owner.

---

*End of plan. Awaiting review — no files beyond this document have been created or modified.*
