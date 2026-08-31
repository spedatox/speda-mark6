# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
OCTAVIUS PROTOCOL — Igor copies his own brain somewhere the server cannot take
it with him.

Everything Speda is lives in one SQLite file: every message, every session, the
memory tree, the observation record, both recall indexes, the background queue.
Lose it and the roster still boots — with no idea who the owner is. This module
takes a real, restorable copy of that file and puts it in the owner's Google
Drive, so a dying host or a move to a new one costs a download instead of a
history.

WHY NOT `cp speda.db`
---------------------
Because the database is in **WAL mode** (app/database.py sets it deliberately, so
background writers do not collide with the chat loop), and in WAL mode the `.db`
file is not the database. Recent commits live in `speda.db-wal` until a
checkpoint moves them. Copying the three files while the process is writing gives
you a torn set; copying only `speda.db` gives you a database that is silently
missing however many hours of the owner's life were still in the WAL.
DEPLOY.md's `scp .../speda.db` has this bug.

So the snapshot is `VACUUM INTO`, run through Igor's OWN engine: SQLite builds a
complete, consistent, compacted copy at a single point in time, with no WAL
sidecars to carry and no writers blocked. It is the one operation that is both
correct and online.

A COPY NOBODY VERIFIED IS NOT A BACKUP
--------------------------------------
Every step is checked before the next one trusts it:

  * `PRAGMA integrity_check` runs on the SNAPSHOT — not on the live database —
    because the question is whether the thing being uploaded is restorable;
  * the gzip is hashed, and the hash rides in the file's Drive metadata, so a
    later `fetch` can prove the bytes that came back are the bytes that went up;
  * after upload, Drive is asked how big the file it stored is, and a mismatch
    fails the run. A backup that quietly uploaded zero bytes is worse than no
    backup, because it stops anyone from looking for one.

**Whether a backup exists is read from Drive, never from a note we wrote saying
we made one.** That distinction is the whole point of `status()`: a local record
of success survives exactly the failure it is meant to detect.

WHAT IS DELIBERATELY NOT IN IT
------------------------------
`runtime_state.json` and the managed `.env` hold the OAuth refresh tokens, the
portal passwords and every API key. They are NOT uploaded, and there is no flag
to make them be. Putting the credentials to every account the owner has into a
file in one of those accounts is a way to turn a single Google compromise into
total compromise, and neither is worth avoiding one afternoon of reconnecting
integrations by hand.

Instead every backup carries a manifest naming those files and where they live,
so a restore knows what it still has to fetch the hard way. See `MANIFEST`.

RESTORING IS HALF A JOB ON PURPOSE
----------------------------------
`fetch` downloads a backup, verifies it end to end, and stages it beside the live
database — then stops and prints the swap. It does not perform the swap, because
Igor is the process holding that file open: replacing a database under a running
SQLAlchemy engine is not a risky operation, it is a corrupt one. The swap needs
the app stopped, which means it cannot be the app that does it.

The stale `-wal` / `-shm` sidecars in that swap are not decoration. Left beside a
restored file they are read as that database's journal, and a journal from a
different database is corruption with a clean bill of health.

Nothing here schedules anything (CLAUDE.md). n8n calls the endpoint.
"""

import asyncio
import gzip
import hashlib
import logging
import shutil
import sqlite3
from pathlib import Path

import httpx

from app.config import _DATA_DIR, settings
from app.core.clock import utc_now
from app.services.google_auth import access_token

logger = logging.getLogger(__name__)

_DRIVE = "https://www.googleapis.com/drive/v3"
_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
_FOLDER_MIME = "application/vnd.google-apps.folder"

# Drive requires every resumable chunk except the last to be a multiple of
# 256 KiB. 8 MiB keeps the request count sane on a large database without
# holding much in memory.
_CHUNK = 8 * 1024 * 1024

# Scratch lives in the container's own filesystem, NOT in the bind-mounted data
# dir: the snapshot is the same size as the database, and writing it next to the
# database means a backup can fill the very disk it exists to protect.
_WORK = Path("/tmp/octavius")

# Where `fetch` stages a restored file. This one IS the data dir on purpose — it
# is bind-mounted, so what lands here is visible to the owner on the host at the
# exact path the swap instructions name.
_RESTORE_DIR = _DATA_DIR / "restore"

MANIFEST = """\
This archive is a gzipped SQLite snapshot of Speda Mark VI's database, taken with
VACUUM INTO (consistent, no WAL sidecars needed).

