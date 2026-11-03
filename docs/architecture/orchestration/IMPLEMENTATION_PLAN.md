# Implementation sequence and release evidence

Design completed 24 October 2026, following the source study begun 23 October. This plan is ready to implement locally. Production rollout is a later, separately verified operation. Preserve the existing uncommitted Iacon work.

## 1. Establish common contracts

Introduce typed room, membership, run/task, artifact, model-call, budget and event contracts in `renovated`. Keep the existing provider adapters and Iacon service boundaries. Adapt mature worker controls and workshop access rather than importing the entire final orchestrator.

Extend `AgentContext` with room/run/task IDs, persona revision, project binding, policy snapshot, root budget handle, cancellation scope and shared retrieval allowance. Replace operationally significant arbitrary `extra` fields incrementally. Keep one owner of system-prompt assembly and thin routers. Persist UTC timestamps plus original timezone where meaningful.

Create additive migrations, idempotency constraints, indexes, transactional outboxes and versioned event schemas. First reuse existing receipt stores through explicit interfaces. Define one authoritative writer per record family. Operational stores must not become a competing personal-memory database.

**Gate:** concurrent admission cannot exceed limits or create duplicate effects; denied admission performs zero paid calls. PostgreSQL transaction tests pass. Existing Iacon/provider regressions remain green.

## 2. Restore the cast and prove direct chat

Port the profile registry and selected identity modules from `final`, adapting them to the renovated policy/context interfaces. Reconcile obsolete roster and command hierarchy instructions. Separate profile, permissions and model policy. Pin revisions per run.

Migrate Nightcrawler to Friday with a stable identity/alias mapping: historical messages, memory provenance, sessions, automations and Telegram bindings retain their original identifiers and authorship. Current display and canonical routing use Friday; old links remain resolvable. Do not perform a global text replacement in evidence. Sentinel's fictional reference remains unset.

Port the existing behavior replay harness and identity/contrast fixtures. Add Friday and the exact requested portrayal criteria. Test the same inputs under each persona and provider/thinking configuration. Run paid dialogue evaluation only under explicit test budgets and verified provider configuration; this study used neither credentials nor paid calls.

**Gate:** direct chat needs no additional planner or style-rewriter call; owner-reviewed dialogue preserves identity and competence. Prompt-capture checks alone cannot pass this gate.

## 3. Build the first shared room

Add membership, invite/address/dismiss/consult/handoff operations, deterministic routing for mentions, an ordered public speaking slot, bounded collaboration rounds and persisted decision/evidence records. Add speaker-aware client messages, task cards, stop scopes and cursor-based reconnection. Adapt existing desktop/mobile subagent views where useful.

First complete scenario: user asks SPEDA to involve Optimus and Scourge in an architecture decision. Optimus proposes a design; Scourge challenges a specific assumption with evidence; Optimus revises or explains disagreement; SPEDA presents the disposition. The user can interrupt and address any participant directly. This is separate generation under each participant's identity, not a single model fabricating an entire cast transcript.

**Gate:** exact attribution survives reload; invited members receive relevant context, unrelated rooms do not leak into it; duplicate client requests and reconnection never create a second paid turn. Participation does not imply unlimited invocation. Limits terminate cycles without hiding unfinished work.

## 4. Attach durable Legion/Forge work

Port/adapt mature worker admission, sequenced events, input receipts and completion receipts. Add supervised executors, task dependencies, global concurrency and spending reservations, durable cancellation and explicit recovery transitions. Connect Forge's model adapter to the renovated provider policy; retain single-layer retry ownership.

Run read-only jobs first, then bounded writes in isolated Cells. The supervisor creates worker checkouts; the current embedded Forge path excludes worktree tools and nested workers. Do not silently enable them as part of a port. Reviewer and Decepticon permissions remain explicit, with network scope appropriate to the task.

**Gate:** crash after admission, after paid response, after tool effect, and before result delivery. Each case either resumes from a saved boundary or enters honest reconciliation. No duplicate charge/effect is assumed safe. Cancellation joins children and verifies Cell/process teardown before releasing ownership.

## 5. Prove Hisar continuity and server execution

Resolve actual Hisar mounting, artifact APIs and execution hosts before implementation depends on them. Introduce stable project identity with a mapping to existing workshop IDs. Retain current bound-chat behavior, checkpoint version checks and exclusive claims while importing project history. Avoid dual authoritative stores during cutover.

Add supervisor-written milestone checkpoints, worktree provisioning, integration ownership, revision-bound verification and recovery packets. Support both Git and non-Git projects. Add scoped server job types, authority references, effect receipts and rollback artifacts. Bind completion and state questions to the correct room/Telegram destination.

**Gate:** an Autobot changes a project, a Decepticon reviews the exact revision, the backend restarts, and a fresh worker continues from verified files and recorded remaining work. A changed global folder selection cannot redirect the old chat. A live orphaned Cell prevents a replacement writer. A stale check cannot prove a newly edited revision correct.

## 6. Roll out with evidence

Use per-feature routing flags for profiles, rooms, durable workers and project continuation. Begin with local fixtures and read-only shadow projections. Shadow evaluation must not duplicate external actions or silently double paid model calls. Enable one controlled project before wider usage. Keep the old read path available until migrated records and clients are verified.

For a production cutover, inspect the actual deployment, read the owner's production operations instructions, back up stores, reconcile live writes/claims, migrate incrementally and verify rollback. Do not replace a live memory dataset with an earlier snapshot. n8n remains the sole recurring scheduler.

## Acceptance matrix

| Concern | Required evidence |
|---|---|
| Character fidelity | Blind owner review of actual casual, technical, emotional and multi-turn replies; bilingual and compaction cases; no generic-voice regression. |
| Useful collaboration | A downstream participant correctly incorporates or disputes a prior participant's evidence. Measure task quality against the single-agent baseline as well as token/latency cost. |
| Memory | Routine recall uses injected context; dates, corrections and unresolved states survive room handoff. Shared retrieval deadline cannot be multiplied by child count. |
| Durability | Fault injection at every admission/effect/result boundary; duplicate wakeups, repeated requests, out-of-order delivery, process death and database failure. |
| Cost | Parallel reservations at the limit, retries, fallbacks, compaction, model changes, unknown usage and restarts all remain bounded. Zero configured allowance causes zero paid maintenance calls. |
| Project continuity | New chat/worker resumes the correct project; relocated paths retain identity; conflicting checkpoint revisions fail safely; unfinished artifacts survive restart. |
| Execution truth | Claimed checks have command/result/exit status and input revision. Failed, skipped and unavailable checks remain distinct. |
| Thinking/provider behavior | Native options validated per actual model; unsupported settings rejected; reasoning continuation bound to its origin; no silent model substitution. |
| User control | Stop-reply versus stop-task versus stop-run; queued steering has honest receipts; dismissing a persona does not falsely claim worker cancellation. |
| Authorization | Scope cannot grow through delegation, persona prose, retrieved instructions or host registration. Existing grants are respected without repetitive prompts. |
| Delivery | SSE/WS reconnect, Telegram duplicate update, unknown send, deleted origin room, slow subscriber and bounded queue behavior. |
| Speed | Report p50/p95 admission, context assembly, retrieval, first useful text, total answer and background interference separately. Include cold starts and concurrent maintenance. |

The first release milestone is the complete SPEDA–Optimus–Scourge room with one durable Hisar task, working budgets and crash recovery. Completion of a schema or a set of prompts alone does not meet it.
