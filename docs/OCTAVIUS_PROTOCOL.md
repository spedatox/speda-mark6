# Octavius Protocol

Octavius backs up Igor's SQLite database to the owner's connected Google Drive.
Enable `OCTAVIUS_PROTOCOL_ENABLED`; n8n remains the only scheduler. Backups use
`VACUUM INTO`, an integrity check, a SHA-256 digest and a verified Drive upload.
Credentials in `runtime_state.json` and `.env` are never included.

## Restore from the apps

In Heartbreaker or SPEDA GO, open **Settings → Protocols → Octavius**. Select a
backup, press **Restore backup**, review the snapshot and confirm. No terminal
commands are needed. Chats and memories return to the selected snapshot; newer
changes remain in the preserved original database, not in the restored history.
Reopen chats after completion to load the restored history.

Automatic restore requires a Docker Compose `app` container, a writable data
directory bind mount, the existing `SYSTEM_OPS_HOST` SSH bridge, and `python3`
(3.8 or later) plus systemd on the host. The service discovers the actual host
mount and exact container ID through Docker inspection; it does not guess paths
or require a copy of the Compose repository on the host. On other deployments,
`POST /admin/octavius/fetch` continues to provide verified staging and manual
instructions.

The restore controller verifies the selected archive's recorded hash and SQLite
integrity, writes a standalone worker into the shared data directory and launches
it with `systemd-run`. This is a single owner-requested job, not a scheduler.
The worker rechecks the staged bytes on the host, stops the exact Igor container,
confirms it is stopped, preserves the old database **with its WAL and SHM**, and
installs the snapshot. It starts Igor again and checks `/health`. A failed start
automatically stops the restored process, quarantines its files, puts the original
database and journals back, and starts Igor again.

## API and durable progress

All endpoints require `X-API-Key`:

- `GET /admin/octavius/backups` lists available snapshots.
- `POST /admin/octavius/restore` accepts an explicit `file_id` and an optional
  `job_id` of 32 lowercase hexadecimal characters. Clients generate the ID before
  submitting so a lost HTTP response cannot be confused with a previous restore.
  Reusing the same ID and backup returns the existing job; it never restores twice.
- `GET /admin/octavius/restore?job_id=...` reads that job's durable status.
  Without an ID it reads the most recent job. Status survives both the restart
  and the database restore because it is stored in files outside the database.

Only one restore can run. Its `restore/jobs/active` directory is an exclusive,
durable lock. An ambiguous SSH dispatch keeps the lock: losing the connection is
not proof the host job did not start. Clients poll status and never resubmit a
restore automatically. `complete` means the restored app became healthy; `failed`
includes the reason and whether rollback completed. `recovery_required` means
automatic recovery failed and further restores are blocked.

## Operator recovery after interruption

Do not deploy/recreate Igor while a restore is active. A host restart, killed
worker, or failed rollback requires reconciliation; the lock never expires.

The bind mount's `restore/jobs/<job_id>/` holds `job.json`, `state.json`, the
worker, and `before-restore/`. Inspect the unit with
`journalctl -u speda-restore-<job_id>` and confirm it is no longer running before
acting. `job.json` identifies the exact container and host database path.

Stop every Igor process using that database and verify they are stopped. Keep
the current database and its journals together in a separate recovery directory.
Move the files present in `before-restore/` back to the database location, keeping
that database's own `-wal` and `-shm` alongside it. If an interruption left some
original files unmoved, reconcile them with the worker's state before replacing
anything. Never combine the restored database with the old journals, or discard
the original journals before checkpointing the original database.

Start Igor and check health and the expected history. After verifying recovery,
update the job's state to `failed` with the recovery outcome and remove the empty
`restore/jobs/active` directory. Preserve the recovery files until the owner has
reviewed them. Successful automatic restores also retain the previous database;
there is no timer that silently removes this recovery copy.