WHAT IS IN IT
  Every session and message, the memory tree, the observation record, both recall
  indexes (vector + FTS5), background jobs, health samples — everything Igor
  knows.

WHAT IS NOT, AND WHY
  /root/.speda/runtime_state.json   OAuth refresh tokens, portal credentials,
                                    Telegram ids, per-agent model pins
  /root/.speda/.env                 the managed override file (API keys)

  These hold the credentials to every account the owner has, including the Google
  account this file is stored in. Uploading them here would make one compromise
  total. Copy them across by hand (scp), or reconnect the integrations on the new
  host — signing in again is an afternoon; losing them all at once is not.

TO RESTORE
  1. Stop the app.       docker compose stop app
  2. Keep the old one.   mv /opt/speda/speda.db /opt/speda/speda.db.before-restore
  3. DELETE THE JOURNAL. rm -f /opt/speda/speda.db-wal /opt/speda/speda.db-shm
     A stale -wal is read as the NEW database's journal. This step is not optional.
  4. Put it in place.    gunzip -c <this file> > /opt/speda/speda.db
  5. Start the app.      docker compose start app
"""


def _now() -> str:
    return utc_now().isoformat(timespec="seconds")


def _mb(size: int) -> float:
    return round(size / 1024 / 1024, 1)


def db_path() -> Path | None:
    """The SQLite file behind `database_url`, or None if this is not SQLite.

    Octavius knows how to snapshot exactly one kind of database. Any other one
    gets an honest refusal rather than a file that is not what it claims.
    """
    url = settings.database_url
    if not url.startswith("sqlite"):
        return None
    # SQLAlchemy's own convention carries the absoluteness: `sqlite:///rel/path`
    # is relative, `sqlite:////abs/path` leaves a leading slash on the tail. So
    # the tail is already the path — prefixing a slash "to be safe" is what turns
    # a Windows dev path into a nonsense one, and a passing test into a lie.
    _, _, tail = url.partition("///")
    return Path(tail) if tail else None


# ── Step 1: a consistent snapshot of a live database ─────────────────────────

async def snapshot(dest: Path) -> dict:
    """`VACUUM INTO` — the only online copy that is also correct. See the docstring.

    AUTOCOMMIT is required, not stylistic: SQLite refuses to VACUUM inside a
    transaction, and SQLAlchemy opens one implicitly.
    """
    from app.database import engine

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    # The path is ours, but the quote-escape is unconditional — a SQL string
    # literal built by concatenation gets escaped even when you are sure.
    literal = str(dest).replace("'", "''")
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.exec_driver_sql(f"VACUUM INTO '{literal}'")
    return {"path": str(dest), "bytes": dest.stat().st_size}


def _integrity(path: Path) -> str:
    """`PRAGMA integrity_check` on the SNAPSHOT — the file that will be restored,
    not the one that is running. Returns "ok" or the first problem reported."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return f"unreadable: {e}"
    return str(rows[0][0]) if rows else "no result"


def _compress(src: Path, dest: Path) -> dict:
    """gzip, and hash what actually came out. The hash is what a later fetch
    checks the download against, so it is taken from the finished artefact."""
    digest = hashlib.sha256()
    with open(src, "rb") as raw, gzip.open(dest, "wb", compresslevel=6) as gz:
        while chunk := raw.read(1024 * 1024):
            gz.write(chunk)
    with open(dest, "rb") as done:
        while chunk := done.read(1024 * 1024):
            digest.update(chunk)
    return {"path": str(dest), "bytes": dest.stat().st_size, "sha256": digest.hexdigest()}


# ── Drive ────────────────────────────────────────────────────────────────────

async def _drive(method: str, url: str, token: str, **kwargs) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
    async with httpx.AsyncClient(timeout=120.0) as client:
        return await client.request(method, url, headers=headers, **kwargs)


async def _folder_id(token: str) -> tuple[str, str]:
    """The backup folder's id, created on first use. Returns (id, error)."""
    name = settings.octavius_drive_folder.replace("'", "\\'")
    query = (f"name = '{name}' and mimeType = '{_FOLDER_MIME}' and trashed = false")
    resp = await _drive("GET", f"{_DRIVE}/files", token,
                        params={"q": query, "fields": "files(id,name)", "pageSize": 5})
    if resp.status_code != 200:
        return "", f"Drive listing failed ({resp.status_code}): {resp.text[:200]}"
    files = resp.json().get("files", [])
    if files:
        return files[0]["id"], ""

    resp = await _drive("POST", f"{_DRIVE}/files", token,
                        json={"name": settings.octavius_drive_folder,
                              "mimeType": _FOLDER_MIME},
                        params={"fields": "id"})
    if resp.status_code not in (200, 201):
        return "", f"could not create the Drive folder ({resp.status_code}): {resp.text[:200]}"
    return resp.json()["id"], ""


