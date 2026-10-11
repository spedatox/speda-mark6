"""Replayable synchronization and durable indexing for the Hall of Records.

Delivery cursors acknowledge queued work, never completion. No polling scheduler.
"""
import asyncio
import datetime as dt
import hashlib
import logging
import uuid

from sqlalchemy import delete, func, select, text

from app.models.library import (
    LibrarySource, LibraryDocument, LibraryRevision, LibraryIndexGeneration, LibraryPassage, LibraryEmbedding,
)
from app.services.library_extract import ExtractionError, extract_document_bytes
from app.services.lexical import fold

logger = logging.getLogger(__name__)


class LibraryIndexer:
    fingerprint = "iacon-extract-v3"

    def __init__(self, client, session_factory, *, owner_user_id=1, max_pages=200, enqueue=None,
                 ocr_factory=None, ocr_fingerprint="off", embedder=None,
                 semantic_max_passages=5000, semantic_min_similarity=0.25):
        self.client = client
        self.sessions = session_factory
        self.owner_user_id = owner_user_id
        self.max_pages = max_pages
        self.source_id = f"owner:{owner_user_id}:" + hashlib.sha256(client.hisar_url.encode()).hexdigest()[:16]
        self.enqueue = enqueue
        self.ocr_factory = ocr_factory
        self.fingerprint = f"iacon-extract-v3:ocr={ocr_fingerprint}"
        self.embedder = embedder
        self.semantic_max_passages = semantic_max_passages
        self.semantic_min_similarity = semantic_min_similarity
        self.lock = asyncio.Lock()

    def generation_id(self, revision_id):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.source_id}:{revision_id}:{self.fingerprint}"))

    async def ensure_keyword_index(self, db):
        if db.bind.dialect.name != "sqlite":
            return False
        await db.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS library_passages_fts USING fts5("
            "passage_id UNINDEXED, content, title, document_id UNINDEXED, "
            "revision_id UNINDEXED, generation_id UNINDEXED, tokenize='unicode61')"
        ))
        return True

    async def _mirror(self, record):
        metadata = record["document"]
        async with self.sessions() as db:
            source = await db.get(LibrarySource, self.source_id)
            if source is None:
                db.add(LibrarySource(id=self.source_id, hisar_url=self.client.hisar_url))
                await db.flush()
            doc = await db.scalar(select(LibraryDocument).where(LibraryDocument.id == metadata["id"]).with_for_update())
            if doc is not None and doc.source_id != self.source_id:
                # The previous single-owner implementation used a literal default
                # source. Adopt only that legacy owner's authoritative UUIDs;
                # retained sources, generations and originals are not deleted.
                if doc.source_id == "default" and self.owner_user_id == 1:
                    doc.source_id = self.source_id
                else:
                    raise ValueError("Catalog document identity belongs to a different source")
            if doc is None:
                doc = LibraryDocument(id=metadata["id"], source_id=self.source_id)
                db.add(doc)
            if (doc.metadata_version or 0) > metadata["metadata_version"]:
                return
            for key in ("title", "aliases", "category", "institution", "subject", "active_revision_id",
                        "access_policy", "is_archived", "is_tombstone", "metadata_version"):
                setattr(doc, key, metadata.get(key))
            doc.doc_type = metadata.get("type")
            for key in ("created_at", "updated_at"):
                setattr(doc, key, dt.datetime.fromisoformat(metadata[key]).replace(tzinfo=None))
            if await self.ensure_keyword_index(db):
                await db.execute(text("UPDATE library_passages_fts SET title=:title WHERE document_id=:id"),
                                 {"title": fold(doc.title), "id": doc.id})
            await db.flush()
            for revision in record.get("revisions", []):
                rev = await db.get(LibraryRevision, revision["id"])
                if rev is None:
                    fields = dict(revision)
                    fields["created_at"] = dt.datetime.fromisoformat(fields["created_at"]).replace(tzinfo=None)
                    db.add(LibraryRevision(**fields))
                else:
                    if rev.document_id != doc.id or rev.blob_sha256 != revision["blob_sha256"]:
                        raise ValueError("Immutable revision identity changed")
                    for key in ("sequence", "original_filename", "mime_type", "size_bytes",
                                "issue_date", "coverage_start", "coverage_end", "term"):
                        if getattr(rev, key) != revision.get(key):
                            raise ValueError("Immutable revision metadata changed")
                    rev.superseded_by = revision.get("superseded_by")
            await db.commit()

    async def _accept(self, record, *, retry=False):
        await self._mirror(record)
        doc = record["document"]
        queued = 0
        if doc["is_tombstone"] or doc["is_archived"]:
            return queued
        for revision in record.get("revisions", []):
            generation_id = self.generation_id(revision["id"])
            async with self.sessions() as db:
                generation = await db.get(LibraryIndexGeneration, generation_id)
                if generation and retry and revision["id"] == doc["active_revision_id"]:
                    generation.status = "pending"
                    await db.commit()
                if generation and generation.status in {"ready_keyword", "ready_ocr", "needs_review"}:
                    if generation.status in {"ready_keyword", "ready_ocr"}:
                        queued += await self._queue_embeddings(doc["id"], revision["id"])
                    continue
                if generation is None:
                    db.add(LibraryIndexGeneration(id=generation_id, revision_id=revision["id"],
                                                  status="pending", fingerprint=self.fingerprint))
                    await db.commit()
            enqueue = self.enqueue
            if enqueue is None:
                from app.services.task_queue import enqueue_payload_job
                enqueue = enqueue_payload_job
            job = await enqueue(kind="library_index", unique_key=generation_id,
                                user_id=self.owner_user_id,
                                payload={"document_id": doc["id"], "revision_id": revision["id"],
                                         "source_id": self.source_id})
            queued += int(job is not None)
        return queued

    async def reconcile(self, *, full=False):
        if not self.client.configured:
            return {"status": "unconfigured", "events": 0, "queued": 0}
        async with self.lock:
            async with self.sessions() as db:
                await self.ensure_keyword_index(db)
                source = await db.get(LibrarySource, self.source_id)
                if source is None:
                    source = LibrarySource(id=self.source_id, hisar_url=self.client.hisar_url, sync_cursor=0)
                    db.add(source)
                await db.commit()
                cursor = source.sync_cursor
            queued = events_count = 0
            if full:
                offset = 0
                while True:
                    manifest = await self.client.get_manifest(offset=offset)
                    for record in manifest["records"]:
                        queued += await self._accept(record)
                    if not manifest["has_more"]:
                        break
                    offset = manifest["next_offset"]
            # Replay includes metadata/revocation/archive/restore changes, not just new bytes.
            while True:
                data = await self.client.get_events(cursor)
                events = data["events"]
                if data.get("has_more") and not events:
                    raise ValueError("Catalog event feed did not advance")
                for event in events:
                    if event["sequence"] <= cursor:
                        raise ValueError("Catalog event sequence did not advance")
                    record = await self.client.get_document(event["document_id"])
                    queued += await self._accept(record, retry=event["event_type"] == "index_requested")
                    cursor = event["sequence"]
                    async with self.sessions() as db:
                        source = await db.get(LibrarySource, self.source_id)
                        source.sync_cursor = cursor
                        await db.commit()
                    events_count += 1
                if not data["has_more"]:
                    break
            return {"status": "accepted", "events": events_count, "queued": queued, "cursor": cursor}

    async def index_revision(self, document_id, revision_id):
        record = await self.client.get_document(document_id)
        await self._mirror(record)
        metadata = record["document"]
        revision = next((r for r in record.get("revisions", []) if r["id"] == revision_id), None)
        if revision is None:
            raise ValueError("Revision is absent from its authoritative document")
        if metadata["is_tombstone"] or metadata["is_archived"]:
            return
        generation_id = self.generation_id(revision_id)
        async with self.sessions() as db:
            generation = await db.get(LibraryIndexGeneration, generation_id)
            if generation and generation.status in {"ready_keyword", "ready_ocr", "needs_review"}:
                # Receipt delivery is retryable independently of extraction.
                await self.client.report_index_receipt(document_id, revision_id, generation.status)
                if generation.status in {"ready_keyword", "ready_ocr"}:
                    await self._queue_embeddings(document_id, revision_id)
                return
            if generation is None:
                generation = LibraryIndexGeneration(id=generation_id, revision_id=revision_id,
                                                    fingerprint=self.fingerprint, status="pending")
                db.add(generation)
            generation.status = "processing"
            await db.commit()
        try:
            await self.client.report_index_receipt(document_id, revision_id, "processing")
            blob = await self.client.download_revision_blob(revision_id)
            if len(blob) != revision["size_bytes"] or hashlib.sha256(blob).hexdigest() != revision["blob_sha256"]:
                raise ValueError("Original failed size or SHA-256 verification")
            passages = await asyncio.to_thread(
                extract_document_bytes, blob, revision["mime_type"],
                max_bytes=self.client.max_bytes, max_pages=self.max_pages,
                ocr=self.ocr_factory() if self.ocr_factory else None,
            )
            # All evidence + readiness publish together. Retry uses stable passage identities.
            async with self.sessions() as db:
                keyword = await self.ensure_keyword_index(db)
                retained_count = await db.scalar(select(func.count()).select_from(LibraryPassage).where(
                    LibraryPassage.generation_id == generation_id))
                if retained_count and retained_count != len(passages):
                    raise ValueError("Published extraction changed; use a new extractor fingerprint to retain its citations")
                for number, extracted in enumerate(passages):
                    passage_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{generation_id}:{number}"))
                    passage = await db.get(LibraryPassage, passage_id)
                    if passage is not None and (passage.content != extracted.content or passage.page != extracted.page
                                                or passage.section != extracted.section or passage.needs_review != extracted.needs_review):
                        raise ValueError("Published extraction changed; use a new extractor fingerprint to retain its citations")
                    if passage is None:
                        passage = LibraryPassage(id=passage_id, generation_id=generation_id, revision_id=revision_id)
                        db.add(passage)
                    passage.content = extracted.content
                    passage.position = number
                    passage.page = extracted.page
                    passage.section = extracted.section
                    passage.needs_review = extracted.needs_review
                    passage.coverage_start = revision.get("coverage_start")
                    passage.coverage_end = revision.get("coverage_end")
                    await db.execute(delete(LibraryEmbedding).where(LibraryEmbedding.passage_id == passage_id))
                    if keyword:
                        await db.execute(text("DELETE FROM library_passages_fts WHERE passage_id=:id"), {"id": passage_id})
                        await db.execute(text(
                            "INSERT INTO library_passages_fts(passage_id,content,title,document_id,revision_id,generation_id) "
                            "VALUES(:id,:content,:title,:doc,:rev,:gen)"
                        ), {"id": passage_id, "content": fold(extracted.content), "title": fold(metadata["title"]),
                            "doc": document_id, "rev": revision_id, "gen": generation_id})
                generation = await db.get(LibraryIndexGeneration, generation_id)
                readiness = "ready_ocr" if any(p.needs_review for p in passages) else "ready_keyword"
                generation.status = readiness
                generation.failure_details = None
                generation.completed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                await db.commit()
        except Exception as exc:
            async with self.sessions() as db:
                generation = await db.get(LibraryIndexGeneration, generation_id)
                generation.status = "needs_review" if isinstance(exc, ExtractionError) else "failed"
                generation.failure_details = str(exc)[:500] if isinstance(exc, (ExtractionError, ValueError)) else type(exc).__name__
                generation.completed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                await db.commit()
            if isinstance(exc, ExtractionError):
                await self.client.report_index_receipt(document_id, revision_id, "needs_review")
                return
            # Retry transport/integrity failures through the existing queue.
            try:
                await self.client.report_index_receipt(document_id, revision_id, "failed")
            except Exception:
                logger.warning("library_failure_receipt_pending", extra={"document_id": document_id})
            raise
        await self.client.report_index_receipt(document_id, revision_id, readiness)
        await self._queue_embeddings(document_id, revision_id)

    async def _queue_embeddings(self, document_id, revision_id):
        if self.embedder is None:
            return 0
        generation_id = self.generation_id(revision_id)
        async with self.sessions() as db:
            missing = await db.scalar(select(LibraryPassage.id).outerjoin(LibraryEmbedding).where(
                LibraryPassage.generation_id == generation_id,
                (LibraryEmbedding.passage_id.is_(None)) | (LibraryEmbedding.model_fingerprint != self.embedder.fingerprint),
            ).limit(1))
        if missing is None:
            return 0
        enqueue = self.enqueue
        if enqueue is None:
            from app.services.task_queue import enqueue_payload_job
            enqueue = enqueue_payload_job
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{generation_id}:{self.embedder.fingerprint}"))
        job = await enqueue(kind="library_embed", unique_key=key, user_id=self.owner_user_id,
                            payload={"document_id": document_id, "revision_id": revision_id,
                                     "source_id": self.source_id, "embedding_fingerprint": self.embedder.fingerprint})
        return int(job is not None)

    async def embed_revision(self, document_id, revision_id):
        if self.embedder is None:
            raise ValueError("Library semantic indexing is disabled or unconfigured")
        record = await self.client.get_document(document_id)
        if record["document"]["is_tombstone"] or record["document"]["is_archived"]:
            return
        if not any(r["id"] == revision_id for r in record["revisions"]):
            raise ValueError("Embedding edition belongs to another document")
        generation_id = self.generation_id(revision_id)
        async with self.sessions() as db:
            generation = await db.get(LibraryIndexGeneration, generation_id)
            if generation is None or generation.status not in {"ready_keyword", "ready_ocr"}:
                raise ValueError("Text extraction is not ready for semantic indexing")
            passages = list((await db.scalars(select(LibraryPassage).outerjoin(LibraryEmbedding).where(
                LibraryPassage.generation_id == generation_id,
                (LibraryEmbedding.passage_id.is_(None)) | (LibraryEmbedding.model_fingerprint != self.embedder.fingerprint),
            ).order_by(LibraryPassage.position))).all())
        # Each completed batch persists independently; a restart resumes missing vectors.
        from app.services.library_embeddings import normalized
        for offset in range(0, len(passages), 32):
            batch = passages[offset:offset + 32]
            vectors = await self.embedder.embed([p.content for p in batch])
            if len(vectors) != len(batch):
                raise ValueError("Embedding response omitted an input")
            vectors = [normalized(vector) for vector in vectors]
            if len({len(v) for v in vectors}) != 1:
                raise ValueError("Embedding dimensions changed within a batch")
            async with self.sessions() as db:
                generation = await db.get(LibraryIndexGeneration, generation_id)
                if generation.status not in {"ready_keyword", "ready_ocr"}:
                    raise ValueError("Extraction changed during semantic indexing")
                for passage, vector in zip(batch, vectors):
                    live = await db.get(LibraryPassage, passage.id)
                    if live is None or live.content != passage.content:
                        raise ValueError("Passage changed during semantic indexing")
                    embedding = await db.get(LibraryEmbedding, passage.id)
                    if embedding is None:
                        db.add(LibraryEmbedding(passage_id=passage.id, vector=vector,
                                                model_fingerprint=self.embedder.fingerprint))
                    else:
                        embedding.vector, embedding.model_fingerprint = vector, self.embedder.fingerprint
                await db.commit()

    async def aclose(self):
        await self.client.aclose()
        if self.embedder is not None:
            await self.embedder.aclose()


