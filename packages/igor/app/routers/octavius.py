"""
The Octavius Protocol's HTTP surface: back the brain up, see what exists, stage
a restore.

Thin per Rule 1 — the snapshot, the verification and the Drive work all live in
services/octavius.py.

`POST /admin/octavius/backup` is what both n8n and the owner's own Protocols pane
call — one endpoint, not two, because "back up now" is the same operation
whoever asked for it.

Its shape is worth noticing: unlike every other scheduled endpoint here it is not
a probe deciding whether to spend a turn. It IS the work, and it costs zero
tokens, so the cron does the whole job without an agent ever waking up. A turn is
spent only when it FAILS — the same cost boundary as the watchers, inverted.

It can take minutes on a large database (snapshot, gzip, upload), so give the
calling node a generous timeout. Nothing else runs meanwhile that would care.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.octavius import (
    BackupEntry,
    BackupResult,
    FetchRequest,
    FetchResult,
    OctaviusStatus,
)
from app.services import octavius

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/octavius", tags=["octavius"])


@router.post("/backup", response_model=BackupResult)
async def run_backup():
    """Snapshot the database, verify it, and upload it to Drive.

    X-API-Key only, like the rest of `/admin` — deliberately NOT the dual-secret
    posture the probes carry. The extra secret exists there because a poller
    reaching the owner's mail should prove it is the poller; here it would buy
    nothing, since anyone holding the API key can already read every message
    through /chat. What it WOULD cost is the owner's own "back up now" button:
    the desktop client has the API key and not the n8n secret, and the answer to
    that is one endpoint the owner can reach, never a second one beside it.

    Returns 200 with `ok: false` rather than an error status — n8n's Gate reads
    the body, and a non-2xx would make a failed backup indistinguishable from an
    unreachable Igor when telling those apart is the entire point."""
    ok, report = await octavius.backup()
    if not ok:
        logger.error("octavius_backup_failed", extra={"stage": report.get("stage"),
                                                      "error": report.get("error")})
    return BackupResult(ok=ok, **report)


@router.get("", response_model=OctaviusStatus)
async def status():
    """Whether a backup you can actually rely on exists, asked of Drive itself."""
    return await octavius.status()


@router.get("/backups", response_model=list[BackupEntry])
async def list_backups():
    """Every archive in the Drive folder, newest first. The `id` from here is
    what `fetch` takes."""
    found, err = await octavius.backups()
    if err:
        raise HTTPException(status_code=409, detail=err)
    return found


@router.post("/fetch", response_model=FetchResult)
async def fetch(body: FetchRequest):
    """Download a backup, verify it end to end, and stage it beside the live
    database — without touching the live database.

    The swap itself is deliberately not done here: this process holds the
    database file open, and replacing it underneath a running engine is corrupt,
    not merely risky. The response carries the exact commands."""
    ok, report = await octavius.fetch(body.file_id)
    if not ok:
        raise HTTPException(status_code=409, detail=report)
    return FetchResult(ok=ok, report=report)
