# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Durable, typed memory connections; retrieval follows edges, not prompt dumps.

An edge names why two records connect. Source-message and entity edges are
associations, never assertions that one event caused another. Writers call the
indexers inside their existing transaction; neither indexer calls a model.
"""

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import defer

from app.models.memory_graph_edge import MemoryGraphEdge


def _ref(kind: str, identifier) -> str:
    return f"{kind}:{identifier}"


async def add_edge(db, user_id: int, source: str, target: str,
                   relation: str, evidence_ref: str | None = None) -> None:
    if source == target:
        return
    if any(isinstance(item, MemoryGraphEdge) and item.user_id == user_id
           and item.source_ref == source and item.target_ref == target
           and item.relation_type == relation for item in db.new):
        return
    existing = (await db.execute(select(MemoryGraphEdge.id).where(
        MemoryGraphEdge.user_id == user_id,
        MemoryGraphEdge.source_ref == source,
        MemoryGraphEdge.target_ref == target,
        MemoryGraphEdge.relation_type == relation,
    ))).scalar_one_or_none()
    if existing is None:
        db.add(MemoryGraphEdge(user_id=user_id, source_ref=source,
                               target_ref=target, relation_type=relation,
                               evidence_ref=evidence_ref))


async def index_observation(db, user_id: int, obs, *,
                            known_subjects: set[str] | None = None,
                            retro_link: bool = True) -> None:
    """Attach one recorded fact to its subject, sources and owner messages."""
    from app.models.observation import Observation
    from app.models.message import Message
    from app.models.session import Session

    node = _ref("observation", obs.id)
    await add_edge(db, user_id, node, _ref("entity", obs.subject), "about")
    claimed_messages = {int(i) for i in (obs.message_ids or [])}
    valid_messages = set()
    if claimed_messages:
        valid_messages = set((await db.execute(select(Message.id).join(Session).where(
            Session.user_id == user_id, Session.triggered_by == "user",
            Message.role == "user", Message.id.in_(claimed_messages),
        ))).scalars().all())
    for message_id in sorted(valid_messages):
        await add_edge(db, user_id, node, _ref("message", int(message_id)),
                       "evidenced_by", _ref("message", int(message_id)))
    source_ids = {int(i) for i in (obs.source_ids or [])}
    if source_ids:
        valid = set((await db.execute(select(Observation.id).where(
            Observation.user_id == user_id, Observation.deleted_at.is_(None),
            Observation.id.in_(source_ids),
        ))).scalars().all())
        for source_id in sorted(valid):
            await add_edge(db, user_id, node, _ref("observation", source_id),
                           "derived_from", _ref("observation", source_id))

    subjects = known_subjects
    if subjects is None:
        subjects = set((await db.execute(select(Observation.subject).where(
            Observation.user_id == user_id, Observation.deleted_at.is_(None),
            Observation.subject != "owner",
        ).distinct())).scalars().all())
    for subject in subjects:
        name = subject.split(":", 1)[-1].strip()
        if (subject != obs.subject and len(name) >= 4 and
                re.search(rf"(?<!\w){re.escape(name)}(?!\w)", obs.content,
                          re.IGNORECASE)):
            await add_edge(db, user_id, node, _ref("entity", subject), "mentions")

    # When an entity is first learned, connect older events that already named
    # it. This is bounded and indexed by the owner's observation partition.
    if retro_link and obs.subject != "owner":
        name = obs.subject.split(":", 1)[-1].strip()
        if len(name) >= 4:
            rows = (await db.execute(select(Observation).where(
                Observation.user_id == user_id,
                Observation.id != obs.id,
                Observation.deleted_at.is_(None),
                Observation.content.ilike(f"%{name}%"),
            ).order_by(Observation.created_at.desc()).limit(100)
              .options(defer(Observation.embedding)))).scalars().all()
            for row in rows:
                if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", row.content,
                             re.IGNORECASE):
                    await add_edge(db, user_id, _ref("observation", row.id),
                                   _ref("entity", obs.subject), "mentions")


async def index_memory_revision(db, user_id: int, revision,
                                evidence: list[dict] | None,
                                record_id: str | None) -> None:
    """Index a committed document mutation and its verified citations."""
    node = _ref("revision", revision.id)
    await add_edge(db, user_id, node, _ref("path", revision.path), "changed")
    if revision.after and revision.path.startswith(("/memories/social/", "/memories/projects/")):
        heading = revision.after.splitlines()[0].strip()
        match = re.fullmatch(r"#\s+(.{2,120})", heading)
        if match:
            kind = "person" if revision.path.startswith("/memories/social/") else "project"
            await add_edge(db, user_id, _ref("path", revision.path),
                           _ref("entity", f"{kind}:{match.group(1).strip()}"),
                           "describes")
    if record_id:
        await add_edge(db, user_id, _ref("record", record_id), node, "version")
        await add_edge(db, user_id, _ref("record", record_id),
                       _ref("path", revision.path), "stored_at")
    for item in evidence or []:
        ref = str(item.get("ref") or "")
        match = re.fullmatch(r"(message|observation):(\d+)(?:#image:\d+)?", ref)
        if match:
            target = _ref(match.group(1), match.group(2))
            await add_edge(db, user_id, node, target, "evidenced_by", target)


async def neighborhood(db, user_id: int, start: str, *, depth: int = 2,
                       max_edges: int = 24) -> list[MemoryGraphEdge]:
    """Bounded bidirectional graph traversal. Caller validates the start node."""
    depth = min(max(int(depth), 1), 3)
    max_edges = min(max(int(max_edges), 1), 60)
    frontier = {start}
    seen_nodes = {start}
    found = []
    seen_edges = set()
    for _ in range(depth):
        if not frontier or len(found) >= max_edges:
            break
        rows = (await db.execute(select(MemoryGraphEdge).where(
            MemoryGraphEdge.user_id == user_id,
            or_(MemoryGraphEdge.source_ref.in_(frontier),
                MemoryGraphEdge.target_ref.in_(frontier)),
        ).order_by(MemoryGraphEdge.id.desc()).limit(max_edges * 3))).scalars().all()
        next_frontier = set()
        for edge in rows:
            if edge.id in seen_edges:
                continue
            seen_edges.add(edge.id)
            found.append(edge)
            for ref in (edge.source_ref, edge.target_ref):
                if ref not in seen_nodes and (ref != "entity:owner" or ref == start):
                    next_frontier.add(ref)
                    seen_nodes.add(ref)
            if len(found) >= max_edges:
                break
        frontier = next_frontier
    return found


async def describe_ref(db, user_id: int, ref: str) -> str | None:
    """Resolve one graph node in this user's partition; never expose another user."""
    from app.models.message import Message
    from app.models.memory_file import MemoryFile
    from app.models.memory_record_meta import MemoryRecordMeta
    from app.models.memory_revision import MemoryRevision
    from app.models.observation import Observation
    from app.models.session import Session

    kind, _, ident = ref.partition(":")
    if kind == "observation" and ident.isdigit():
        row = (await db.execute(select(Observation).where(
            Observation.user_id == user_id, Observation.id == int(ident),
            Observation.deleted_at.is_(None),
        ).options(defer(Observation.embedding)))).scalar_one_or_none()
        if row:
            ended = f" [ended {row.valid_until}]" if row.valid_until else ""
            return f"[{ref}] {row.content}{ended}"
    elif kind == "message" and ident.isdigit():
        row = (await db.execute(select(Message).join(Session).where(
            Session.user_id == user_id, Message.id == int(ident),
            Session.triggered_by == "user", Message.role == "user",
        ))).scalar_one_or_none()
        if row:
            from app.services.relevant_recall import _extract_text
            return f"[{ref}] Owner said: {' '.join(_extract_text(row.content).split())[:220]}"
    elif kind == "path":
        row = (await db.execute(select(MemoryFile).where(
            MemoryFile.user_id == user_id, MemoryFile.path == ident,
        ))).scalar_one_or_none()
        if row:
            return f"[{ref}] {' '.join(row.content.split())[:220]}"
    elif kind == "revision" and ident.isdigit():
        row = (await db.execute(select(MemoryRevision).where(
            MemoryRevision.user_id == user_id, MemoryRevision.id == int(ident),
        ))).scalar_one_or_none()
        if row:
            return f"[{ref}] {row.action} {row.path}: {' '.join(row.after.split())[:160]}"
    elif kind == "record":
        row = (await db.execute(select(MemoryRecordMeta).where(
            MemoryRecordMeta.user_id == user_id, MemoryRecordMeta.record_id == ident,
        ))).scalar_one_or_none()
        if row:
            return f"[{ref}] {row.kind} {row.category} {row.occurred_on or row.period}"
        # Older records have a revision ID but predate the metadata envelope.
        revision = (await db.execute(select(MemoryRevision).where(
            MemoryRevision.user_id == user_id, MemoryRevision.record_id == ident,
        ).order_by(MemoryRevision.id.desc()).limit(1))).scalar_one_or_none()
        if revision:
            return f"[{ref}] {revision.path}"
    elif kind == "entity":
        from app.models.observation import Observation
        exists = (await db.execute(select(Observation.id).where(
            Observation.user_id == user_id, Observation.subject == ident,
            Observation.deleted_at.is_(None),
        ).limit(1))).scalar_one_or_none()
        if exists:
            return f"[{ref}]"
        paths = (await db.execute(select(MemoryGraphEdge.source_ref).where(
            MemoryGraphEdge.user_id == user_id,
            MemoryGraphEdge.target_ref == ref,
            MemoryGraphEdge.relation_type == "describes",
        ).limit(3))).scalars().all()
        for path_ref in paths:
            if await describe_ref(db, user_id, path_ref):
                return f"[{ref}]"
    return None


