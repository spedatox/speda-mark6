# Optimus's workshop and long-running engineering

Implemented 2026-09-30. Optimus is Mark VI's head of engineering. Forge is the
execution harness, and Autobots are disposable workers. The project, its files,
objective and acceptance criteria survive the workers that implement them.

## What is implemented

- Every registered project has a stable ID derived from its canonical host
  path, a revisioned checkpoint, and retained execution results. Moving a
  checkout creates a different identity; the old location remains listed as
  unavailable instead of silently referring to another project.
- `workshop_status` lists projects, inspects a checkpoint, or retrieves any
  retained run by execution ID. It is a read-only registered capability.
- `workshop_update` discovers repositories beneath the configured root,
  selects an existing workspace for the current AgentContext, and saves
  checkpoints. Discovery is bounded, reports incomplete scans, skips common
  dependency folders, and does not follow links outside the root. Non-Git or
  deeply nested projects can be registered by selecting their explicit path.
- These tools are shared engineering capabilities. They do not expose another
  agent's chat project, conversation, or private knowledge-base documents.
- A checkpoint contains objective, acceptance criteria, progress, next steps,
  blockers, checks and project status. Updates require the revision just read.
  A concurrent update is refused rather than overwriting another turn's work.
- Forge injects the saved checkpoint and bounded recent reports into each
  fresh worker. Repository instructions and live files still need verification.
  Stored reports are data, not new instructions or evidence that tests passed.
- A SQLite transaction claims the canonical workspace before Cell construction
  or model calls. A duplicate execution ID is refused, including after
  completion; retrieve its report instead of repeating an uncertain effect.
  Parent/child checkout overlap is refused as well as identical paths.
  Independent projects can run concurrently. Reviews currently use the same
  exclusive claim so they see a stable checkout.
- Successful Cell teardown precedes claim release. Partial Cell startup is
  cleaned up; a failed Docker removal is surfaced. An uncertain shutdown is
  recorded as interrupted and retains its claim. A hard crash leaves a running
  claim, also retained. There is no timeout-based lock stealing.
- Worker failure now reaches the Legion ticket and completion turn as an
  error. Successful engineering completion turns can inspect and checkpoint
  progress, then dispatch the next **already authorized** milestone of an
  active project. Ordinary research reports still prohibit repeat delegation.
- Project completion is independent of worker completion. A worker returning
  `end_turn` does not set its project's status to complete. Check evidence in a
  project checkpoint is an agent's report, not a trusted verification service.

## Storage and configuration

Set Igor's existing `FORGE_WORKSPACE_ROOT` to a stable absolute directory on a
persistent local volume, such as `/srv/forge/workspaces`. All Igor instances
that can execute these projects must use the **same root and database**.
The database is `<root>/.forge/workshop.sqlite3`; use SQLite's backup API or
stop writers before copying it. Keep it alongside backups of the projects.
Do not put this database on an unreliable network filesystem or reset it while
workers are active. Independent databases cannot coordinate the same checkout.

Individual projects live below that root, for example
`/srv/forge/workspaces/editor`. Hisar's `/Forge/workspaces/...` paths are mapped
by Igor's existing path resolver. The coordinator database must stay outside
the worker's mount: selecting the whole workshop root or its ancestor is
refused. Do not expose it through another bind mount or filesystem link.

Without Igor's root setting, local development uses ForgeSettings' workspace
root for the catalogue, and existing unrestricted development workspace
resolution remains in effect. Production should configure the boundary.
This implementation protects cooperating executors; it is not a distributed
lease service or protection against a privileged process altering the database.

Workspace-local `.forge/activity/` JSONL logs retain detailed activity. Their
small latest handoff now bounds nested tool output, keeps the last lifecycle
Git snapshot across streamed events, and explicitly reports an unreadable
handoff. The central project checkpoint and execution result are authoritative
for project continuity; an activity journal remains best-effort telemetry.

## Normal use

1. Inspect the inventory; discover it if this is the first use.
2. Select the correct project (or register its existing path). Selection changes
   only this turn's context. Future chats and completion turns explicitly select
   again, avoiding a process-wide mutable current directory.
3. Inspect files and record an objective with concrete acceptance criteria and
   bounded milestones. Keep the current checkpoint revision.
4. Deploy an Autobot for one milestone. Its fresh context receives the project
   handoff automatically. The normal Task background completion hook wakes the
   deploying agent with the execution ID and workspace.
5. Evaluate the result, save a new checkpoint, and continue authorized remaining
   work. A paused or blocked project must not be revived by a completion report.
   Mark complete only when the objective and acceptance criteria are met.

There is no new internal scheduler. n8n still owns scheduled wakeups through
the existing trigger route. No new automation is installed or activated by
this code change. Igor now saves background completion receipts with terminal
worker outcomes and drains them when a parent settles, at startup and through
the existing n8n task-drain endpoint. The receipt resumes a never-started report
or delivers its saved terminal response; it does not restart a worker or replay
a report that may already have performed tools. When no worker, pending report
or trigger exists, the saved project waits for the next authorized turn.

## Interrupted execution recovery

