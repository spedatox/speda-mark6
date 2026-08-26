# Octavius Protocol — Igor backs up his own brain

**Owner agent:** Orion · **Tool:** `octavius_protocol` · **Endpoints:** `/admin/octavius*` · **Workflow:** [`octavius_backup.json`](../packages/igor/scripts/n8n/octavius_backup.json)

| Piece | Where |
|---|---|
| snapshot, verification, Drive, retention | [`app/services/octavius.py`](../packages/igor/app/services/octavius.py) |
| the tool and its gate | [`app/skills/octavius.py`](../packages/igor/app/skills/octavius.py) |
| the HTTP surface | [`app/routers/octavius.py`](../packages/igor/app/routers/octavius.py) |
| the shared Google token | [`app/services/google_auth.py`](../packages/igor/app/services/google_auth.py) |
| what Orion is told to do | `app/prompts/agents/orion/06_octavius.md` |
| the invariants, pinned | [`tests/test_octavius.py`](../packages/igor/tests/test_octavius.py) |

Everything Speda is lives in one SQLite file: every message, every session, the
memory tree, the observation record, both recall indexes, the background queue.
Lose it and the roster still boots — with no idea who the owner is.

---

## Why not `cp speda.db`

Because the database runs in **WAL mode** (`app/database.py` sets it deliberately,
so background writers do not collide with the chat loop), and in WAL mode the
`.db` file is not the database. Recent commits live in `speda.db-wal` until a
checkpoint moves them.

- Copy all three files while the process is writing → a torn set.
- Copy only `speda.db` → a database silently missing however many hours were
  still in the journal.

**`DEPLOY.md` used to recommend exactly the second one.** It now points here.

The snapshot is `VACUUM INTO`, run through Igor's own engine: SQLite builds a
complete, consistent, compacted copy at a single point in time, no WAL sidecars
to carry, no writers blocked. It is the one copy that is both online and correct.

---

## A copy nobody verified is not a backup

The failure worth engineering against is not "the backup did not run" — it is
"the backup ran, said it succeeded, and is not restorable", because that one
stops anyone from looking. So every step is checked before the next trusts it:

| Step | Check |
|---|---|
| snapshot | `PRAGMA integrity_check` on the **snapshot**, not the live file |
| compress | the gzip is hashed, and the hash rides in the file's Drive metadata |
| upload | Drive is asked how big the file it stored is; a mismatch fails the run |
| restore | the download is re-hashed against that metadata, then integrity-checked again |

**`integrity` failing is not a backup problem.** It is a statement about the
**live database**, and both the tool and the workflow report it in those words
rather than as a flaky run.

**Whether a backup exists is read from Drive, never from a local note.** A note
saying "last backup succeeded" survives precisely the failures it exists to catch
— a revoked token, a trashed folder, someone tidying up.

---

## Schedule

Import [`octavius_backup.json`](../packages/igor/scripts/n8n/octavius_backup.json)
and activate it. Nightly at 04:17.

Its shape is the usual cost boundary **inverted**. Every other watcher polls
something cheap and spends a turn when the answer is yes; here the cheap half *is*
the work — `POST /admin/octavius/backup` snapshots, verifies, uploads and prunes
for zero tokens and no agent. The Gate fires on **failure**, edge-triggered, so a
revoked Google token produces one push rather than one a night.

---

## What is deliberately not in it

`runtime_state.json` and the managed `.env` — the OAuth refresh tokens, the portal
credentials, every API key. **They are never uploaded and there is no flag to make
them be.** Putting the keys to every account the owner has into a file inside one
of those accounts turns a single Google compromise into all of them.

Every archive carries a manifest naming those files and their paths, so a restore
knows what it still has to carry by hand.

**This matters most on a MOVE.** Restoring the database on a new host gives back
everything Speda knows and nothing Speda can log into: those two files at
`/opt/speda/` travel by `scp`, or the integrations get reconnected on the far
side. Orion is instructed to say so every time a move comes up.

---

## Restoring

```
octavius_protocol(action="list")     # what Drive holds, with ids
octavius_protocol(action="fetch")    # newest, or fetch a specific id
```

`fetch` downloads, verifies the hash, gunzips, integrity-checks, and stages the
file at `/opt/speda/restore/`. It touches nothing live and it needs the owner in
the conversation.

**Then it stops.** Igor is the process holding that database file open, and
replacing it underneath a running SQLAlchemy engine is not risky, it is corrupt.
The swap needs the app stopped, which means it cannot be the app that does it —
so `fetch` returns the commands instead:

```bash
docker compose stop app
mv /opt/speda/speda.db /opt/speda/speda.db.before-restore
rm -f /opt/speda/speda.db-wal /opt/speda/speda.db-shm
mv /opt/speda/restore/<name>.db /opt/speda/speda.db
docker compose start app
```

**Step 3 is not tidying.** A leftover `-wal` from the old database is read as the
*new* one's journal, and a journal from a different database is corruption that
passes every check.

---

## Settings

| Setting | Default | |
|---|---|---|
| `OCTAVIUS_PROTOCOL_ENABLED` | off | needs Google connected; needs **no** ssh bridge |
| `OCTAVIUS_DRIVE_FOLDER` | `Speda Mark VI — Backups` | created on first use |
| `OCTAVIUS_KEEP` | 14 | older ones are **trashed**, not deleted — Drive holds trash 30 days, so a retention number typed with one digit too few stays undoable |
| `OCTAVIUS_STALE_HOURS` | 48 | set from the cron's period, not from taste |

The Drive scope the Connections flow already requests is the full
`https://www.googleapis.com/auth/drive`, so no re-consent is needed.

---

## Safety

- The tool is `restricted_to={"orion", "optimus"}`.
- **`backup` is NOT owner-gated** — it only ever creates, cannot lose anything,
  and the moment a backup matters most is the moment nobody is around to
  authorise one. A watchdog seeing the Lifeboat Protocol at `critical` should be
  able to take one.
- **`fetch` IS owner-gated.** It still touches nothing live, but it is the first
  step of replacing the owner's entire history.
- The swap is in neither category, because nothing here performs it.
- Scratch copies live in the container's own `/tmp`, never in the bind-mounted
  data dir — a snapshot is the size of the database, and writing it next to the
  database means a backup can fill the very disk it exists to protect. They are
  removed in a `finally`, on success and on failure alike.
