# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Octavius Protocol: a copy nobody verified is not a backup.

The failure this module exists to prevent is not "the backup did not run". It is
"the backup ran, reported success, and is not restorable" — which is worse than
having none, because it stops anyone from looking for one.

So what is pinned here is the chain of proof:

  * the snapshot is taken with VACUUM INTO through Igor's own engine, because the
    database is in WAL mode and copying the .db file gives you a database missing
    whatever was still in the journal (DEPLOY.md's `scp` has this bug);
  * an integrity check runs on the SNAPSHOT — and a failure there is a statement
    about the LIVE database, reported as such, never as a flaky backup;
  * the upload is confirmed against Drive's own idea of the stored size;
  * `status` asks DRIVE whether a backup exists, never a local note, because a
    local note survives exactly the failures it is meant to catch;
  * the credential files are never uploaded, and no argument makes them be;
  * a restore is staged, never swapped, and the swap instructions delete the
    stale journal.
"""

import gzip
import sqlite3
from pathlib import Path

import pytest

from app.services import octavius
from app.skills.octavius import OctaviusProtocolSkill


class _Resp:
    def __init__(self, status=200, payload=None, headers=None, content=b""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.content = content
        self.text = str(self._payload)

    def json(self):
        return self._payload


@pytest.fixture
def real_db(tmp_path, monkeypatch):
    """An actual SQLite file, in WAL mode, with rows in it — so VACUUM INTO and
    PRAGMA integrity_check are exercised for real rather than mocked.

    app.database.engine is replaced as well as the URL. It is built once at
    import from settings.database_url, so patching the setting alone would leave
    snapshot() quietly copying whichever database the test session imported
    first — which passes in isolation and snapshots the wrong file in a full run.
    """
    path = tmp_path / "speda.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT INTO messages (body) VALUES (?)",
                     [(f"message {i}",) for i in range(50)])
    conn.commit()
    conn.close()

    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    monkeypatch.setattr(octavius.settings, "database_url", url)

    import app.database as db_mod
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    monkeypatch.setattr(db_mod, "engine", create_async_engine(url, poolclass=NullPool))
    return path


@pytest.fixture
def drive(tmp_path, monkeypatch):
    """A fake Drive: records every call, holds uploaded bytes in memory."""

    class Drive:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []
            self.files: dict[str, dict] = {}
            self.blobs: dict[str, bytes] = {}
            self.folder_exists = True
            self.upload_status = 200
            self.stored_size_override: int | None = None
            self._n = 0
            self._seq = 0

        async def request(self, method, url, token, **kwargs):
            self.calls.append((method, url))
            params = kwargs.get("params") or {}

            # Order matters: the resumable upload endpoint ALSO ends in /files,
            # so it has to be matched before the plain Drive files collection.
            if method == "POST" and "/upload/" in url:
                self._n += 1
                self._pending = {"id": f"file{self._n}", **kwargs["json"]}
                return _Resp(200, headers={"Location": f"https://upload/{self._n}"})

            if method == "PUT":
                if self.upload_status != 200:
                    return _Resp(self.upload_status, {"error": "boom"})
                f = self._pending
                self.blobs[f["id"]] = self.blobs.get(f["id"], b"") + kwargs.get("content", b"")
                self._seq += 1
                self.files[f["id"]] = {
                    "id": f["id"], "name": f["name"],
                    "size": str(len(self.blobs[f["id"]])),
                    "createdTime": f"2026-08-25T00:00:{self._seq:02d}.000Z",
                    "appProperties": f.get("appProperties", {}),
                }
                return _Resp(200, {"id": f["id"]})

            if method == "GET" and url.endswith("/files"):
                # The folder lookup is the one that filters on mimeType; the
                # backup listing filters on parents.
                if "mimeType" in params.get("q", ""):
                    return _Resp(200, {"files": [{"id": "folder1", "name": "backups"}]
                                       if self.folder_exists else []})
                listed = sorted(self.files.values(),
                                key=lambda f: f["createdTime"], reverse=True)
                return _Resp(200, {"files": listed})

            if method == "POST" and url.endswith("/files"):
                self.folder_exists = True
                return _Resp(200, {"id": "folder1"})

            if method == "GET" and params.get("alt") == "media":
                return _Resp(200, content=self.blobs[url.rsplit("/", 1)[1]])

            if method == "GET":
                stored = dict(self.files.get(url.rsplit("/", 1)[1], {}))
                if self.stored_size_override is not None:
                    stored["size"] = str(self.stored_size_override)
                return _Resp(200, stored)

            if method == "PATCH":
                fid = url.rsplit("/", 1)[1]
                self.files.pop(fid, None)
                return _Resp(200, {"id": fid})

            return _Resp(404, {})

    d = Drive()
    monkeypatch.setattr(octavius, "_drive", d.request)

    async def _token():
        return "tok"

    monkeypatch.setattr(octavius, "access_token", _token)
    monkeypatch.setattr(octavius, "_WORK", tmp_path / "work")
    monkeypatch.setattr(octavius, "_RESTORE_DIR", tmp_path / "restore")
    monkeypatch.setattr(octavius.settings, "octavius_protocol_enabled", True)
    monkeypatch.setattr(octavius.settings, "octavius_keep", 3)
    monkeypatch.setattr(octavius.settings, "octavius_stale_hours", 48)
    return d


class _Ctx:
    request_id = "test-request"
    agent_id = "orion"
    user_id = 1
    triggered_by = "user"
    trigger_payload: dict = {}
    extra: dict = {}


def _ctx(triggered_by="user"):
    c = _Ctx()
    c.triggered_by = triggered_by
    return c


# ── The snapshot ─────────────────────────────────────────────────────────────

async def test_the_snapshot_carries_data_still_in_the_write_ahead_log(real_db, tmp_path):
    """The bug in `scp speda.db`: WAL-mode commits are not in the .db file yet."""
    conn = sqlite3.connect(real_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO messages (body) VALUES ('written moments ago')")
    conn.commit()

    dest = tmp_path / "snap.db"
    await octavius.snapshot(dest)
    conn.close()

    rows = sqlite3.connect(dest).execute(
        "SELECT count(*) FROM messages WHERE body = 'written moments ago'"
    ).fetchone()
    assert rows[0] == 1, "the snapshot lost a commit that was still in the WAL"


async def test_the_snapshot_needs_no_wal_sidecars_to_be_complete(real_db, tmp_path):
    dest = tmp_path / "snap.db"
    await octavius.snapshot(dest)

    assert not (tmp_path / "snap.db-wal").exists()
    assert octavius._integrity(dest) == "ok"


def test_integrity_check_catches_a_damaged_file(tmp_path):
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"SQLite format 3\x00" + b"\x00" * 500)
    assert octavius._integrity(broken) != "ok"


def test_a_non_sqlite_deployment_is_refused_rather_than_guessed(monkeypatch):
    monkeypatch.setattr(octavius.settings, "database_url", "postgresql+asyncpg://h/db")
    assert octavius.db_path() is None


async def test_a_non_sqlite_deployment_backs_nothing_up(real_db, drive, monkeypatch):
    monkeypatch.setattr(octavius.settings, "database_url", "postgresql+asyncpg://h/db")
    ok, report = await octavius.backup()

    assert not ok and report["stage"] == "database"
    assert drive.calls == []


# ── A full run ───────────────────────────────────────────────────────────────

async def test_a_backup_uploads_a_restorable_archive(real_db, drive):
    ok, report = await octavius.backup()

    assert ok, report
    assert report["integrity"] == "ok"
    assert report["name"].startswith("speda-brain-")
    assert report["archive_bytes"] > 0

    raw = gzip.decompress(drive.blobs[report["file_id"]])
    assert raw[:15] == b"SQLite format 3", "the archive must gunzip to a real database"


async def test_the_uploaded_bytes_hash_to_what_the_metadata_claims(real_db, drive):
    import hashlib

    ok, report = await octavius.backup()
    stored = drive.files[report["file_id"]]

    assert stored["appProperties"]["sha256"] == report["sha256"]
    assert hashlib.sha256(drive.blobs[report["file_id"]]).hexdigest() == report["sha256"]


async def test_a_short_upload_fails_the_run_instead_of_reporting_success(real_db, drive):
    """A backup that quietly stored zero bytes is worse than none."""
    drive.stored_size_override = 0
    ok, report = await octavius.backup()

    assert not ok
    assert report["stage"] == "confirm"


async def test_a_failed_upload_is_not_reported_as_a_backup(real_db, drive):
    drive.upload_status = 503
    ok, report = await octavius.backup()

    assert not ok and report["stage"] == "upload"


async def test_google_not_connected_stops_before_touching_the_database(real_db, drive, monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr(octavius, "access_token", _none)
    ok, report = await octavius.backup()

    assert not ok and report["stage"] == "google"
    assert "reconnect" in report["error"] or "sign in" in report["error"]


async def test_scratch_files_never_survive_a_run(real_db, drive):
    await octavius.backup()
    work = octavius._WORK
    assert not work.exists() or list(work.iterdir()) == [], (
        "a snapshot left behind is the size of the database, and is how the "
        "next run fails for a different reason"
    )


async def test_scratch_files_never_survive_a_failure_either(real_db, drive):
    drive.upload_status = 500
    await octavius.backup()
    work = octavius._WORK
    assert not work.exists() or list(work.iterdir()) == []


# ── Retention ────────────────────────────────────────────────────────────────

async def test_old_backups_are_trashed_not_deleted(real_db, drive):
    for _ in range(5):
        ok, _ = await octavius.backup()
        assert ok

    patches = [c for c in drive.calls if c[0] == "PATCH"]
    assert patches, "retention must actually retire something past the keep count"
    remaining, err = await octavius.backups()
    assert not err
    assert len(remaining) <= octavius.settings.octavius_keep


# ── Status ───────────────────────────────────────────────────────────────────

async def test_status_reports_no_protection_when_drive_is_empty(real_db, drive):
    state = await octavius.status()

    assert state["count"] == 0
    assert state["stale"] is True, "nothing at all is not 'fresh'"


async def test_status_treats_an_unreachable_drive_as_no_protection(real_db, drive, monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr(octavius, "access_token", _none)
    state = await octavius.status()

    assert state["stale"] is True
    assert state["count"] == 0


async def test_status_counts_what_drive_holds_not_what_we_believe(real_db, drive):
    await octavius.backup()
    state = await octavius.status()
    assert state["count"] == 1

    # Somebody tidies Drive. Our own record of a successful run must not survive it.
    drive.files.clear()
    state = await octavius.status()
    assert state["count"] == 0
    assert state["stale"] is True


# ── Restore ──────────────────────────────────────────────────────────────────

async def test_fetch_stages_a_verified_copy_without_touching_the_live_database(real_db, drive):
    await octavius.backup()
    before = real_db.read_bytes()

    ok, report = await octavius.fetch()
    assert ok, report
    assert real_db.read_bytes() == before, "the live database must not be touched"

    staged = list((octavius._RESTORE_DIR).iterdir())
    assert len(staged) == 1
    assert octavius._integrity(staged[0]) == "ok"


async def test_fetch_refuses_a_download_that_does_not_match_its_hash(real_db, drive):
    ok, report = await octavius.backup()
    drive.blobs[report["file_id"]] = gzip.compress(b"not the database")

    ok, report = await octavius.fetch()
    assert not ok
    assert "does not match the hash" in report
    assert not (octavius._RESTORE_DIR.exists() and list(octavius._RESTORE_DIR.iterdir()))


async def test_the_swap_instructions_delete_the_stale_journal(real_db, drive):
    """A leftover -wal is read as the NEW database's journal: silent corruption."""
    await octavius.backup()
    _, report = await octavius.fetch()

    assert "speda.db-wal" in report and "speda.db-shm" in report
    assert "rm -f" in report
    assert report.index("stop app") < report.index("rm -f") < report.index("start app")


async def test_fetch_never_performs_the_swap_itself(real_db, drive):
    await octavius.backup()
    _, report = await octavius.fetch()

    assert "has NOT been touched" in report
    assert real_db.exists()


async def test_fetch_with_no_backups_says_so(real_db, drive):
    ok, report = await octavius.fetch()
    assert not ok
    assert "nothing to restore" in report


# ── The credential boundary ──────────────────────────────────────────────────

async def test_no_credential_file_is_ever_uploaded(real_db, drive):
    ok, report = await octavius.backup()
    payload = drive.blobs[report["file_id"]]

    assert b"runtime_state" not in payload
    assert b"refresh_token" not in payload


def test_the_manifest_names_what_the_owner_still_has_to_carry():
    assert "runtime_state.json" in octavius.MANIFEST
    assert ".env" in octavius.MANIFEST
    assert "speda.db-wal" in octavius.MANIFEST


def test_there_is_no_flag_that_uploads_secrets():
    schema = OctaviusProtocolSkill().input_schema["properties"]
    assert set(schema) == {"action", "file_id"}


# ── The gate ─────────────────────────────────────────────────────────────────

async def test_a_watchdog_may_take_a_backup_unasked(real_db, drive):
    """It creates and cannot lose anything, and the moment it matters most is
    the moment nobody is around to authorise one."""
    result = await OctaviusProtocolSkill().execute(
        {"action": "backup"}, _ctx(triggered_by="n8n")
    )
    assert "BACKUP COMPLETE" in result


async def test_staging_a_restore_needs_the_owner(real_db, drive):
    await octavius.backup()
    result = await OctaviusProtocolSkill().execute(
        {"action": "fetch"}, _ctx(triggered_by="n8n")
    )

    assert "REFUSED" in result
    assert not (octavius._RESTORE_DIR.exists() and list(octavius._RESTORE_DIR.iterdir()))


async def test_an_integrity_failure_is_reported_as_a_database_problem(real_db, drive, monkeypatch):
    monkeypatch.setattr(octavius, "_integrity", lambda p: "row 12 missing from index")
    result = await OctaviusProtocolSkill().execute({"action": "backup"}, _ctx())

    assert "LIVE DATABASE" in result
    assert "transient" in result


async def test_a_disabled_deployment_says_protection_does_not_exist(real_db, drive, monkeypatch):
    monkeypatch.setattr(octavius.settings, "octavius_protocol_enabled", False)
    result = await OctaviusProtocolSkill().execute({"action": "backup"}, _ctx())

    assert "NOTHING is being backed up" in result
    assert drive.calls == []
