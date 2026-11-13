# Orion host maintenance

Orion administers the live Mark VI host. In owner-started conversations, System
Ops runs Bash commands and file operations as root, including during Orion's
collaboration turns. Commands start in `/root` and run
until they finish or reach an explicitly requested timeout. A running
command suspends its tool turn. Its durable result resumes the same tool exchange
without model polling. Use `operation_status` for explicit inspection. Results contain up to
8,000 bytes per stream. For longer output, redirect it to a host file and inspect
the relevant sections.

Other personas use `collaborate` to request server evidence from Orion in an
owner-started conversation. Orion can check service health, incidents, recent
service logs, load, storage, listening sockets, failed systemd units, the host
timezone and recent successful SSH root password logins during these exchanges.
An app restart requested through `restart_service` waits for
the backend admission gate and verifies a different ready instance.

Install `scripts/orion_host_ops.py` as `/usr/local/libexec/speda-orion-host-ops`
and `scripts/orion-host-ops.service` as a systemd system unit. Create
`/opt/speda-operations/orion-host-ops/socket` as root with mode 0700 and mount
that directory at `/run/speda-orion-ops` in the SPEDA backend as read-only.
The broker creates the socket with mode 0600 and accepts root peers.
The production application container uses UID 0.

The broker retains command receipts in its root-owned directory. To revoke
host execution, stop and disable orion-host-ops.service. Keep its receipt and
workspace directories for review. Remove the socket bind mount during the next
normal deployment if retiring the integration.


## Completion delivery

The backend creates the additive operation binding, boundary history, event inbox,
cursor and continuation tables through `init_db`. Existing tool receipt identities
remain canonical. Deploy the updated host script and backend together. The existing
directory bind mount must expose **both** `broker.sock` and `events.sock`; mounting
only the command socket is insufficient. Both sockets require mode 0600 and root
peer credentials. No external callback or additional API credential is used.

Admission commits the request before starting `speda-orion-result-<operation ID>`.
That independent systemd service owns bounded result capture and SQLite receipt /
outbox persistence. Restarting the broker does not kill that service. A result and
its outbox entry commit together. The event socket replays from the backend's last
committed sequence, requires an acknowledgement after each committed envelope,
and sends heartbeats on idle subscriptions. Its separate threaded server cannot
occupy the command server. The backend retries delivery without model requests.
Receipts, worker launch markers, events and acknowledgements are retained; this
change introduces no automatic deletion or unit evidence cleanup.

The coordinator saves the full assistant tool message, batch cursor, earlier
results, call ordinal, iteration counters, tool schemas, artifacts, private
provider state and pinned run authority before dispatch. A pending operation
releases the room and model slots. Later owner turns may run; a continuation
reacquires the room slot under a lock and retains its original root budgets.
A fresh coordinator can recover an expired executor's known external wait.
Uncertain provider outcomes or uncertain host execution remain held for inspection.

Stopping a reply or run suppresses its model continuation. It does not kill an
already authorized host command. Results still persist. No API for cancelling a
host command is introduced. An explicit command timeout remains enforced by its
worker. Lost worker launch/result evidence never authorizes another execution.
An authority revision, provider/schema change, paused budget, expired deadline,
or dismissed participant holds continuation. Existing paid admission gates apply
again before the next provider request. Run inspection exposes operation IDs,
states, deadlines and canonical receipt IDs directly from saved state.

Desktop and Android fold waiting/finished/resumed events and retain the preceding
message. Session feeds project only operation steps marked as public; private
consultations are excluded. Replayed frames do not concatenate output twice.

## Local verification and deployment gate

`tests/test_operation_waits.py` exercises suspension, paired resume, duplicate and
immediate completion, batch cursors, restart recovery, pause, stop, conflict and
invalid event handling through the real coordinator and registry. Host receipt
and event protocol tests are in `tests/test_host_operation_outbox.py`. Desktop
behavior tests are in `tests/compatibility/operation_wait_client.test.ts`; Android
behavior is covered by `RoomParityTest`.

Production deployment is separate. Before rollout, verify actual systemd worker
survival through broker/backend restarts, Unix peer credentials and mount access,
PostgreSQL concurrency, and native client builds in the target environment.
Portable host tests exercise the durable SQLite and replay protocol with fixture
execution; they do not establish Linux systemd survival. Preserve the host receipt
DB and its WAL, backend database, installed script and service as rollback evidence.
Do not downgrade while external waits are active without retaining their results
and resolving their saved boundaries.
