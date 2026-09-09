# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned state records and a deterministic, time-aware current view.

Records are ordinary revisioned memory documents; their small JSON envelope is
the lifecycle contract, not a replacement for domain documents. Expiry never
claims completion: overdue records are explicitly unverified until reviewed.
"""

import hashlib
import json
import re
from datetime import date
from types import SimpleNamespace

ROOT = "/memories/states/"
CURRENT = "/memories/current.md"
MARKER = "<!-- memory-state-v1 "
STATUSES = ("active", "waiting", "planned", "completed", "cancelled", "superseded")
OPEN = frozenset(STATUSES[:3])


def version(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def parse(content: str) -> dict:
    line = next((s for s in content.splitlines() if s.startswith(MARKER)), "")
    if not line.endswith(" -->"):
        raise ValueError("State metadata is missing. Use memory_state to repair this record.")
    record = json.loads(line[len(MARKER):-4])
    validate(record)
    return record


def validate(record: dict) -> None:
    if not isinstance(record, dict):
        raise ValueError("State metadata must be an object.")
    for key in ("key", "summary", "status", "source", "review_on", "verified_on"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError(f"State requires {key}.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", record["key"]):
        raise ValueError("Use a stable lowercase-hyphenated state key, without dates or paths.")
    if record["status"] not in STATUSES:
        raise ValueError(f"State status must be one of {', '.join(STATUSES)}.")
    for field in ("review_on", "verified_on", "starts_on", "ends_on", "closed_on"):
        if record.get(field):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record[field]):
                raise ValueError(f"{field} must be an absolute YYYY-MM-DD date.")
            date.fromisoformat(record[field])
    if record["review_on"] < record["verified_on"]:
        raise ValueError("review_on cannot precede verified_on.")
    if record.get("starts_on") and record.get("ends_on") and record["ends_on"] < record["starts_on"]:
        raise ValueError("ends_on cannot precede starts_on.")
    if record["status"] not in OPEN and not record.get("closed_on"):
        raise ValueError("A terminal state requires closed_on and evidence of the outcome.")
    if record["status"] in OPEN and record.get("closed_on"):
        raise ValueError("An open state cannot have closed_on.")
    for key in ("summary", "source"):
        if "\n" in record[key] or "<!--" in record[key] or "-->" in record[key]:
            raise ValueError(f"{key} must be a single line without metadata delimiters.")


def encode(record: dict) -> str:
    validate(record)
    metadata = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return (
        f"# {record['key']}\n\n{MARKER}{metadata} -->\n\n"
        f"{record['summary']}\n\n"
        f"- Status: {record['status']}\n- Verified: {record['verified_on']}\n"
        f"- Review: {record['review_on']}\n- Source: {record['source']}\n"
    )


def render(files, today: date) -> str:
    active, review, planned = [], [], []
    for f in sorted(files, key=lambda f: f.path):
        if not f.path.startswith(ROOT):
            continue
        try:
            r = parse(f.content)
        except (ValueError, TypeError, KeyError):
            review.append(f"- Invalid state record: {f.path}. Inspect and repair; do not infer its contents.")
            continue
        if r["status"] not in OPEN:
            continue
        stamp = today.isoformat()
        bullet = f"- [{r['key']}] {r['summary']} (status: {r['status']}; verified {r['verified_on']}; source: {r['source']})"
        if r["review_on"] < stamp or (r.get("ends_on") and r["ends_on"] < stamp):
            review.append(f"- [{r['key']}] Review overdue; last verified {r['verified_on']}. Read {f.path}; do not assume it remains true or has completed.")
        elif r["status"] == "planned" or (r.get("starts_on") and r["starts_on"] > stamp):
            planned.append(bullet)
        else:
            active.append(bullet)
    sections = ["# Current — ongoing situations", "", "_Computed from versioned state records. Completed actions belong in dated logs._"]
    for heading, rows in (("Active / waiting", active), ("Confirmed plans", planned), ("Needs verification", review)):
        sections += ["", f"## {heading}", "", *(rows or ["(none recorded)"])]
    return "\n".join(sections) + "\n"


def project_files(files, today: date):
    """Read-only projection; never dirty an ORM instance while assembling recall.

    Migration writes a marker into current.md. Until then the legacy snapshot
    remains visible, explicitly labelled unverified; deployment cannot erase it.
    """
    files = list(files)
    current = next((f for f in files if f.path == CURRENT), None)
    migrated = current is not None and "<!-- state-projection-v1 -->" in current.content
    if not migrated:
        transitional = render(files, today) if any(f.path.startswith(ROOT) for f in files) else ""
        return [SimpleNamespace(path=f.path, content=(
            transitional + "\n_Legacy snapshot: not lifecycle-verified. Confirm before present-tense use._\n\n" + f.content
            if f.path == CURRENT else f.content), updated_at=f.updated_at) for f in files]
    content = render(files, today)
    return [SimpleNamespace(path=f.path, content=content if f.path == CURRENT else f.content,
                            updated_at=f.updated_at) for f in files]


async def effective_current(db, user_id: int) -> str:
    from sqlalchemy import select
    from app.core.clock import owner_today
    from app.models.memory_file import MemoryFile
    files = (await db.execute(select(MemoryFile).where(MemoryFile.user_id == user_id))).scalars().all()
    return next((f.content for f in project_files(files, owner_today()) if f.path == CURRENT), "")


async def verify_source(db, user_id: int, source: str) -> None:
    """Resolve at least one evidence reference within this owner's store.

    Existence is not semantic entailment; the writer and Orion still check what
    the source actually says. Missing references are a deterministic rejection.
    """
    from sqlalchemy import select
    from app.models.memory_file import MemoryFile
    from app.models.observation import Observation
    from app.models.message import Message
    from app.models.session import Session
    refs = re.findall(r"/memories/[a-zA-Z0-9_./-]+\.md", source)
    obs_ids = [int(n) for n in re.findall(r"observation:(\d+)", source)]
    msg_ids = [int(n) for n in re.findall(r"message:(\d+)", source)]
    if not refs and not obs_ids and not msg_ids:
        raise ValueError("source must cite an existing /memories/...md path, observation:<id>, or message:<id>.")
    for path in refs:
        if path == CURRENT or path.startswith(ROOT):
            raise ValueError("A state cannot cite the current view or another state as its only evidence; cite the original domain document or conversation.")
        exists = (await db.execute(select(MemoryFile.id).where(
            MemoryFile.user_id == user_id, MemoryFile.path == path))).scalar_one_or_none()
        if exists is None:
            raise ValueError(f"Evidence file does not exist: {path}")
    for ident in obs_ids:
        exists = (await db.execute(select(Observation.id).where(
            Observation.user_id == user_id, Observation.id == ident,
            Observation.deleted_at.is_(None)))).scalar_one_or_none()
        if exists is None:
            raise ValueError(f"Evidence observation is missing or retired: {ident}")
    for ident in msg_ids:
        exists = (await db.execute(select(Message.id).join(Session, Message.session_id == Session.id).where(
            Session.user_id == user_id, Message.id == ident, Message.role == "user"))).scalar_one_or_none()
        if exists is None:
            raise ValueError(f"Owner evidence message is missing: {ident}")
