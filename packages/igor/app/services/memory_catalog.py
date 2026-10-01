# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One catalog for paths, identities, record envelopes and historical sources.

Folders describe storage time, never evidence that something happened then.
Writers refresh this catalog in the same transaction as their payload. Source
capsules are immutable, searchable history; they must not be asserted as current.
"""
import re
from datetime import datetime, timezone
from sqlalchemy import select
from app.models.memory_file import MemoryFile
from app.models.memory_record_meta import MemoryRecordMeta
from app.models.memory_source import MemorySource
from app.services.memory_paths import MonthPeriod, parse_monthly_path, slugify
from app.services.memory_identity import content_hash, find_or_create_entity, generate_record_id


def legacy_collection(path: str):
    """A superseded domain monolith, excluding deliberately stable owner roots."""
    from app.services.memory_spec import collection_from_monolith
    if path in ("/memories/owner.md", "/memories/current.md", "/memories/dossier.md",
                "/memories/history.md", "/memories/patterns.md", "/memories/log.md"):
        return None
    return collection_from_monolith(path)


def retired_monoliths(files) -> set[tuple[int | None, str]]:
    """One owner's replacement never hides a different owner's source.

    Partial legacy content is still retrievable by exact path/source; removing
    a duplicate active authority does not claim complete semantic reconciliation.
    """
    files = list(files)
    def value(row, key, default=None):
        return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)
    paths = {(value(row, "user_id"), value(row, "path")) for row in files}
    return {(user, path) for user, path in paths if (coll := legacy_collection(path))
            and any(other_user == user and target.startswith(coll.root + "/") and "/." not in target
                    for other_user, target in paths)}


def active_corpus_files(files):
    """Read projection only; never delete or mutate a legacy ORM payload."""
    files = list(files)
    retired = retired_monoliths(files)
    return [file for file in files if "/." not in file.path
            and (getattr(file, "user_id", None), file.path) not in retired]


def canonical_path(path: str, recorded_at: str, content: str | None = None) -> str:
    """Recognize every shipped layout; change addresses, never claim dates."""
    state = re.fullmatch(r"/memories/states/(?:\d{2}-\d{2}/)?([^/]+\.md)", path)
    if state:
        return "/memories/states/" + state[1]
    legacy_ledger = re.fullmatch(r"/memories/finance/ledger/(\d{4}-\d{2})\.md", path)
    if legacy_ledger:
        if content is not None and "<!-- finance-projection-v1 -->" not in content:
            return "/memories/finance/legacy/"+legacy_ledger[1]+".md"
        period = MonthPeriod.from_canonical(legacy_ledger[1])
        return f"/memories/finance/{period.folder_name}/ledger.md"
    monthly = parse_monthly_path(path)
    if monthly and monthly.category == "finance":
        if monthly.slug.startswith("record-"):
            return "/memories/finance/records/"+monthly.slug.removeprefix("record-")+".md"
        if monthly.slug in ("balances", "reports", "monthly-structure"):
            return "/memories/finance/"+monthly.slug+".md"
        if monthly.slug == "ledger" and content is not None and "<!-- finance-projection-v1 -->" not in content:
            return "/memories/finance/legacy/"+monthly.period.canonical+".md"
    if monthly or path.count("/") == 2:
        return path
    if path.startswith("/memories/academic/courses/"):
        return path
    project = re.fullmatch(r"/memories/projects/([^/]+)/(\d{2}-\d{2})\.md", path)
    if project:
        return f"/memories/projects/{project[2]}/{project[1]}.md"
    social = re.fullmatch(r"/memories/social/(personal|professional)/([^/]+)/(\d{2}-\d{2})\.md", path)
    if social:
        return f"/memories/social/{social[3]}/{social[1]}/{social[2]}.md"
    # Stable finance record IDs and generated legacy/view paths are not topics.
    if path.startswith(("/memories/finance/records/", "/memories/finance/ledger/", "/memories/finance/legacy/")):
        return path
    period = MonthPeriod.from_date(datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).date()).folder_name
    flat_social = re.fullmatch(r"/memories/social/(personal|professional)/([^/]+\.md)", path)
    if flat_social:
        return f"/memories/social/{period}/{flat_social[1]}/{flat_social[2]}"
    flat = re.fullmatch(r"/memories/([^/.]+)/([^/]+\.md)", path)
    if flat:
        return f"/memories/{flat[1]}/{period}/{flat[2]}"
    raise ValueError(f"Unrecognized layout requires an explicit mapping: {path}")


def document_identity(path: str, content: str) -> tuple[str, str, str] | None:
    """Entity names come from a document, not a guessed association."""
    category = path.split("/")[2] if path.startswith("/memories/") else ""
    if category not in ("social", "projects"):
        return None
    heading = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if not heading:
        return None
    return category, "person" if category == "social" else "project", heading[1].strip()


def declared_aliases(name: str, content: str) -> list[str]:
    """Only aliases explicitly printed in the heading or description."""
    aliases = []
    short = re.split(r"\s+[—–]\s+|\s+\(", name, maxsplit=1)[0]
    if short != name and len(short)>=4:
        aliases.append(short)
    acronym = re.match(r"^[A-Z](?:\.[A-Z]){2,}\.?", name)
    if acronym:
        aliases.append(acronym[0].replace(".", ""))
    intro = content.split("**Events:**",1)[0].split("## Log",1)[0]
    for match in re.finditer(r"\bcodename\s+[\"`]?([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})", intro):
        aliases.append(match[1])
    return sorted(set(a for a in aliases if slugify(a)!=slugify(name)))


async def maintain_record(db, user_id: int, file: MemoryFile, record_id: str | None = None):
    """Refresh metadata after a successful write, without owning its commit."""
    from app.services.memory_identity import get_entity_head, next_edition_seq, update_entity_head
    if "/." in file.path:
        return None
    meta = (await db.execute(select(MemoryRecordMeta).where(
        MemoryRecordMeta.user_id == user_id, MemoryRecordMeta.memory_file_id == file.id,
    ))).scalar_one_or_none()
    identity = document_identity(file.path, file.content)
    entity_id = None
    if identity:
        category, kind, name = identity
        entity_id, _ = await find_or_create_entity(db, user_id, category, kind, name)
        from app.models.memory_entity import MemoryEntity
        entity = await db.get(MemoryEntity,entity_id)
        previous = entity.aliases if isinstance(entity.aliases,list) else []
        entity.aliases = sorted(set(previous+declared_aliases(name,file.content)))
    monthly = parse_monthly_path(file.path)
    category = file.path.split("/")[2].removesuffix(".md")
    if meta is None:
        # Explicit capture envelopes are installed by the capture caller.
        if record_id:
            return None
        meta = MemoryRecordMeta(memory_file_id=file.id, user_id=user_id,
            record_id=generate_record_id(), category=category,
            kind="state" if category == "states" else "reference",
            period=monthly.period.canonical if monthly else file.updated_at.strftime("%Y-%m"),
            period_basis="recorded_at", date_precision="unknown",
            recorded_at=file.updated_at, schema_version=3, version=1,
            content_hash=content_hash(file.content))
        db.add(meta)
    elif meta.content_hash != content_hash(file.content):
        meta.version += 1
        meta.content_hash = content_hash(file.content)
    meta.entity_id = entity_id or meta.entity_id
    if entity_id:
        if meta.edition_seq is None:
            meta.edition_seq = await next_edition_seq(db, user_id, entity_id)
        await db.flush()
        head, version = await get_entity_head(db, user_id, entity_id)
        # A historical edition must never displace a newer current edition.
        old = (await db.execute(select(MemoryRecordMeta).where(
            MemoryRecordMeta.user_id == user_id, MemoryRecordMeta.record_id == head,
        ))).scalar_one_or_none() if head else None
        if old is None or (meta.period, meta.recorded_at, meta.record_id) >= (old.period, old.recorded_at, old.record_id):
            if head != meta.record_id and not await update_entity_head(db, user_id, entity_id, meta.record_id, version):
                raise ValueError("Entity head changed concurrently; reread before retrying.")
    await db.flush()
    return meta


async def source_for_path(db, user_id: int, path: str):
    return (await db.execute(select(MemorySource).where(
        MemorySource.user_id == user_id, MemorySource.original_path == path,
    ).order_by(MemorySource.created_at.desc()).limit(1))).scalar_one_or_none()


async def document_warnings(db, user_id: int, path: str) -> str:
    from app.models.memory_source import MemoryIssue
    from sqlalchemy import String, cast
    ref = "path:"+path
    rows = (await db.execute(select(MemoryIssue).where(
        MemoryIssue.user_id == user_id, MemoryIssue.status == "open",
        cast(MemoryIssue.refs, String).icontains(ref, autoescape=True),
    ).limit(8))).scalars().all()
    details = [row.detail for row in rows if ref in row.refs]
    return ("Unresolved source discrepancies — preserve both accounts:\n"+"\n".join(details))[:850] if details else ""


async def resolve_member(db, user_id: int, coll, name: str, group: str | None = None) -> str:
    """Reuse a current identity before creating a new monthly document."""
    from app.services.memory_spec import member_path
    from app.services.memory_store import resolve_alias
    from app.models.memory_entity_head import MemoryEntityHead
    from app.services.memory_identity import resolve_entity_by_name
    from app.services.memory_paths import build_monthly_path
    legacy = member_path(coll, name, group)
    category = coll.root.rsplit("/", 1)[-1]
    entity_id = await resolve_entity_by_name(db, user_id, category, name)
    if entity_id:
        current = (await db.execute(select(MemoryFile.path).join(
            MemoryRecordMeta, MemoryRecordMeta.memory_file_id == MemoryFile.id,
        ).join(MemoryEntityHead, MemoryEntityHead.current_edition_id == MemoryRecordMeta.record_id).where(
            MemoryEntityHead.user_id == user_id, MemoryEntityHead.entity_id == entity_id,
            MemoryRecordMeta.user_id == user_id, MemoryFile.user_id == user_id,
        ))).scalar_one_or_none()
        if current:
            return current
    redirected = await resolve_alias(db, user_id, legacy)
    if redirected:
        return redirected
    files = (await db.execute(select(MemoryFile).where(
        MemoryFile.user_id == user_id, MemoryFile.path.startswith(coll.root + "/"),
    ))).scalars().all()
    candidates = [file for file in files if document_identity(file.path, file.content)
                  and slugify(document_identity(file.path, file.content)[2]) == slugify(name)]
    if len(candidates) == 1:
        return candidates[0].path
    if len(candidates) > 1:
        raise ValueError("Multiple editions lack a current entity head. Reconcile the catalog; nothing was written.")
    return build_monthly_path(category, MonthPeriod.current(), slugify(name),
                              slugify(group) if group else None)


async def search_sources(db, user_id: int, query: str, *, limit: int = 6) -> str:
    """Bounded, literal fallback over history. No embedding or provider calls."""
    words = re.findall(r"[\w-]{3,}", query)[:6]
    if not words:
        return "Supply at least one specific name or topic (3+ characters)."
    from sqlalchemy import or_
    filters = [MemorySource.content.icontains(word, autoescape=True) for word in words]
    rows = (await db.execute(select(MemorySource).where(
        MemorySource.user_id == user_id, MemorySource.classification != "system",
        or_(*filters),
    ).order_by(MemorySource.created_at.desc(), MemorySource.id).limit(min(limit, 6)))).scalars().all()
    # Collapse byte-identical archives, but keep every source in storage.
    seen, lines = set(), ["Immutable sources — historical originals may be outdated; owner confirmations are separately labelled:"]
    for row in rows:
        if row.content_hash in seen:
            continue
        seen.add(row.content_hash)
        pos = next((row.content.casefold().find(w.casefold()) for w in words if w.casefold() in row.content.casefold()), 0)
        label = "owner confirmation" if row.classification == "owner_confirmation" else "source document" if row.classification == "document_attachment" else "historical original"
        lines.append(f"source:{row.id} [{label}] {row.original_path}\n{row.content[max(pos-100,0):pos+550]}")
    return "\n\n".join(lines)[:4200]