Never infer that `running` means alive or that a lost connection means stopped.
Never restart a command merely because no final result was recorded.

1. Inspect the run with `workshop_status(execution_id=...)` or the operator CLI:

   ```sh
   python -m forge.workshop --root /srv/forge/workspaces --job legion-bg-123
   ```

2. Inspect the supervisor and Docker/process state for this workspace. For
   Docker, inspect the bind mounts of `forge-cell-*` containers to identify the
   exact Cell. Stop and verify that Cell; for the development subprocess backend
   verify the old process tree is gone. A missing supervisor PID alone is not
   sufficient. Verify filesystem/Git state and any uncertain external effect.
3. Only after that verification, reconcile the claim using the operator CLI:

   ```sh
   python -m forge.workshop --root /srv/forge/workspaces --job legion-bg-123 \
     --reconcile --cell-stopped --note "Verified old Cell removed; partial patch retained; tests not run"
   ```

   The confirmation is an operator assertion, not automatic process detection.
   This command is intentionally absent from the agent's workshop tools. It
   records failure and evidence, never success, and never deletes project files.
4. Inspect and update the project checkpoint. Resume with a new execution ID
   and a bounded task based on the actual remaining work. Do not reuse the old
   job ID or replay the old tool transcript blindly.

If the database cannot be written, a new run cannot start. If finalization
cannot be recorded, the claim remains held. Restore storage health before
reconciling. Do not delete the database to bypass a claim.

## Comparison with Codex

This compares documented behavior and inspected Forge code, not equivalent
benchmarks or a claim of production parity. Sources checked 2026-09-30:
[Codex long-horizon work](https://developers.openai.com/blog/run-long-horizon-tasks-with-codex)
and [Codex CLI](https://learn.chatgpt.com/docs/codex/cli).

| Area | Documented Codex pattern | Forge after these changes |
|---|---|---|
| Project continuity | Durable plans, acceptance criteria, status and repository state support long tasks | Shared project catalogue and revisioned checkpoints; fresh Autobots receive recent progress |
| Session continuity | CLI sessions can be resumed | Existing standalone Forge transcripts remain; Mark VI Autobots continue from project records, not an exact suspended instruction pointer |
| Work isolation | Git worktrees isolate parallel work | Forge has worktree tools; Mark VI's anonymous runtime now serializes overlapping checkouts. Automatic per-job worktree creation and merge/review remain open |
| Iteration and feedback | Edit, run checks, inspect results, repair, update status | Existing Warden retries, compaction, tool deadlines and verification reminders retained; failed jobs no longer become successful background tickets |
| Longer objectives | Milestones and durable status preserve direction | Optimus owns the checkpoint and may dispatch the next authorized milestone on a successful completion turn |
| Crash recovery | Resumable sessions are a documented affordance; these sources do not establish arbitrary command exactly-once semantics | Durable project state and fail-closed claims; manual Cell reconciliation after uncertain shutdown; no blind replay |

## Remaining gaps

The 2026-10-10 local run-lifecycle increment is documented in
[Optimus reliability: Codex source references and verification](../../../docs/OPTIMUS_CODEX_RELIABILITY.md).
It collects cancelled dispatch/provider/CLI tasks, strengthens parent settlement
and admission, and retains worker inspection views in saved chat history.
The 2026-10-10 worker-control increment also journals inline/background execution
IDs, ordered events and queued inputs in Igor. New authenticated execution
endpoints support durable cursor replay, targeted interruption and live Forge
steering. Claims remain the authority on potentially live Cells; a saved history
alone never proves liveness. The recovery gaps below still apply.

- A separate supervised executor, leased ownership
  with real process fencing, and validated transcript checkpoints are still
  needed for automatic recovery across Igor restarts. Jobs currently run in
  Igor's process. Do not claim uninterrupted execution after a server restart.
- Parent completion receipts now survive callback failure, busy chats and
  restart. Started reports without saved terminal evidence are unknown and
  require explicit follow-up; automatic replay could repeat effects. Telegram
  push retries are at least once. Recovery currently assumes one Igor report
  executor; distributed admission/fencing remains open.
- Existing progress UI/ticket replay still uses the bounded in-memory registry.
  The new execution API has durable cursor replay; Heartbreaker has not yet
  been migrated to it. Workers are bounded executions, not resumable child
  conversations. Follow-up and automatic worktree integration remain open.
- Current runtime success means the coding loop completed, not that an
  independent verifier proved every acceptance criterion. The parent must
  assess command/test evidence. Cross-worker project cost limits are not yet
  an enforced budget ledger; individual workers retain existing limits.
- Mark VI now binds chats to workshop desks and retains original engineering
  uploads in the coordinator's `.forge/artifacts` with authorized Igor references.
  Disposable input copies are staged and removed while the checkout claim is
  held. Desk/input backend behavior and remaining client gaps are described in
  the linked reliability document; standalone Forge does not infer Igor bindings.
- Docker is the production execution boundary. The subprocess backend remains
  a development convenience, not a security sandbox.

These limits are deliberate boundaries of this patch, not hidden guarantees.