def indexer_from_settings():
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.services.hisar_library_client import client_from_settings
    from app.services.library_ocr import LocalOCR
    from app.services.library_embeddings import LibraryEmbeddings
    embedder = None
    if settings.library_semantic_enabled and settings.openai_api_key:
        embedder = LibraryEmbeddings(api_key=settings.openai_api_key, model=settings.embedding_model)
    ocr_factory = None
    if settings.library_ocr_enabled:
        ocr_factory = lambda: LocalOCR(languages=settings.library_ocr_languages, timeout=settings.library_ocr_timeout_seconds)
    return LibraryIndexer(client_from_settings(settings), AsyncSessionLocal,
                          owner_user_id=settings.library_owner_user_id, max_pages=settings.library_max_pages,
                          ocr_factory=ocr_factory, ocr_fingerprint=settings.library_ocr_languages if ocr_factory else "off",
                          embedder=embedder, semantic_max_passages=settings.library_semantic_max_passages,
                          semantic_min_similarity=settings.library_semantic_min_similarity)


async def run_index_job(user_id, payload):
    indexer = indexer_from_settings()
    try:
        if user_id != indexer.owner_user_id or payload.get("source_id") != indexer.source_id:
            raise ValueError("Index job belongs to a different owner or source")
        await indexer.index_revision(payload["document_id"], payload["revision_id"])
    finally:
        await indexer.aclose()


async def run_embedding_job(user_id, payload):
    indexer = indexer_from_settings()
    try:
        if (user_id != indexer.owner_user_id or payload.get("source_id") != indexer.source_id
                or indexer.embedder is None or payload.get("embedding_fingerprint") != indexer.embedder.fingerprint):
            raise ValueError("Embedding job belongs to a different owner, source or model")
        await indexer.embed_revision(payload["document_id"], payload["revision_id"])
    finally:
        await indexer.aclose()
