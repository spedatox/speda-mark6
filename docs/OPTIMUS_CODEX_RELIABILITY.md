# Optimus reliability, durable Hisar desks, worker controls and completion recovery

10 October 2026. This implements the local cancellation, settlement, admission
and filesystem fixes, durable desk/input backend and saved worker inspection,
interruption, steering and background completion recovery from the reconnaissance
plan. It does not establish full Codex parity or complete every phase's exit
conditions. Production deployment and Heartbreaker's coding-process
visualization remain separate milestones.

## Reference baseline

The inspected public Codex checkout is pinned to
`806d9732c974bc8a51b8317c1bd8985544fe627c`, cloned at
`C:/Users/ahmet/AppData/Local/CodexStudies/codex-public-20261010`.
These are source references to that revision, not assertions about every
feature of the proprietary desktop app. The Python changes adapt ownership
and state semantics; they do not copy the Rust implementation.

| Implemented behavior | Codex reference at the pinned revision | Igor / Forge implementation |
|---|---|---|
| Collect cancelled tool tasks and progress waiters before the parent settles | [Owned dispatch and capability-gated concurrency](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/tools/parallel.rs#L141) | `packages/igor/app/core/orchestrator.py`, `packages/forge/forge/warden/dispatch.py` |
| Abort an in-flight provider request and join its watcher | [Cancellation tokens and owned dispatch handles](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/tools/parallel.rs#L202) | `packages/igor/app/execution/forge.py` |
| Reap the local Docker CLI and output readers on cancellation; also collect cancelled readiness probes | [Owned task abort and cleanup](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/tasks/mod.rs#L925) | `packages/forge/forge/cell/docker_cell.py`; existing runtime teardown still confirms container removal before releasing its workshop claim |
| Reject engine EOF or an unknown model stop reason as successful completion | [Stream closed before response.completed is an error](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/session/turn.rs#L2663) | `packages/igor/app/core/turn_runner.py`, `packages/igor/app/core/orchestrator.py` |
| Save assistant history before terminal success; preserve completed, interrupted and failed outcomes | [Interruption cleanup and history flush](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/tasks/mod.rs#L925), [typed turn states](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/app-server-protocol/src/protocol/v2/turn.rs#L33) | `packages/igor/app/core/turn_runner.py`; failed history saves produce ERROR and suppress success hooks; trigger delivery receives an explicit failure instead of fetching an older answer |
| Share capacity between admitting, inline and background workers; reserve before ticket creation and release on all outcomes | [Execution permits and release through Drop](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/agent/control/execution.rs#L19), [spawn reservations](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/agent/control/spawn.rs#L735) | `packages/igor/app/legion/runner.py`; the no-await reservation is specific to Igor's single event loop |
| Admit one live parent turn per session and reject a known request ID instead of generating a different execution ID | [Active-turn ownership](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/tasks/mod.rs#L317), [explicit thread/turn addressing](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/app-server-protocol/src/protocol/v2/turn.rs#L45) | `packages/igor/app/core/turn_runner.py`, thin transport guard in `packages/igor/app/routers/chat.py`; only process-local protection, not durable idempotency |
| Parallelize consecutive read-only calls and order mutations with barriers | [Parallel-tool capability and read/write execution lock](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/tools/parallel.rs#L141) | `packages/igor/app/core/orchestrator.py`; CapabilityRegistry retains classification ownership |
| Retain worker assignments, text, tool calls/results and explicit outcomes in saved chat history | [Rollout recording and flush](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/rollout/src/recorder.rs#L1087) | `packages/igor/app/core/turn_runner.py`, `packages/igor/app/services/chat_history.py`; this is a saved view using Heartbreaker's existing card format, not a durable child-session graph |
| Bound max_tokens/pause recovery separately from tool iterations; preserve a legal transcript for unexecuted calls | [Explicit terminal outcome requirement](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/session/turn.rs#L2663) | `packages/igor/app/core/orchestrator.py`, generic configuration fields; Anthropic stop-reason recovery is an Igor adaptation, not a port of Codex's provider protocol |
| Normalize Linux file paths and check canonical targets before file operations; refuse host search paths and links outside the selected root | [Canonical policy roots and explicit sandbox boundary](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/sandboxing/src/bwrap.rs#L218) | `packages/forge/forge/cell/docker_cell.py`, `packages/forge/forge/tools/search.py`; these API checks are not equivalent to Codex's OS sandbox |
| Restore a chat's stored desk rather than today's global picker | [Persisted working directory in thread metadata](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/state/src/model/thread_metadata.rs#L161) | `packages/igor/app/services/workspaces.py`, nullable workshop references on sessions/private projects, typed `AgentContext.workshop_project_id`; Igor uses a workshop ID and adopts a stricter immutable binding policy |
| Inspect a saved worker identity separately from its live executor | [Loaded versus unloaded inspection](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/agent/control/inspection.rs#L14) | `services/worker_control.py`, `models/worker_execution.py`; flat parent-turn-to-execution records, not resumable child conversations |
| Interrupt a specific child without cancelling its supervising parent | [Targeted child interruption](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/agent/control/interrupt.rs#L16) | `legion/runner.py`, `execution/forge.py`; Forge signal stops provider/tools and confirms teardown, hard cancellation retains uncertain claims |
| Distinguish queue-only messages from starting another task | [Mailbox retention](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/agent/control/mailbox.rs#L42), [delivery modes](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/agent/control/delivery.rs#L13) | Durable `worker_inputs`, async boundary source in Forge Warden; queued and boundary-recorded receipts, no automatic follow-up/start |
| Capture a terminal child result separately from acceptance/delivery to its parent | [Completion routing and queued/failed metrics](https://github.com/openai/codex/blob/806d9732c974bc8a51b8317c1bd8985544fe627c/codex-rs/core/src/agent/control/completion.rs) | `services/completion_recovery.py`, `worker_completions`; Igor adds database recovery to Codex's best-effort completion callback, rather than claiming Codex provides this durable outbox |

## Saved worker inspection and control

Igor admits inline and background Legion workers to its own operational journal
before provider or Cell work. Each execution has a fresh UUID, parent request,
origin chat/agent/owner, model, task, desk, original input references and optional
background ticket. The Forge claim uses this execution UUID. Existing tool IDs
and background `legion-bg-*` IDs remain UI correlations. A reused UI/tool ID
cannot target a newer execution with an older control request.

Ordered progress events commit before live publication. Tool result previews
retain full results in the journal; oversized events use hash-addressed artifacts
outside the worker mount. Terminal status and the final event commit together,
after owned task cleanup. Saved success is required before a success reaches
the parent or UI. Journal admission/commit failures fail closed. The original
workshop claim remains authoritative: uncertain Cell cleanup yields unknown
and keeps the checkout fenced. A signal-driven stop can release the claim only
after confirmed Cell teardown. Repeated interruption does not cancel cleanup
again, and parent cancellation remains cancellation even during settlement.

The authenticated API is available for the upcoming Heartbreaker view:

| API | Behavior |
|---|---|
| `GET /legion/executions?session_id=...&agent_id=...` | Latest owner-scoped executions, both inline and background |
| `GET /legion/executions/{execution_id}` | Saved execution and message receipts, plus current executor availability |
| `GET /legion/executions/{execution_id}/events?after=...` | Ordered durable events and next sequence cursor |
| `GET /legion/executions/{execution_id}/attach?after=...` | Replay then tail; terminal histories remain replayable after registry eviction |
| `GET /legion/executions/{execution_id}/event-artifacts/{hash}` | Authorized, hash-verified original oversized event |
| `POST /legion/executions/{execution_id}/interrupt` | Record request and signal exactly that live execution; completion follows cleanup |
| `POST /legion/executions/{execution_id}/messages` | Queue `{text, message_id?}` for a live uninterrupted Forge execution |

`legion_inspect` and `legion_control` expose inspection/control to the parent
agent with owner/agent scope. Inspection is read-only and deliberately uncached
within a turn. Workers cannot see or execute the worker-control tools, nor any
tool outside their supplied allowlist. General worker reads may run concurrently;
mutations form ordered barriers using registry-owned capability facts.

Reuse the same `message_id` when retrying the same input. A changed text or target
under that ID is refused. A receipt starts as `queued`. Forge appends the text
at a legal tool-result or turn-end boundary, then commits the corresponding
Anthropic transcript and `boundary_recorded` receipt before its next provider
call. This says the input reached a saved boundary, not that a model acted on
it. Input arriving too late to be claimed remains queued and inspectable after
completion; it never starts new work implicitly. Steering ordinary research
workers and follow-up turns on completed children are not implemented here.

Replay survives reconstructing the service or deleting the source chat. Raw
chat exports include worker executions, events and message receipts; oversized
artifact bytes remain separate references. A stored running execution with no
live handle is exposed as unknown/unavailable. Replay ends with an explicit
unavailable observation rather than silent successful EOF. No worker or Cell
is automatically resumed, marked complete, or reconciled after a restart.
The old `/legion/active` and ticket attach paths remain compatibility surfaces
for existing clients/external peers; they retain their old in-memory behavior.

## Completion recovery

Every newly completed background worker (Forge or general Legion) commits its
terminal result/event and a unique `worker_completions` receipt in one Igor
transaction. Inline workers return directly to their parent and have no report
outbox. A failed terminal transaction never publishes saved success or leaves
an orphan receipt. Each receipt retains its worker ID, stable report request ID,
seed/response message addresses, report attempts, push attempts and last error.

Worker callbacks wake a bounded drain. Busy parent chats and full turn capacity
leave the receipt pending without appending a report seed. Parent settlement
releases its turn slot before draining again, so multiple workers can finish
together and report serially in their original conversation. Deferred receipts
rotate through bounded drains so one busy chat cannot starve other chats.
Startup and the existing authenticated `/admin/tasks/drain` invocation also
drain; no internal timer, new scheduler or n8n automation is added.

| Report state | Recovery behavior |
|---|---|
| `pending` | Wait for admission in the originating owner/agent/chat |
| `ready` | Seed + receipt committed together; engine has not started. Retry can reuse the same seed |
| `running` | Start marker committed before advancing the engine; do not blindly replay tools |
| `completed`, `failed`, `interrupted` | An exact saved terminal assistant message proves this report turn's outcome; delivery can retry separately |
| `unknown` | Started report has no saved terminal proof. Preserve evidence and require explicit reconciliation/follow-up |
| `blocked` | Origin chat/profile/desk is missing, closed or mismatched. Retain the receipt; do not open a substitute chat |

Recovery searches for the report's exact request ID and terminal metadata after
its validated seed. A saved response recovers a lost receipt acknowledgement;
a later unrelated reply is never sent in its place. Delivery failures retry
only the saved response, without replaying the worker or report engine. Empty
closing responses deliver the saved worker result with an explicit explanation.
Unknown worker outcomes remain unknown; the report framing never declares their
assignment completed. Workshop claims are never released by this service.

`delivery_status` distinguishes `pending`, `sending`, `delivered` and `blocked`.
`delivered` means Telegram accepted the push or the desktop Notification was
committed; it does not prove the owner read it.
External Telegram delivery is at least once: a timeout or restart after remote
acceptance can repeat a notification. This is not an exactly-once network claim.
The local desktop Notification and delivered receipt commit atomically; failed
fallback storage stays pending, and a lost acknowledgement after that commit
cannot insert another Notification. Cancellation joins the receipt cleanup;
turn wait/shutdown also join drains still finishing after a chat slot releases.

Existing execution inspection/listing expose `completion`, and raw chat exports
include `worker_completions`. Historic terminal workers that predate this table
are not automatically re-reported, because prior delivery cannot be inferred.
The new table is additive at normal database initialization. Recovery assumes
one Igor process owns reports; this increment does not establish distributed
turn admission, process fencing, supervised worker survival or resumable child
conversations. Generic external-peer/dispatch reporters retain their compatibility
paths and are not silently migrated to this worker outbox.

## Durable desks and original inputs

New chats snapshot their initial selected desk. A bound chat ignores subsequent
global `cwd` changes; explicit selection of a different desk is refused. A new
chat in a bound private project inherits that desk. Historic chats without a
recorded binding remain unbound even if their private project was linked later.
`workshop_update(select)` or an explicit `workshop_project_id` may establish the
first binding. Conditional session/project updates commit together, preventing
two simultaneous selections from installing conflicting references.

Owner and agent checks precede workshop lookup. Private project instructions
and extracted knowledge remain private even when two agents select the same
filesystem desk. Missing saved desks fail closed. The app-owned workspace
service resolves canonical paths before HTTP chat history writes and restores
desk IDs for Task assignments, completion reports, WebSocket and Telegram
continuations. Session/project responses and START events include the desk ID.
This does not yet update Heartbreaker's global folder indicator.

Original coding uploads have owner/agent-scoped Igor references and verified
SHA-256 bytes under the persistent root's `.forge/artifacts`, outside worker
mounts. Bound chats on the same desk and agent can reuse these originals without
re-upload. Unbound chats carry uploads live; explicitly selecting a desk during
that turn retains them. The existing extracted-text knowledge representation is
unchanged. New Telegram upload ingestion is not converted to this input store;
Telegram can restore already retained engineering inputs.

Workers receive disposable copies under their checkout's `.forge/inputs`.
Staging and cleanup now occur inside the runtime's exclusive claim, including
when a run fails or is cancelled. A refused claim stages nothing. Coordinator
metadata cannot be selected as a desk or a folder-creation parent. Missing or
corrupted originals are errors. Input IDs allow a Task to choose a subset;
limits are 64 MiB per file and 128 MiB of materialized bytes per execution.
Deleting a chat removes its input references; original blobs are preserved.
There is no automatic garbage collection or 24-hour output cleanup for originals.

This artifact store is an additional SPEDA durability requirement. The Codex
metadata reference establishes stored working-directory behavior, not an
assertion that Codex has identical upload retention or immutable desk rules.

## Runtime contract

The detached parent runner buffers its engine's terminal event while saving
history. A failed save replaces DONE with ERROR. Successful completion hooks
run only after a successful save and a successful engine outcome. Cancellation
collects the owned tool/provider tasks and saves an interruption marker before
the cancel call returns. Repeated cancellation cannot interrupt that save.
Disconnecting an SSE subscriber still leaves the detached turn running.

Saved `_speda_meta.turn` contains `request_id` and `status`. DONE gains a
`status` alongside existing usage/language fields. Error events retain their
existing text format. History rows also expose `turn` and `subagents`; the
display projection stays out of model conversation content. Worker success
requires an explicit `ok: true`. A worker with no recorded final outcome is
shown as unknown, rather than promoted to success or left indefinitely running
in a saved message.

The existing `legion_max_background_workers` configuration key now caps inline,
background and admitting workers together. The settings label/help describe
that shared limit. Background admission refuses to launch if its ticket cannot
be saved. Shutdown joins background cleanup before dropping its task registry.

`chat_max_output_tokens` is the initial response allowance. On truncation Igor
doubles it up to `chat_recovery_max_output_tokens` (default 32,768).
`chat_max_continuations` (default 3) bounds truncation and pause recovery across
the turn. The existing 30-tool-use guard remains independent. Configure the
recovery ceiling within the chosen provider/model's supported allowance; this
increment does not introduce a typed model-limit resolver. An unfinished tool
request receives an error result without executing its partial arguments.

Docker file reads/writes require GNU `realpath` with `-m` and NUL output in the
Cell image. Missing or malformed confinement output fails closed. Validation
uses Linux POSIX paths even when the coordinator runs on Windows. Host search
checks its requested root and skips file links escaping the workspace before
opening them. These checks address the reproduced traversal/link escapes;
they do not eliminate path-replacement races with an arbitrary process changing
the filesystem concurrently. Shell commands still rely on container isolation.

## Verification

Regression coverage includes blocked cleanup during cancellation, provider
abort, mutation ordering, bounded continuation, invalid terminal reasons,
failed history persistence, immediate and repeated cancellation, shared worker
capacity, concurrent ticket admission, retained inspection data, failed trigger
delivery, traversal and container symlink escapes. Docker CLI cleanup is tested
with real sleeping child processes, substituted for the CLI; this is not a
Linux container integration test. The trigger path uses isolated SQLite.

Focused verification after the additional cancellation/admission/delivery
regressions: **103 passed**. The broader affected suite passed **1,419 checks**,
with **20 platform/tooling skips**. After adding explicit closure of a suspended
engine when event collection fails, **91 affected checks passed**. Results are
recorded in
`C:/Users/ahmet/AppData/Local/CodexStudies/OPTIMUS_CODEX_RELIABILITY_TESTS_20261010.log`.
The final settlement result is in
`C:/Users/ahmet/AppData/Local/CodexStudies/OPTIMUS_CODEX_SETTLEMENT_TESTS_20261010.log`.
Tests disable pytest's cache and Python bytecode; scratch files stay in the OS
temporary directory. No production data or owner development database is used.

After the owner installed the public key, `speda-production` SSH returned
`root`. At 18:01 Istanbul time, actual Linux Docker checks passed **32 assertions**
using the installed immutable Optimus image
`sha256:2099efe2a346b6b1b6561a4b905d1a61e57e85cf6e358425c96116f104d1cf49`.
Two fresh Cells used only OS temporary directories, network disabled, with
uid 1000 and root postures. Tests covered valid files/new parents, traversal,
symlink targets/parents, escaped active directories, live command cancellation
and confirmed removal of both Cells. No live workshop directory was mounted.
The repeatable runner is `packages/forge/tests/integration/check_docker_lifecycle.py`;
its result is in
`C:/Users/ahmet/AppData/Local/CodexStudies/OPTIMUS_LINUX_DOCKER_TESTS_20261010.log`.
Candidate DockerCell SHA-256:
`908c6884f386bf2f78466f2d0be3b7984a594d07228381774038a564f5dd9cd8`.

Read-only host inspection confirmed `/opt/hisar/vault/Forge/workspaces` and
Docker execution. The running Igor image ID was
`sha256:12ebf61d91581d600d3086d67b4a9d927ee9fd38e30f86a988f0670c02b48227`;
its revision label was absent. Its deployed DockerCell source hash was
`526bdbef4381348fc6b1ab90a485b41de3a6e51b31111db22b7e660f50be4184`,
and TurnRegistry source hash was
`9f9b4a7ad1f418d11b976c4117675006858426e75b55833156ec13c2ee6d982b`.
The existing `forge-cell-forge-coder-322e3996` still mounted the Optimus desk and
showed only `sleep` in its process snapshot. Read-only workshop inspection found
claim `call_p1gjQITBlhtZJM8HsD0CSo6c`, still running since 15:38 Istanbul time.
Neither the Cell nor the claim was stopped or reconciled. No candidate code was
deployed into Igor; the Linux check loaded it in an isolated test interpreter.

## Remaining before parity and the new process view

- Complete production baseline and reconciliation of the older Optimus claim.
  SSH and isolated Linux Docker checks now work; the older execution was left
  untouched. No host-key workaround, key change or deployment was performed.
- Durable request fingerprints and retry recovery across registry eviction or
  restart. Known IDs currently return HTTP 409 during the in-memory window.
- Admission before normal user history writes and durable user-request retries.
  Trigger/report admission now precedes seed writes, and background worker
  reports have an outbox; admission does not span multiple Igor processes.
- Complete the client desk indicator/selection validation and test actual
  Heartbreaker restart behavior. Backend bindings and retained inputs are now
  implemented; automatic isolated job checkouts/integration remain open.
- Resumable child conversations and explicit follow-up.
  Durable execution records/cursors, per-worker interrupt and live Forge
  steering now exist; their saved history does not establish executor liveness.
  Parent Stop does not yet collect
  previously backgrounded workers; their ownership/control contract is still
  required before claiming Codex-equivalent Stop behavior.
- Supervised execution independent of Igor, process fencing/reconciliation,
  independent acceptance verification and aggregate run cost enforcement.
- A complete read-only capability audit for shared context/DB access. This
  increment applies the registry's declared safety, not a blanket assertion
  that every read-only integration is concurrency-safe.
- Heartbreaker handling of transport EOF before a terminal event, server
  request acknowledgement, and the planned durable coding-process view.
  No Heartbreaker source was changed by this implementation increment.

Keep these gaps visible when evaluating Optimus. Passing lifecycle regressions
does not prove coding competence or restart-safe autonomous operation; the
multi-Autobot project benchmark from the plan remains required.

The desk/input regressions also cover service reconstruction, separate chats,
private-project inheritance and concurrent selection, cross-owner/agent refusal,
original reuse without re-upload, corrupted input rejection, failed input-record
commit, HTTP chat restoration, worker completion restoration, coordinator-path
rejection and claim-covered input staging/cleanup. The final broader result is
recorded in
`C:/Users/ahmet/AppData/Local/CodexStudies/OPTIMUS_DURABLE_DESKS_TESTS_20261010.log`.
The full Forge suite and affected Igor suites passed **1,484 checks**, with
**20 platform/tooling skips**, in 90.44 seconds. This includes the original
lifecycle regressions as well as the new desk/input regressions; it does not
run a live model competence benchmark or exercise the Heartbreaker UI.
Linux artifact verification passed **20 assertions**, including concurrent puts,
integrity failures and directory/prefix/file symlink refusal; its temporary data
was removed. The candidate artifact module SHA-256 was
`156fe24176fe4a6f5b6da16b263e5ae9edcac4d2b18300ff3ce341cd95609728`.
The result is in
`C:/Users/ahmet/AppData/Local/CodexStudies/OPTIMUS_LINUX_ARTIFACT_TESTS_20261010.log`.

Worker-control verification used real WAL/FK SQLite, the actual Legion-to-Forge
adapter, Warden and the development subprocess Cell with scripted provider
responses. It covers service reconstruction/replay, cursor/tail races, ownership,
message retries/conflicts and legal transcript placement, targeted interruption,
provider abort, repeated parent cancellation during settlement, failed admission
and journal writes, terminal publication ordering, preserved uncertain claims,
chat deletion/export, scoped oversized events, uncached live inspection and
worker tool enforcement/ordered mutations.

The full Forge suite plus affected Igor suites passed **1,588 tests**, with
**20 platform/tooling skips**, in 85.36 seconds. A subsequent final focused run
passed all **24 worker-control tests**, including the saved-interrupt/live-signal
retry gap and real hard-cancellation claim retention. The two results overlap;
they are not additive test counts. Logs:

- `C:/Users/ahmet/AppData/Local/CodexStudies/OPTIMUS_WORKER_CONTROLS_TESTS_20261010.log`
- `C:/Users/ahmet/AppData/Local/CodexStudies/OPTIMUS_WORKER_CONTROLS_FINAL_TESTS_20261010.log`

Eighteen changed Python files also parsed under Python 3.12 syntax and tracked
patch whitespace checks passed. This increment did not deploy code, modify the
live workshop/older Cell, run a live model competence benchmark, or migrate the
Heartbreaker UI. This worker-control verification preceded the completion
recovery increment below; production supervision and follow-up remain required
before claiming full run parity.

Completion-recovery verification used the same real WAL/FK SQLite and actual
SessionManager/TurnRegistry, plus the Legion control wrapper. Provider replies,
Telegram and owner-memory post-turn tasks were scripted. The **29 completion
checks** cover atomic terminal/outbox failure, busy chats and capacity before
seed writes, two/four/eight workers reporting serially, callback failure,
ready/running crash windows, exact saved-response reconciliation, delivery
failure, atomic desktop fallback/lost acknowledgement, repeated cancellation
during receipt cleanup, invalid origins, retained desk/project/original inputs,
raw export/owner scope, fairness and the authenticated n8n task-drain route.

The final complete Forge suite plus affected Igor suites passed **1,620 tests**,
with **20 platform/tooling skips**, in 141.15 seconds. The final result is in
`C:/Users/ahmet/AppData/Local/CodexStudies/OPTIMUS_COMPLETION_FULL_FINAL_TESTS_20261010.log`.
The preceding focused run passed **119 checks**; these counts overlap. Fourteen
affected Python files parsed with Python 3.12 syntax, and patch whitespace checks
passed. All test scratch/cache data stayed outside the checkout. No production
deployment, live provider competence benchmark or Heartbreaker UI migration was
performed in this completion-recovery increment.
