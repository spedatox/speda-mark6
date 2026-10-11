"""Owner-scoped, live-authorized bounded evidence retrieval.

The catalog owns authority; Igor owns derived passages. Library material is
untrusted source content and never becomes instructions or personal memory.
"""
import datetime as dt
import hashlib
import hmac
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import bindparam, or_, select, text

from app.core.context import AgentContext
from app.models.library import LibraryDocument, LibraryRevision, LibraryIndexGeneration, LibraryPassage, LibraryCitation
from app.schemas.library import (
    LibraryListResult, LibraryListResponse, LibrarySearchResult, LibrarySearchResponse, LibraryReadResponse,
)
from app.services.lexical import build_match_query, fold


class LibraryService:
    def __init__(self, indexer, *, n8n_secret=""):
        self.indexer = indexer
        self.client = indexer.client
        self.sessions = indexer.sessions
        self.n8n_secret = n8n_secret

    def _owner(self, context):
        if context.user_id != self.indexer.owner_user_id:
            raise HTTPException(403, "Hall of Records belongs to a different owner")

    async def _catalog(self, context, *, category=None, doc_type=None, include_archived=False, document_ids=None):
        self._owner(context)
        if not self.client.configured:
            raise HTTPException(503, "Hall of Records is not configured")
        async with self.sessions() as db:
            query = select(LibraryDocument).where(LibraryDocument.source_id == self.indexer.source_id)
            if document_ids is not None:
                query = query.where(LibraryDocument.id.in_(document_ids))
            ids = list((await db.scalars(query.order_by(LibraryDocument.id).limit(2001))).all())
        if len(ids) > 2000:
            raise HTTPException(422, "Narrow the search to document IDs; catalog exceeds 2000 entries")
        records = []
        try:
            for start in range(0, len(ids), 100):
                response = await self.client.resolve_access([d.id for d in ids[start:start+100]], context.agent_id)
                for item in response["items"]:
                    if not item["access_granted"]:
                        continue
                    record = item.get("record")
                    if not record:
                        raise ValueError("Hisar catalog response is missing authoritative metadata")
                    doc = record["document"]
                    if doc["is_tombstone"] or (doc["is_archived"] and not include_archived):
                        continue
                    if category and doc["category"] != category:
                        continue
                    if doc_type and doc["type"] != doc_type:
                        continue
                    records.append(record)
        except Exception as exc:
            raise HTTPException(503, "Hall of Records authority is unavailable; retry after synchronization") from exc
        return records

    async def _status(self, db, revision_id):
        if not revision_id:
            return "no_edition"
        generation = await db.get(LibraryIndexGeneration, self.indexer.generation_id(revision_id))
        return generation.status if generation else "pending"

    async def list_documents(self, context, category=None, doc_type=None, include_archived=False, *, offset=0, limit=30):
        records = await self._catalog(context, category=category, doc_type=doc_type, include_archived=include_archived)
        records.sort(key=lambda r: fold(r["document"]["title"]))
        selected = records[offset:offset + limit]
        results = []
        async with self.sessions() as db:
            for record in selected:
                doc, rev = record["document"], record.get("active_revision") or {}
                results.append(LibraryListResult(
                    id=doc["id"], title=doc["title"], category=doc["category"], type=doc["type"],
                    active_revision_id=doc["active_revision_id"], index_status=await self._status(db, doc["active_revision_id"]),
                    issue_date=rev.get("issue_date"), coverage_start=rev.get("coverage_start"), coverage_end=rev.get("coverage_end"),
                ))
        return LibraryListResponse(documents=results, has_more=offset + limit < len(records))

    async def _evidence(self, db, passage, doc, rev, *, score=0, historical=False):
        # Deterministic receipts make read-only retrieval idempotent under parallel calls.
        digest = hashlib.sha256(passage.content.encode()).hexdigest()
        citation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.indexer.source_id}:{passage.id}:{digest}"))
        # A concurrent read can create the same citation; conflict-safe insert is portable.
        if db.bind.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        elif db.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            raise HTTPException(503, "Unsupported citation store")
        await db.execute(insert(LibraryCitation).values(
            id=citation_id, passage_id=passage.id, excerpt_hash=digest,
        ).on_conflict_do_nothing(index_elements=["id"]))
        return LibrarySearchResult(
            passage_id=passage.id, document_title=doc["title"], document_type=doc["type"],
            document_id=doc["id"], revision_id=rev.id, content=passage.content, page=passage.page,
            section=passage.section, issue_date=rev.issue_date, coverage_start=rev.coverage_start,
            coverage_end=rev.coverage_end, score=score, citation_id=citation_id,
            citation_url=f"/library/sources/{citation_id}", historical=historical,
        )

    async def search(self, context, query_text, doc_ids=None, *, category=None, doc_type=None,
                     on_date=None, revision_id=None, limit=8):
        today = dt.datetime.now(ZoneInfo(context.timezone)).date().isoformat()
        query_date = on_date or today
        if on_date:
            dt.date.fromisoformat(on_date)
        match = build_match_query(query_text)
        if not match:
            return LibrarySearchResponse(status="empty_query", query_date=query_date)
        records = await self._catalog(context, category=category, doc_type=doc_type,
                                      document_ids=doc_ids, include_archived=bool(revision_id))
        warnings = []
        eligible = {}
        async with self.sessions() as db:
            for record in records:
                doc = record["document"]
                rid = revision_id or doc["active_revision_id"]
                if not rid:
                    continue
                rev = await db.get(LibraryRevision, rid)
                if rev is None or rev.document_id != doc["id"]:
                    warnings.append(f"{doc['title']}: edition has not synchronized")
                    continue
                # Period-bound records are filtered using authoritative edition metadata.
                current = record.get("active_revision") or {}
                dates = current if not revision_id else {"coverage_start": rev.coverage_start, "coverage_end": rev.coverage_end}
                if on_date or (not revision_id and (dates.get("coverage_start") or dates.get("coverage_end"))):
                    start, end = dates.get("coverage_start"), dates.get("coverage_end")
                    if not start or not end:
                        warnings.append(f"{doc['title']}: coverage dates are missing")
                        continue
                    if not start <= query_date <= end:
                        warnings.append(f"{doc['title']}: edition does not cover {query_date}")
                        continue
                status = await self._status(db, rid)
                if status != "ready_keyword":
                    warnings.append(f"{doc['title']}: {status}")
                    continue
                eligible[rid] = (doc, rev)
            if not eligible:
                return LibrarySearchResponse(status="unavailable" if warnings else "no_matches",
                                             query_date=query_date, warnings=warnings)
            generations = [self.indexer.generation_id(rid) for rid in eligible]
            scores = {}
            if db.bind.dialect.name == "sqlite":
                statement = text(
                    "SELECT passage_id, bm25(library_passages_fts,0,1,2,0,0,0) AS rank "
                    "FROM library_passages_fts WHERE library_passages_fts MATCH :match "
                    "AND revision_id IN :revisions AND generation_id IN :generations "
                    "ORDER BY rank, passage_id LIMIT :limit"
                ).bindparams(bindparam("revisions", expanding=True), bindparam("generations", expanding=True))
                rows = (await db.execute(statement, {"match": match, "revisions": list(eligible),
                                                    "generations": generations, "limit": limit})).all()
                passage_ids = [r[0] for r in rows]
                scores = {r[0]: -float(r[1]) for r in rows}
                passages = list((await db.scalars(select(LibraryPassage).where(
                    LibraryPassage.id.in_(passage_ids), LibraryPassage.needs_review.is_(False),
                ))).all())
                passages.sort(key=lambda p: passage_ids.index(p.id))
                mode = "keyword_bm25"
            else:
                # Honest bounded literal fallback; vector retrieval is not claimed.
                terms = [t.rstrip("*") for t in match.split(" OR ")][:12]
                passages = list((await db.scalars(select(LibraryPassage).where(
                    LibraryPassage.revision_id.in_(eligible),
                    LibraryPassage.generation_id.in_(generations),
                    LibraryPassage.needs_review.is_(False),
                    or_(*(LibraryPassage.content.ilike(f"%{t}%", escape="\\") for t in terms)),
                ).order_by(LibraryPassage.id).limit(limit))).all())
                mode = "literal_fallback"
                warnings.append("BM25 is unavailable on this database; literal search only")
            results = []
            for passage in passages:
                doc, rev = eligible[passage.revision_id]
                results.append(await self._evidence(db, passage, doc, rev, score=scores.get(passage.id, 0),
                                                   historical=rev.id != doc["active_revision_id"]))
            await db.commit()
        return LibrarySearchResponse(status="success" if results else "no_matches",
                                     query_date=query_date, search_mode=mode, results=results, warnings=warnings)

    async def read_passage(self, context, document_id, page=None, *, revision_id=None, offset=0, limit=8):
        records = await self._catalog(context, document_ids=[document_id], include_archived=bool(revision_id))
        if not records:
            return LibraryReadResponse(document_id=document_id, status="not_found")
        doc = records[0]["document"]
        rid = revision_id or doc["active_revision_id"]
        async with self.sessions() as db:
            status = await self._status(db, rid)
            rev = await db.get(LibraryRevision, rid) if rid else None
            if rev is None or rev.document_id != document_id:
                return LibraryReadResponse(document_id=document_id, revision_id=rid,
                                           status="pending" if rid == doc["active_revision_id"] else "not_found",
                                           warnings=["Edition metadata has not synchronized"])
            if status != "ready_keyword":
                return LibraryReadResponse(document_id=document_id, revision_id=rid, status=status)
            query = select(LibraryPassage).where(
                LibraryPassage.revision_id == rid, LibraryPassage.generation_id == self.indexer.generation_id(rid),
                LibraryPassage.needs_review.is_(False),
            )
            if page is not None:
                query = query.where(LibraryPassage.page == page)
            passages = list((await db.scalars(query.order_by(LibraryPassage.position, LibraryPassage.id)
                                              .offset(offset).limit(limit+1))).all())
            results = [await self._evidence(db, p, doc, rev, historical=rid != doc["active_revision_id"])
                       for p in passages[:limit]]
            await db.commit()
        return LibraryReadResponse(document_id=document_id, revision_id=rid, status="success" if results else "no_matches",
                                   passages=[r.content for r in results], citation_ids=[r.citation_id for r in results],
                                   results=results, next_offset=offset+limit if len(passages) > limit else None)

    async def citation_source(self, citation_id):
        async with self.sessions() as db:
            citation = await db.get(LibraryCitation, citation_id)
            passage = await db.get(LibraryPassage, citation.passage_id) if citation else None
            revision = await db.get(LibraryRevision, passage.revision_id) if passage else None
            doc = await db.get(LibraryDocument, revision.document_id) if revision else None
            if doc is None or doc.source_id != self.indexer.source_id:
                raise HTTPException(404, "Citation not found")
            try:
                record = await self.client.get_document(doc.id)
            except Exception as exc:
                raise HTTPException(503, "Hall of Records authority is unavailable") from exc
            metadata = record["document"]
            if metadata["is_tombstone"]:
                raise HTTPException(410, "Record was withdrawn")
            if hashlib.sha256(passage.content.encode()).hexdigest() != citation.excerpt_hash:
                raise HTTPException(409, "Citation evidence failed integrity verification")
            return {"citation_id": citation.id, "document_id": doc.id, "document_title": metadata["title"],
                    "revision_id": revision.id, "issue_date": revision.issue_date,
                    "content": passage.content, "page": passage.page, "section": passage.section,
                    "historical": revision.id != metadata["active_revision_id"],
                    "archived": metadata["is_archived"]}

    async def reconcile_probe(self, secret, *, full=False):
        if not secret or not self.n8n_secret or not hmac.compare_digest(secret, self.n8n_secret):
            raise HTTPException(401, "Invalid n8n secret")
        try:
            return await self.indexer.reconcile(full=full)
        except Exception as exc:
            raise HTTPException(503, "Hall of Records synchronization failed; work remains retryable") from exc

    async def drain_jobs(self):
        from app.services.task_queue import drain
        await drain()