async def _upload(path: Path, name: str, folder: str, token: str,
                  properties: dict) -> tuple[str, str]:
    """Resumable upload, in chunks. Returns (file_id, error).

    Resumable rather than a single POST because this is a backup: on a link that
    drops at 90% of a large database, a single POST starts again from zero, and a
    backup that only completes on a good day is a backup nobody can rely on.
    """
    total = path.stat().st_size
    start = await _drive(
        "POST", _UPLOAD, token,
        params={"uploadType": "resumable", "fields": "id"},
        json={"name": name, "parents": [folder],
              "description": "Speda Mark VI — Octavius Protocol brain backup",
              "appProperties": properties},
        headers={"X-Upload-Content-Type": "application/gzip",
                 "X-Upload-Content-Length": str(total)},
    )
    if start.status_code not in (200, 201):
        return "", f"upload could not start ({start.status_code}): {start.text[:200]}"
    session = start.headers.get("Location", "")
    if not session:
        return "", "Drive accepted the upload request but returned no session URI"

    offset = 0
    with open(path, "rb") as fh:
        while offset < total:
            fh.seek(offset)
            chunk = fh.read(_CHUNK)
            end = offset + len(chunk) - 1
            resp = await _drive(
                "PUT", session, token, content=chunk,
                headers={"Content-Range": f"bytes {offset}-{end}/{total}"},
            )
            if resp.status_code in (200, 201):
                return resp.json().get("id", ""), ""
            if resp.status_code == 308:
                # Drive says how much it actually holds. Trust that over our own
                # count — it is the whole reason for using a resumable session.
                rng = resp.headers.get("Range", "")
                offset = int(rng.rpartition("-")[2]) + 1 if "-" in rng else offset + len(chunk)
                continue
            return "", f"upload failed at byte {offset} ({resp.status_code}): {resp.text[:200]}"
    return "", "upload finished the file without Drive confirming it"


async def _confirm(file_id: str, expect: int, token: str) -> str:
    """Ask Drive how big the file it stored is. Empty string = it matches."""
    resp = await _drive("GET", f"{_DRIVE}/files/{file_id}", token,
                        params={"fields": "id,size,name"})
    if resp.status_code != 200:
        return f"uploaded, but Drive would not confirm it ({resp.status_code})"
    stored = int(resp.json().get("size") or 0)
    if stored != expect:
        return f"Drive stored {stored} bytes, expected {expect}"
    return ""


async def _list(token: str, folder: str) -> tuple[list[dict], str]:
    resp = await _drive(
        "GET", f"{_DRIVE}/files", token,
        params={"q": f"'{folder}' in parents and trashed = false",
                "orderBy": "createdTime desc", "pageSize": 100,
                "fields": "files(id,name,size,createdTime,appProperties)"},
    )
    if resp.status_code != 200:
        return [], f"Drive listing failed ({resp.status_code}): {resp.text[:200]}"
    return resp.json().get("files", []), ""


async def _retain(files: list[dict], token: str) -> list[str]:
    """Trash everything past the keep count. TRASH, not delete: Drive holds it
    for 30 days, so a retention setting typed with one digit too few is a
    mistake the owner can undo instead of a history they cannot."""
    doomed = files[settings.octavius_keep:]
    trashed = []
    for old in doomed:
        resp = await _drive("PATCH", f"{_DRIVE}/files/{old['id']}", token,
                            json={"trashed": True}, params={"fields": "id"})
        if resp.status_code == 200:
            trashed.append(old["name"])
        else:
            logger.warning("octavius_retain_failed",
                           extra={"backup_name": old["name"], "status": resp.status_code})
    return trashed


# ── The protocol ─────────────────────────────────────────────────────────────

