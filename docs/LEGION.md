# Legion, Autobots and Decepticons

The [27 October IGOR/FORGE extension](IGOR_FORGE.md) adds dispatch during the
supervisor reply, supervisor reasoning inboxes, cross-turn project dependencies,
bounded acceptance turns and owner-approved releases. It supersedes the older
queue-after-contribution description below while retaining these worker contracts.

Native Coordinator and Forge integration, 25 October 2026. Original clients keep
their layouts; the original Legion/Workspace/remote-agent engines are not wired
back into the application. This implements local contracts, not a live-provider
or Docker production certification.

## Roles and authority

| Worker | Native runtime | Authority |
| --- | --- | --- |
| Scout, researcher, analyst, judge, general | Anonymous read-only Legion child | Parent's allowed read tools, pinned MCP grants and shared budget |
| Archivist | Anonymous read-only Legion child | Iacon packet and `memory_lookup` only; parent memory allowance is shared |
| Autobot | Forge coder | Bound project artifact, approved engineering scope; Optimus is the team lead |
| Decepticon | Forge pentester | Authorized local security assessment, isolated project artifact; Scourge is the team lead |
| Forge reviewer | Forge reviewer | Read-only review of the exact predecessor artifact |

These are temporary assignments, not additional enduring personas. The actual
delegating supervisor is recorded independently of the team lead. Speda retains
its approved ability to assign either team; that does not invent a contribution
from Optimus or Scourge. Optimus cannot assign Decepticons, Scourge cannot assign
Autobots, and workers cannot spawn more workers or transfer authority.

`profiles/workers.py` owns declarative instructions, role aliases, iteration limits
and tool scope. `forge_coder`, `forge_pentester`, `reviewer` and `general-purpose`
remain aliases. The orchestrator assembles read-worker prompts. Forge assembles its
execution packet from the selected role. The native policy owns model/thinking
choices; descriptive role effort hints do not translate or override provider-native
thinking controls.

## Assignment and lifecycle

`Task` accepts a `worker_id` (or historical `subagent_type`), description and prompt.
Read-only work waits and returns bounded findings by default. `background: true`
or the historical `run_in_background: true` returns a durable execution ID and
ticket. Conflicting aliases are refused. Admission is not a completed result.

Forge roles route to `Projects.delegate` with acceptance criteria and optional
earlier-task dependencies. They always enter the project's durable queue; the
calling contribution must finish before that queue drains. There is no second
Legion writer for project task status or claims. Workshop remains checkpoint/claim
authority and canonical source integration remains a separate operation.

Legion read assignments persist before dispatch, then transition from admitted to
running and a terminal result. A stop before dispatch cannot launch a provider.
Scope validation binds owner, room, root run, active supervisor step and role.
Root limits allow eight combined read/Forge assignments, with at most three active
Legion children; actual paid concurrency is the existing shared root/owner gate.
Known in-flight capacity can be awaited within the original deadline before
reservation. Unknown or stale-executor receipts hold capacity without another send.

A reply can finish while detached work continues under its original run lease,
deadline and budgets. New owner messages can start another room round. They do not
enter an earlier child's snapshot unless explicitly steered to that child. Closing
the transport does not cancel work. Stop-reply, stop-task and stop-run retain
separate scopes. Repeated task-stop requests cannot repeatedly cancel cleanup.

All read and Forge calls use the existing coordination/global paid boundaries.
Per-worker model and native thinking preferences live in `PolicyDocument` under
its revision/CAS authority and are pinned in the admitted run snapshot. The
original `/agents/legion-models` panel now uses this policy. No extra planner,
personality rewrite, provider polling loop or unmetered helper model was added.

## Inspect, collect, steer and reconnect

The original authenticated interfaces are mounted:

- `GET /legion/executions` and `GET /legion/executions/{execution_id}`.
- `GET /legion/executions/{execution_id}/events?after=<sequence>`.
- `GET /legion/executions/{execution_id}/attach?after=<sequence>`.
- `POST /legion/executions/{execution_id}/interrupt`.
- `POST /legion/executions/{execution_id}/messages` with `text` and `message_id`.
- `GET /legion/executions/{execution_id}/event-artifacts/{digest}`.
- `GET /legion/active` and `GET /legion/attach/{ticket}`.

Owner API reads are owner-scoped. Supervisor tools (`legion_inspect`,
`legion_status`, `legion_control`) are limited to that supervisor's workers in the
same conversation. The status tool resolves native tickets; inspect supports
bounded cursor reads and an optional short wait. Control accepts stop/interrupt
or a steering message and uses normal pre-effect tool receipts. There is no
model-facing execution-resurrection capability.

Steering is idempotent by message ID. Queued means accepted, delivered means
included at a legal model boundary, and neither proves compliance. Supervisor
steering remains server-attributed agent input within the existing assignment;
it does not become new owner authorization or personal memory. Forge uses its
existing TaskInbox, while read workers consume their own bounded inbox between
complete protocol exchanges.

Read activity uses Coordinator `RoomEvent` records. Forge activity is a projection
of existing `ProjectTask`/`RoomEvent` records. Stable execution/ticket identity,
monotonic room sequence and event ID let both desktop clients and Android replay
without duplicate text or tool calls. Tool results retain their original call IDs.
Child findings remain in child cards, distinct from the supervisor's answer.
History reload reconstructs those cards as a read view; original messages stay
immutable. Recent completed work remains inspectable in the comms surfaces.

Large read-worker event previews disclose truncation. Bounded original event
artifacts are stored separately and require owner/execution access checks. Saved
result text is bounded, and inspection includes uncertain tool-receipt metadata.
Lost executors are reported as unknown/reconciling, not completed. Recovery never
restarts a read worker or Forge Cell; unsafe parent resume is refused. Verified
completed read results can be replayed through existing safe receipt boundaries.
Native tickets address native executions; archived legacy job execution is not
reconstructed from old prose or imported as a live process.

## Project continuity and reference patterns

Original `cwd` and `workshop_project_id` chat fields now resolve to a native stable
project. The explicit `/Forge/workspaces/...` vault prefix maps only through the
configured workspace root. Host-native paths must remain under that root;
execution/repository metadata cannot be selected. A chat binds once and refuses a
different project rather than retargeting old work. Disjoint workspace/state roots
and `PROJECT_EXECUTION_ENABLED` still apply. Chat-folder/knowledge projects,
transient ambient context and voice presentation remain separate integration work.

The interaction patterns were checked against current primary documentation:
[Open WebUI sub-agents](https://docs.openwebui.com/features/chat-conversations/chat-features/subagents/)
distinguish foreground return and detached work, while
[LibreChat subagents](https://www.librechat.ai/docs/features/subagents) describe
isolated child context and inspectable delegation. LibreChat's current development
documentation deprecates legacy Agent Chains in favor of handoffs/subagents;
that is not treated as a guarantee for every stable release.
[LibreChat resumable streams](https://www.librechat.ai/docs/features/resumable_streams)
and [Open WebUI task visibility](https://docs.openwebui.com/features/chat-conversations/chat-features/task-management/)
inform the presentation. No framework runtime was imported. Comparison and merged
answers are separate interactions and are not presented as independent verification.

Local tests exercise real native loops with fixture providers, durable SQLite and
disposable PostgreSQL records, replay/steering/cancellation, exact-artifact project
roles and client folds/builds. Real Hisar Docker Cells, restart/teardown, integration
and rollback, live-provider quality and full visual end-to-end acceptance remain
release gates. No production deployment, source cutover or paid evaluation was
performed for this slice. n8n remains the only scheduler.