async def build_context(db, user_id: int, start: str, *, depth: int = 2,
                        max_edges: int = 24) -> str:
    """Verified, bounded context for an addressable memory node."""
    if await describe_ref(db, user_id, start) is None:
        return f"No accessible memory node {start}."
    edges = await neighborhood(db, user_id, start, depth=depth, max_edges=max_edges)
    if not edges:
        return f"{await describe_ref(db, user_id, start)}\nNo recorded links."
    refs = {start}
    for edge in edges:
        refs.update((edge.source_ref, edge.target_ref))
    labels = {ref: await describe_ref(db, user_id, ref) for ref in refs}
    lines = ["Connected memory (edge labels describe provenance or association, not causation):"]
    for ref in sorted(refs):
        if labels[ref]:
            lines.append(labels[ref])
    lines.append("Connections:")
    for edge in edges:
        if labels[edge.source_ref] and labels[edge.target_ref]:
            lines.append(f"{edge.source_ref} --{edge.relation_type}--> {edge.target_ref}")
    return "\n".join(lines)[:6000]


async def rebuild_graph_for_user(user_id: int, *, batch_size: int = 100) -> dict:
    """One-time, resumable no-model backfill for pre-graph facts and revisions.

    Each batch commits independently. The existence of its primary edge is the
    checkpoint, so a crash resumes missing units instead of repeating the run.
    """
    import asyncio
    from sqlalchemy import cast, exists, String

    from app.database import AsyncSessionLocal
    from app.models.memory_revision import MemoryRevision
    from app.models.memory_write_receipt import MemoryWriteReceipt
    from app.models.observation import Observation
    from app.services.memory_states import version

    batch_size = min(max(batch_size, 1), 200)
    counts = {"observations": 0, "revisions": 0}
    async with AsyncSessionLocal() as db:
        known_subjects = set((await db.execute(select(Observation.subject).where(
            Observation.user_id == user_id, Observation.deleted_at.is_(None),
            Observation.subject != "owner",
        ).distinct())).scalars().all())
    while True:
        async with AsyncSessionLocal() as db:
            about = MemoryGraphEdge.__table__.alias("about")
            missing = ~exists(select(1).select_from(about).where(
                about.c.user_id == user_id,
                about.c.source_ref == ("observation:" + cast(Observation.id, String)),
                about.c.relation_type == "about",
            ))
            rows = (await db.execute(select(Observation).where(
                Observation.user_id == user_id, Observation.deleted_at.is_(None),
                missing,
            ).order_by(Observation.id).limit(batch_size)
              .options(defer(Observation.embedding)))).scalars().all()
            for obs in rows:
                await index_observation(db, user_id, obs,
                                        known_subjects=known_subjects,
                                        retro_link=False)
            if rows:
                await db.commit()
                counts["observations"] += len(rows)
        if not rows:
            break
        await asyncio.sleep(0)

    while True:
        async with AsyncSessionLocal() as db:
            changed = MemoryGraphEdge.__table__.alias("changed")
            missing = ~exists(select(1).select_from(changed).where(
                changed.c.user_id == user_id,
                changed.c.source_ref == ("revision:" + cast(MemoryRevision.id, String)),
                changed.c.relation_type == "changed",
            ))
            rows = (await db.execute(select(MemoryRevision).where(
                MemoryRevision.user_id == user_id, missing,
            ).order_by(MemoryRevision.id).limit(batch_size))).scalars().all()
            for revision in rows:
                receipt = (await db.execute(select(MemoryWriteReceipt).where(
                    MemoryWriteReceipt.user_id == user_id,
                    MemoryWriteReceipt.path == revision.path,
                    MemoryWriteReceipt.after_hash == version(revision.after),
                ).order_by(MemoryWriteReceipt.id.desc()).limit(1))).scalar_one_or_none()
                await index_memory_revision(
                    db, user_id, revision, receipt.evidence if receipt else [],
                    revision.record_id,
                )
            if rows:
                await db.commit()
                counts["revisions"] += len(rows)
        if not rows:
            break
        await asyncio.sleep(0)
    return counts