async def backup() -> tuple[bool, dict]:
    """Snapshot → verify → compress → upload → confirm → prune. Never raises.

    Returns (ok, report). Called by n8n on a cron and by the owner through
    Orion; the two paths are identical because a backup is the same operation
    whoever asked for it.
    """
    if not settings.octavius_protocol_enabled:
        return False, {"stage": "disabled", "error": (
            "The Octavius Protocol is disabled on this deployment "
            "(OCTAVIUS_PROTOCOL_ENABLED is off). Nothing was backed up."
        )}

    source = db_path()
    if source is None:
        return False, {"stage": "database", "error": (
            "This deployment is not on SQLite, and Octavius knows how to snapshot "
            "exactly one kind of database. Nothing was backed up."
        )}
    if not source.exists():
        return False, {"stage": "database",
                       "error": f"the database file is missing at {source}"}

    token = await access_token()
    if not token:
        return False, {"stage": "google", "error": (
            "Google is not connected, so there is nowhere to put the backup. Ask "
            "the owner to sign in via Settings → Google Workspace. Nothing was "
            "backed up — do not report this as a transient error, it will fail "
            "identically every run until they reconnect."
        )}

    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    name = f"speda-brain-{stamp}.db.gz"
    snap = _WORK / f"{stamp}.db"
    archive = _WORK / name
    report: dict = {"name": name, "started_at": _now(), "live_bytes": source.stat().st_size}

    try:
        _WORK.mkdir(parents=True, exist_ok=True)

        shot = await snapshot(snap)
        report["snapshot_bytes"] = shot["bytes"]

        integrity = await asyncio.to_thread(_integrity, snap)
        report["integrity"] = integrity
        if integrity != "ok":
            return False, {**report, "stage": "integrity", "error": (
                f"the snapshot failed its integrity check ({integrity}), so it was "
                "NOT uploaded. The live database may be damaged — say so plainly "
                "rather than reporting a failed backup."
            )}

        packed = await asyncio.to_thread(_compress, snap, archive)
        report["archive_bytes"] = packed["bytes"]
        report["sha256"] = packed["sha256"]

        folder, err = await _folder_id(token)
        if err:
            return False, {**report, "stage": "folder", "error": err}

        file_id, err = await _upload(
            archive, name, folder, token,
            {"sha256": packed["sha256"],
             "snapshot_bytes": str(shot["bytes"]),
             "schema": "sqlite",
             "taken_at": report["started_at"]},
        )
        if err:
            return False, {**report, "stage": "upload", "error": err}
        report["file_id"] = file_id

        mismatch = await _confirm(file_id, packed["bytes"], token)
        if mismatch:
            return False, {**report, "stage": "confirm", "error": mismatch}

        files, err = await _list(token, folder)
        report["kept"] = min(len(files), settings.octavius_keep)
        report["trashed"] = await _retain(files, token) if not err else []

    except Exception as e:  # noqa: BLE001
        logger.error("octavius_backup_failed", extra={"error": str(e)})
        return False, {**report, "stage": "unexpected", "error": str(e)[:400]}
    finally:
        # The scratch copies are the size of the database. Leaving one behind on
        # a failed run is how the next run fails for a different reason.
        snap.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)

    report["finished_at"] = _now()
    # NB: not `name` — logging refuses an `extra` key that collides with a
    # LogRecord attribute, and would raise on every SUCCESSFUL backup.
    logger.warning("octavius_backup", extra={
        "backup_name": report.get("name"),
        "archive_bytes": report.get("archive_bytes"),
        "kept": report.get("kept"),
    })
    return True, report


async def backups() -> tuple[list[dict], str]:
    """Every backup Drive actually holds, newest first."""
    if not settings.octavius_protocol_enabled:
        return [], "the Octavius Protocol is disabled on this deployment"
    token = await access_token()
    if not token:
        return [], "Google is not connected"
    folder, err = await _folder_id(token)
    if err:
        return [], err
    files, err = await _list(token, folder)
    if err:
        return [], err
    return [
        {
            "id": f["id"],
            "name": f["name"],
            "bytes": int(f.get("size") or 0),
            "mb": _mb(int(f.get("size") or 0)),
            "created": f.get("createdTime", ""),
            "sha256": (f.get("appProperties") or {}).get("sha256", ""),
        }
        for f in files
    ], ""


