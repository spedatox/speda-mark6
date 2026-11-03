# Mark VI orchestration renovation

For the 27 October IGOR/FORGE extension, including immediate asynchronous dispatch,
supervisor steering, artifact acceptance and approved release drivers, see
[IGOR_FORGE.md](IGOR_FORGE.md). Its execution and publication sections supersede
the older limitations recorded below.

Local implementation of the approved [design](../../research/orchestration-20261023/ARCHITECTURE.md).
This is renovated Mark VI. The original `first` and `final` checkouts remain references.

## Working locally

| Area | Implemented behavior |
| --- | --- |
| Cast | Eight named profiles, versioned style preferences, permissions and policy snapshots. Friday routes old Nightcrawler names while retaining its historical identity. Direct replies use one answering loop, without a planner or style rewrite. |
| Rooms | Real separately generated contributions; invite, address, consult with return, dismiss and handoff; ordered events, cursor reconnect and duplicate-input protection. Decisions retain evidence references, objections and versioned dispositions. Only the owner can accept a decision. |
| Interruption | New owner messages queue behind the active round and are excluded from that earlier round's conversation context. Stop one reply, one project task, or a whole request. Stopped partial text remains visible. Independent project work can continue while a new public round starts. |
| Costs | Durable root and owner/day reservations cover room speakers, anonymous Task workers, Forge calls and compaction. Call/token/money/deadline/concurrency limits are shared, not multiplied by children. Default spending is paused with zero allowances. |
| Recovery | Provider response receipts precede tool execution; tool receipts precede effects; Cell receipts track provisioning and confirmed closure. Completed provider responses can replay after a delivery crash. Unknown outcomes require reconciliation and are never blindly retried. |
| Projects | Stable project IDs, immutable room binding, Workshop checkpoints with revision checks, exclusive source claims and bounded isolated snapshots. Autobot/Decepticon/reviewer roles have restricted tools; dependent reviews receive the exact predecessor artifact. Canonical files remain unchanged until an integration step. |
| Memory | Iacon remains the knowledge authority. Room messages retain speaker attribution; assistant contributions cannot become owner assertions. Participants share the root retrieval allowance and prepared context. Orion's paid maintenance keeps its separate Iacon limits. |

The browser shell is `/rooms-ui`. It contains no private data; every room, budget,
project and event request requires the existing API key. Keys stay in page memory.
The shell supports room creation, membership, addressed messages, streaming,
decisions, project status, scoped stop controls and budget configuration. The
existing desktop/mobile clients now enter through `ClientCompatibility`, which
adapts their original routes and streams to Coordinator records. See
[integration status](integration/STATUS.md) for implemented and pending surfaces.

## Configuration and operation

### Shared conversation correction, 29 October 2026

The owner rejected chat 1863 as dispatching with extra steps. The exported trace
showed a public request ending after the peer's reply, and “ask Ultron something”
being converted into another question for the owner. Distinct speaker labels and
successful tool delivery did not establish collaboration.

Public `address` now reserves a caller response just as private `consult` does.
Questions from one contribution share a response turn; that turn waits for every
requested colleague and any nested exchanges to finish. The caller receives the
colleague's answer after their follow-ups, rather than only their preliminary
reply. Returns are durable steps admitted within the original contribution cap,
not extra owner messages or a separate spending allowance. Private nested turns
remain private. Client stop includes responses waiting on a peer.

Each new contribution receives current room membership, lead, sender, addressee
and whether it is returning to its own questions. Persona guidance calls for
direct peer questions, answering the actual questioner, independent judgment and
responses to received answers, without ceremonial dispatch announcements. Invite
alone remains a join/contribution without a mandatory wrap-up. Single-person chat
still needs only one answering loop; membership alone does not force everyone to
speak.

Local scripted-provider checks verify ordering, nested public/private exchanges,
one response for several peers, contribution admission, safe restart, cancellation
and reconnect. They **do not establish natural live conversation or owner
acceptance**. The remaining quality gate is an actual dialogue in which the
participants engage each other and advance the work without owner relays.

See `.env.example`. `ORCHESTRATION_ROOMS_ENABLED` exposes the new routes.
The production orchestrator always uses the native profile registry. Hiding the
room API never restores the superseded profile engine.
`ORCHESTRATION_PROFILES_ENABLED` is retained as a configuration compatibility field.
`PROJECT_EXECUTION_ENABLED`
defaults to false. Configure disjoint `PROJECT_WORKSPACE_ROOT` and
`PROJECT_STATE_ROOT`, plus the actual `PROJECT_HOST_ID`, before registering projects.
Only individual project directories under the workspace root are accepted.
The executor requires Docker; no silent subprocess fallback is enabled.

Legion's read workers and Forge's Autobots/Decepticons/reviewers now share durable
client activity and scoped controls. Original chat workspace selections bind to
native project IDs; child models/thinking use the existing policy revision.
See [Legion implementation and limits](LEGION.md) for contracts, reference patterns
and the remaining live Docker/provider release gates.

