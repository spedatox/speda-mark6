"""Wire schemas for the Octavius Protocol. See services/octavius.py."""

from pydantic import BaseModel, Field


class BackupEntry(BaseModel):
    """One archive Drive actually holds.

    `sha256` is the hash of the gzip as it was uploaded, carried in the file's
    Drive metadata. It is what lets a restore prove the bytes that came back are
    the bytes that went up, rather than assuming it.
    """

    id: str
    name: str
    bytes: int = 0
    mb: float = 0.0
    created: str = ""
    sha256: str = ""


class OctaviusStatus(BaseModel):
    """Whether a usable backup exists — answered by asking Drive.

    Never by consulting a local record that one was made: such a record survives
    exactly the failures it is meant to catch (a revoked token, a trashed folder,
    someone tidying up), and would report protection that is not there.
    """

    enabled: bool = False
    count: int = 0
    latest: BackupEntry | None = None
    age_hours: float | None = None
    # True when the newest backup is older than octavius_stale_hours, when Drive
    # holds none at all, or when it could not be asked. All three mean the same
    # thing to whoever is reading: there is no protection you can count on.
    stale: bool = False
    detail: str = ""


class BackupResult(BaseModel):
    """What one run actually did, stage by stage.

    `stage` on a failure names where it stopped — 'google', 'integrity',
    'upload', 'confirm'. That distinction matters: an integrity failure is a
    statement about the LIVE database, not about the backup, and must not be
    reported as "the backup failed, try again later".
    """

    ok: bool
    name: str = ""
    stage: str = ""
    error: str = ""
    live_bytes: int = 0
    snapshot_bytes: int = 0
    archive_bytes: int = 0
    integrity: str = ""
    sha256: str = ""
    file_id: str = ""
    kept: int = 0
    trashed: list[str] = Field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


class FetchRequest(BaseModel):
    """Stage a backup for a manual swap. Empty id = the newest one."""

    file_id: str = Field(default="", max_length=128)


class FetchResult(BaseModel):
    ok: bool
    report: str