async def status() -> dict:
    """Whether a usable backup exists — asked of Drive, not of our own records.

    A local "last backup succeeded" note survives exactly the failures it is
    meant to catch: a revoked token, a trashed folder, someone tidying Drive. So
    the answer comes from the only place that can actually be wrong in the
    owner's favour.
    """
    out = {
        "enabled": settings.octavius_protocol_enabled,
        "count": 0, "latest": None, "age_hours": None,
        "stale": False, "detail": "",
    }
    if not settings.octavius_protocol_enabled:
        out["detail"] = "OCTAVIUS_PROTOCOL_ENABLED is off on this deployment."
        return out

    found, err = await backups()
    if err:
        out["detail"] = err
        out["stale"] = True
        return out

    out["count"] = len(found)
    if not found:
        out["stale"] = True
        out["detail"] = "Drive holds no backups at all."
        return out

    latest = found[0]
    out["latest"] = latest
    try:
        from datetime import datetime
        taken = datetime.fromisoformat(latest["created"].replace("Z", "+00:00"))
        age = (utc_now() - taken).total_seconds() / 3600
        out["age_hours"] = round(age, 1)
        out["stale"] = age > settings.octavius_stale_hours
    except Exception:  # noqa: BLE001
        out["detail"] = "the newest backup has an unreadable timestamp"
    return out


async def fetch(file_id: str = "") -> tuple[bool, str]:
    """Download a backup, verify it end to end, and stage it for a manual swap.

    Deliberately stops short of installing it — see the module docstring. The
    swap instructions it prints are the ones in MANIFEST, with real paths.
    """
    if not settings.octavius_protocol_enabled:
        return False, "The Octavius Protocol is disabled on this deployment."

    token = await access_token()
    if not token:
        return False, "Google is not connected, so nothing could be downloaded."

    found, err = await backups()
    if err:
        return False, f"Could not list the backups: {err}"
    if not found:
        return False, "Drive holds no backups, so there is nothing to restore from."

    chosen = next((f for f in found if f["id"] == file_id), None) if file_id else found[0]
    if chosen is None:
        return False, (f"No backup with id {file_id}. Run list first and use an id "
                       "from it verbatim.")

    _RESTORE_DIR.mkdir(parents=True, exist_ok=True)
    archive = _WORK / chosen["name"]
    staged = _RESTORE_DIR / chosen["name"].replace(".db.gz", ".db")

    try:
        _WORK.mkdir(parents=True, exist_ok=True)
        resp = await _drive("GET", f"{_DRIVE}/files/{chosen['id']}", token,
                            params={"alt": "media"})
        if resp.status_code != 200:
            return False, f"Download failed ({resp.status_code}): {resp.text[:200]}"
        archive.write_bytes(resp.content)

        digest = hashlib.sha256(resp.content).hexdigest()
        if chosen["sha256"] and digest != chosen["sha256"]:
            return False, (
                "REFUSED — the downloaded file does not match the hash recorded "
                "when it was uploaded, so it is corrupt in transit or in Drive. "
                "Nothing was staged. Try another backup."
            )

        with gzip.open(archive, "rb") as gz, open(staged, "wb") as out:
            shutil.copyfileobj(gz, out)

        integrity = await asyncio.to_thread(_integrity, staged)
        if integrity != "ok":
            staged.unlink(missing_ok=True)
            return False, (
                f"REFUSED — the restored database failed its integrity check "
                f"({integrity}). Nothing was staged. Try an older backup."
            )
    except Exception as e:  # noqa: BLE001
        logger.error("octavius_fetch_failed", extra={"error": str(e)})
        return False, f"The restore staging failed: {str(e)[:300]}"
    finally:
        archive.unlink(missing_ok=True)

    host_dir = "/opt/speda"
    logger.warning("octavius_fetched", extra={"backup_name": chosen["name"]})
    return True, "\n".join([
        f"STAGED — {chosen['name']} ({_mb(staged.stat().st_size)} MB uncompressed) "
        "downloaded, hash verified, integrity check passed.",
        f"It is on the host at {host_dir}/restore/{staged.name}",
        "",
        "The live database has NOT been touched, and Igor must not be the one to "
        "swap it — this process is holding that file open. Give the owner these, "
        "in this order:",
        "",
        "    docker compose stop app",
        f"    mv {host_dir}/speda.db {host_dir}/speda.db.before-restore",
        f"    rm -f {host_dir}/speda.db-wal {host_dir}/speda.db-shm",
        f"    mv {host_dir}/restore/{staged.name} {host_dir}/speda.db",
        "    docker compose start app",
        "",
        "Step 3 is not optional and not tidying: a leftover -wal is read as the "
        "NEW database's journal, and a journal from a different database is "
        "corruption that passes every check.",
        "",
        f"Not in this archive: runtime_state.json and .env (both in {host_dir}). "
        "They hold the OAuth tokens and portal credentials and are never uploaded "
        "— carry them across by hand or reconnect the integrations.",
    ])