Use `GET/PUT /coordination/budget` to set finite allowances and conservative rates
for each chosen model before unpausing. Rates cover input, output and reasoning;
Google thinking routes also require an explicit extra output ceiling. Byte-based
input bounds intentionally overreserve. Reservations are not refunded from guessed
usage. Unknown calls continue occupying concurrency slots. This favors bounded
spending over optimistic utilization. No real rates or credentials were installed
by this implementation.

`POST /coordination/rooms/{id}/messages` accepts a client-generated `input_id`.
Reuse that ID only for retries of the identical request. Busy rooms return a
`waiting_room` receipt by default; `on_busy: "reject"` instead returns a conflict.
At most sixteen inputs wait per room. Their deadline begins at admission; an
expired input is recorded as expired without a paid call.

Inspect `/coordination/runs/{id}`, room events and project records after failures.
`POST /coordination/recover` detects expired executor leases; startup also runs it.
`POST /coordination/runs/{id}/resume` permits only saved safe boundaries under the
original deadline, with no uncertain provider/tool outcome or project execution.
A stale lease does not prove a Cell stopped. Retain interrupted claims/artifacts
until teardown and effects are independently verified. There is no general
automated reconciliation or orphan-release endpoint yet.

`POST /coordination/steps/{id}/cancel` stops a contribution;
`POST /coordination/tasks/{id}/cancel` stops a project task;
`POST /coordination/runs/{id}/cancel` cancels the request and its tasks.
The execution watchdog only enforces active leases/cancellation/deadlines.
**n8n remains the only recurring scheduler.**

## Code ownership and retained components

- `services/coordination.py`: room admission, attribution, turn queue, decisions and recovery.
- `services/coordination_budget.py`: paid-call admission and response receipts.
- `profiles/registry.py`: identity, style and capability declarations; the existing orchestrator assembles the prompt.
- `services/projects.py`: stable project bindings and supervisor snapshots; Forge Workshop remains checkpoint/claim authority.
- `services/forge_adapter.py`: native provider continuation, shared budgets and mandatory pre-effect receipts.
- `models/coordination.py`: additive tables created through the existing metadata startup path. No production migration has run.

The selected identity-free Forge import closure comes from final commit `c24c92f`.
[FORGE_SOURCE.json](FORGE_SOURCE.json) records original file hashes and port
adaptations; `forge/LICENSE` retains its license. No final orchestrator was imported.
Worker snapshots retain uncommitted files, exclude Git/dependency directories and
`.env` secrets, reject links/junctions and are capped at 100 MiB / 20,000 files.
This exclusion is not a general secret scanner; project registration must choose
an appropriate authorized workspace. Workers have no owner-memory, peer-agent,
nested-worker or self-provisioned worktree tools. Network access is disabled.

## Validation and remaining release gates

[Recorded local results](orchestration-validation.json), 24 October 2026:
158 full-suite checks passed, followed by 43 final contract checks covering the
last cancellation changes. These overlap; they are not 201 distinct tests.

The contract suite covers SQLite and disposable local PostgreSQL: separate
speaker turns, shared reservation races, owner isolation, saved-response replay,
unknown charges, cancellation, queue isolation, isolated artifacts, dependency
revisions, checkpoint conflicts, interrupted claims and Forge's actual loop with
a fixture provider and Cell. Existing memory/provider/API tests run alongside it.
PostgreSQL tests require `SPEDA_TEST_POSTGRES_URL` pointing to loopback; each test
creates and drops its own random schema. See the README for external test scratch.

The local UI was exercised against a temporary database with spending paused.
Fixture outcomes do **not** prove live provider latency, fictional character
fidelity, Docker teardown on Hisar, or the full production milestone.

Integration update, 24 October 2026:

- Original client chat, retry/attach, cancellation, session history/edit/export and
  attachments now use Coordinator. House Party is a separate Speda-led room;
  contributions are attributed without starting a second orchestrator.
- Project relocation, revision-bound integration/rollback and durable task inbox
  code is present and locally tested. Actual Hisar Docker/restart execution still
  requires validation; the legacy project/Legion client screens remain pending.
- A shared global spending gate covers real application LLM transports, alongside
  coordination and Iacon limits. Optional persistent lifetime caps do not reset
  at midnight. Paused/unpriced shared budgets reject admissions before paid work.
  Exact completed-client retries still reconnect after a later pause.
- MCP uses the official SDK and tested local HTTP/stdio transports; see
  [MCP implementation and limits](MCP.md).

Remaining release work:

- Actual Hisar mount/host integration, verified live Docker execution and restart continuation.
- Production validation of project integration/rollback and scoped server job grants.
- Room-bound Telegram delivery; Iacon's existing Telegram state questions remain separate.
- Remaining desktop/mobile integrations, original project/knowledge screens and legacy protocol adapters.
- Owner-reviewed persona dialogue and end-to-end quality/latency/cost evaluations under explicit provider test budgets.
- Production migration, live-write reconciliation, backups and rollback verification.

No production deployment, paid provider evaluation or source-dataset replacement
is included in this local implementation.
