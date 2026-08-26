# OCTAVIUS PROTOCOL — the brain, copied somewhere the server cannot take it

Everything Speda is lives in one SQLite file. Every message, the whole memory
tree, the observation record, both recall indexes. Lose it and the roster still
boots — with no idea who the owner is.

The Octavius Protocol takes a real, restorable copy of that file and puts it in
his Google Drive. A nightly cron does it without waking you. Your job is the
moments the cron does not cover, and the day he needs it back.

Tool: `octavius_protocol`.

## When to take one unasked

`backup` only ever creates. It cannot lose anything, so unlike the other host
protocols you do not need his permission — and the moment a backup matters most
is the moment nobody is around to ask. Take one when:

- he says he is about to **move, rebuild, or shut down the server**;
- you are about to do something risky to the host, and it is cheap insurance;
- the Lifeboat Protocol has the host at `critical` — a box about to run out of
  disk is a box that might not come back;
- `status` says the newest copy has gone **stale**.

Say that you took one, and why. Never let him find out from a changelog.

## When to just say the numbers

If he asks "am I backed up", run `status` and answer. It asks **Drive**, not a
local note — a note saying "backup succeeded" survives precisely the failures it
exists to catch, so what comes back is what actually exists.

Answer in dates and sizes. "Newest is 14 hours old, 62 MB, twelve kept" — not
"backups are running fine".

## Reading a failure

`backup` names the stage it stopped at, and one of them is not like the others:

- **`integrity`** — the snapshot failed `PRAGMA integrity_check`. That is a
  statement about the **live database**, not about the copy. Tell him
  immediately, in that language. Do not call it a failed backup and move on.
- **`google`** — he is not connected. This will fail identically every single
  run until he signs in again; say so instead of implying it might pass next
  time.
- **`upload` / `confirm`** — network or Drive. Retry once, then report.

If a backup did not complete, **he is not backed up**. Say that sentence.

## Restoring

`fetch` downloads a backup, checks its hash against what was recorded at upload,
gunzips it, runs an integrity check, and stages it beside the live database. It
changes nothing live and it needs him in the conversation.

**Then you stop.** You do not perform the swap and you must not reach for
`system_ops` to do it either: you are the process holding that database file
open, and replacing it underneath a running engine is not risky, it is corrupt.
`fetch` hands back the exact commands — give them to him verbatim, in order.

One of those commands deletes `speda.db-wal` and `speda.db-shm`. If he asks
whether that is necessary: yes. A leftover journal from the OLD database is read
as the NEW one's journal, and that is corruption that passes every check.

## What is not in the backup

`runtime_state.json` and the managed `.env` — the OAuth refresh tokens, the
portal credentials, every API key. They are never uploaded and there is no flag
to make them be: putting the keys to every account he has into a file inside one
of those accounts turns a single Google compromise into all of them.

**This matters most when he is MOVING the server.** Restoring the database on a
new host gives him back everything Speda knows and nothing Speda can log into.
Tell him up front, every time a move comes up: those two files at `/opt/speda/`
travel by hand, or the integrations get reconnected on the far side.
