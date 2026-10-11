"""Behavior regressions for real Hall indexing/retrieval, isolated from owner data."""
import copy
import hashlib
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.context import AgentContext
from app.database import Base
from app.models.library import (
    LibrarySource, LibraryDocument, LibraryRevision, LibraryIndexGeneration,
    LibraryPassage, LibraryEmbedding, LibraryCitation,
)
from app.services.library import LibraryService
from app.services.library_extract import ExtractionError, chunk_text, extract_document_bytes
from app.services.library_indexer import LibraryIndexer
from app.skills.library import ListLibrarySkill, ReadLibrarySkill, SearchLibrarySkill


class Catalog:
    hisar_url = "http://isolated-hisar"
    max_bytes = 25 * 1024**2
    configured = True

    def __init__(self):
        self.records, self.blobs, self.events, self.receipts, self.queued = {}, {}, [], [], {}
        self.fail_queue = False
        self.bad_bytes = False

    def add(self, title, content, *, doc_id=None, mime="text/plain", **edition):
        doc_id = doc_id or str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        blob = content.encode() if isinstance(content, str) else content
        if doc_id not in self.records:
            self.records[doc_id] = {"document": {
                "id": doc_id, "title": title, "aliases": [], "category": "Academics", "type": "reference",
                "institution": None, "subject": None, "active_revision_id": None, "access_policy": {},
                "is_archived": False, "is_tombstone": False, "metadata_version": 1,
                "created_at": "2026-10-10T10:00:00+00:00", "updated_at": "2026-10-10T10:00:00+00:00",
            }, "revisions": []}
        record = self.records[doc_id]
        rev = {"id": revision_id, "document_id": doc_id, "sequence": len(record["revisions"])+1,
               "blob_sha256": hashlib.sha256(blob).hexdigest(), "original_filename": "original.txt",
               "mime_type": mime, "size_bytes": len(blob), "issue_date": None, "coverage_start": None,
               "coverage_end": None, "term": None, "superseded_by": None, "created_at": "2026-10-10T10:00:00+00:00",
               **edition}
        record["revisions"].append(rev)
        record["active_revision"] = rev
        record["document"]["active_revision_id"] = revision_id
        record["document"]["metadata_version"] += 1
        self.blobs[revision_id] = blob
        self.events.append({"sequence": len(self.events)+1, "event_type": "revision_added",
                            "document_id": doc_id, "revision_id": revision_id})
        return doc_id, revision_id

    async def get_document(self, doc_id):
        return copy.deepcopy(self.records[doc_id])

    async def get_manifest(self, offset=0, limit=100):
        records = list(self.records.values())
        return {"records": copy.deepcopy(records[offset:offset+limit]), "has_more": offset+limit < len(records),
                "next_offset": offset+limit}

    async def get_events(self, after_sequence=0):
        events = self.events[after_sequence:after_sequence+2]
        return {"events": copy.deepcopy(events), "has_more": after_sequence+len(events) < len(self.events)}

    async def resolve_access(self, document_ids, agent_id):
        items = []
        for doc_id in document_ids:
            record = self.records.get(doc_id)
            doc = record["document"] if record else None
            agents = doc["access_policy"].get("allowed_agents") if doc else []
            granted = bool(doc and not doc["is_tombstone"] and (agents is None or agent_id in agents))
            items.append({"document_id": doc_id, "active_revision_id": doc["active_revision_id"] if granted else None,
                          "is_archived": doc["is_archived"] if granted else False, "access_granted": granted,
                          "record": copy.deepcopy(record) if granted else None})
        return {"items": items}

    async def download_revision_blob(self, revision_id):
        return b"tampered" if self.bad_bytes else self.blobs[revision_id]

    async def report_index_receipt(self, document_id, revision_id, status):
        self.receipts.append((document_id, revision_id, status))

    async def enqueue(self, **job):
        if self.fail_queue:
            raise ConnectionError("queue unavailable")
        key = job["unique_key"]
        if key in self.queued:
            return None
        self.queued[key] = job
        return len(self.queued)


@pytest.fixture
async def hall(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + (tmp_path / "derived.db").as_posix())
    tables = [model.__table__ for model in (
        LibrarySource, LibraryDocument, LibraryRevision, LibraryIndexGeneration,
        LibraryPassage, LibraryEmbedding, LibraryCitation,
    )]
    async with engine.begin() as db:
        await db.run_sync(lambda conn: Base.metadata.create_all(conn, tables=tables))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client = Catalog()
    indexer = LibraryIndexer(client, sessions, enqueue=client.enqueue)
    context = AgentContext(agent_id="ultron", user_id=1, session_id=1, request_id="hall-test",
                           triggered_by="user", trigger_payload={}, output_mode="respond", model="test",
                           system_prompt="", conversation_history=[], db=None, timezone="Europe/Istanbul")
    service = LibraryService(indexer, n8n_secret="isolated-n8n")
    yield SimpleNamespace(client=client, indexer=indexer, service=service, sessions=sessions, context=context)
    await engine.dispose()


async def index_all(hall):
    await hall.indexer.reconcile(full=True)
    for record in hall.client.records.values():
        for rev in record["revisions"]:
            await hall.indexer.index_revision(record["document"]["id"], rev["id"])


async def test_upload_index_search_read_and_resolve_real_evidence(hall):
    doc, rev = hall.client.add("Akademik takvim", "Final sınavları 18 Ocak tarihinde başlar.")
    await index_all(hall)
    result = await hall.service.search(hall.context, "sinav ne zaman")
    assert result.status == "success"
    assert result.search_mode == "keyword_bm25"
    assert result.results[0].content == "Final sınavları 18 Ocak tarihinde başlar."
    assert result.results[0].revision_id == rev
    read = await hall.service.read_passage(hall.context, doc)
    assert read.citation_ids == [result.results[0].citation_id]
    source = await hall.service.citation_source(read.citation_ids[0])
    assert source["document_id"] == doc and source["revision_id"] == rev
    assert not source["historical"]
    assert (await hall.service.list_documents(hall.context)).documents[0].index_status == "ready_keyword"
    async with hall.sessions() as db:
        assert len((await db.scalars(select(LibraryCitation))).all()) == 1


async def test_pending_replacement_never_falls_back_to_old_edition(hall):
    doc, old = hall.client.add("Calendar", "Final exam starts January 18.")
    await index_all(hall)
    _, new = hall.client.add("Calendar", "Final exam starts January 25.", doc_id=doc)
    # Change authority without delivering its event.
    result = await hall.service.search(hall.context, "exam")
    assert not result.results and result.status == "unavailable"
    assert (await hall.service.read_passage(hall.context, doc)).status == "pending"
    historical = await hall.service.read_passage(hall.context, doc, revision_id=old)
    assert historical.results[0].historical
    await index_all(hall)
    result = await hall.service.search(hall.context, "exam")
    assert all(r.revision_id == new for r in result.results)


async def test_access_filter_precedes_top_k_and_revocation_is_live(hall):
    for n in range(10):
        doc, _ = hall.client.add(f"Denied {n}", "Rareword rareword rareword")
        hall.client.records[doc]["document"]["access_policy"] = {"allowed_agents": ["sentinel"]}
    allowed, _ = hall.client.add("Allowed", "Rareword appears once.")
    await index_all(hall)
    result = await hall.service.search(hall.context, "rareword", limit=1)
    assert result.results[0].document_id == allowed
    hall.client.records[allowed]["document"]["access_policy"] = {"allowed_agents": []}
    assert not (await hall.service.search(hall.context, "rareword")).results
    assert not (await hall.service.read_passage(hall.context, allowed)).results
    assert not (await hall.service.list_documents(hall.context)).documents


async def test_coverage_dates_and_explicit_history(hall):
    old, old_rev = hall.client.add("September menu", "Dinner: rice", coverage_start="2026-09-01", coverage_end="2026-09-30")
    current, _ = hall.client.add("October menu", "Dinner: soup", coverage_start="2026-10-01", coverage_end="2026-10-31")
    await index_all(hall)
    result = await hall.service.search(hall.context, "Dinner", on_date="2026-10-10")
    assert [r.document_id for r in result.results] == [current]
    assert not (await hall.service.search(hall.context, "Dinner", on_date="2026-11-10")).results


async def test_durable_acceptance_cursor_and_replay(hall):
    for n in range(5):
        hall.client.add(str(n), "some text")
    hall.client.fail_queue = True
    with pytest.raises(ConnectionError):
        await hall.indexer.reconcile()
    async with hall.sessions() as db:
        source = await db.get(LibrarySource, hall.indexer.source_id)
        assert source.sync_cursor == 0
    hall.client.fail_queue = False
    first = await hall.indexer.reconcile()
    assert first["cursor"] == 5 and len(hall.client.queued) == 5
    await hall.indexer.reconcile(full=True)
    assert len(hall.client.queued) == 5
    await index_all(hall)
    async with hall.sessions() as db:
        assert len((await db.scalars(select(LibraryIndexGeneration))).all()) == 5


async def test_hash_mismatch_never_publishes_passages(hall):
    doc, rev = hall.client.add("Original", "Important evidence")
    await hall.indexer.reconcile()
    hall.client.bad_bytes = True
    with pytest.raises(ValueError, match="SHA-256"):
        await hall.indexer.index_revision(doc, rev)
    async with hall.sessions() as db:
        assert not (await db.scalars(select(LibraryPassage))).all()
        assert (await db.get(LibraryIndexGeneration, hall.indexer.generation_id(rev))).status == "failed"
    hall.client.bad_bytes = False
    await hall.indexer.index_revision(doc, rev)
    assert (await hall.service.read_passage(hall.context, doc)).status == "success"


async def test_scan_and_unsupported_format_require_review_without_fake_text(hall):
    import io
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    original = io.BytesIO()
    writer.write(original)
    doc, rev = hall.client.add("Scanned", original.getvalue(), mime="application/pdf")
    await index_all(hall)
    assert (await hall.service.read_passage(hall.context, doc)).status == "needs_review"
    async with hall.sessions() as db:
        assert not (await db.scalars(select(LibraryPassage))).all()
    assert hall.client.receipts[-1][-1] == "needs_review"
    with pytest.raises(ExtractionError):
        extract_document_bytes(b"original", "image/png")


async def test_citations_fail_closed_on_withdrawal_or_authority_outage(hall):
    doc, rev = hall.client.add("Original", "Exact evidence")
    await index_all(hall)
    read = await hall.service.read_passage(hall.context, doc)
    hall.client.records[doc]["document"]["is_tombstone"] = True
    with pytest.raises(HTTPException) as withdrawn:
        await hall.service.citation_source(read.citation_ids[0])
    assert withdrawn.value.status_code == 410


async def test_tools_use_registry_contract_and_bound_arguments(hall):
    for tool in (ListLibrarySkill(hall.service), SearchLibrarySkill(hall.service), ReadLibrarySkill(hall.service)):
        assert tool.read_only and not tool.memoize
        assert tool.to_tool_definition()["input_schema"]["type"] == "object"
    assert not SearchLibrarySkill.deferred
    result = await SearchLibrarySkill(hall.service).execute({"query": "x", "limit": 1000}, hall.context)
    assert "invalid_arguments" in result
    other = copy.copy(hall.context)
    other.user_id = 99
    with pytest.raises(HTTPException) as exc:
        await hall.service.list_documents(other)
    assert exc.value.status_code == 403


async def test_reconciliation_probe_requires_both_headers(hall, monkeypatch):
    from app.config import settings
    from app.middleware.auth import AuthMiddleware
    from app.routers.library import router
    monkeypatch.setattr(settings, "speda_api_key", "isolated-api")
    app = FastAPI()
    app.state.library = hall.service
    app.include_router(router)
    app.add_middleware(AuthMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://igor") as client:
        assert (await client.post("/library/reconcile")).status_code == 401
        assert (await client.post("/library/reconcile", headers={"X-API-Key": "isolated-api"})).status_code == 401
        # Avoid draining the real owner's database in this isolated test.
        async def no_work():
            pass
        monkeypatch.setattr(hall.service, "drain_jobs", no_work)
        response = await client.post("/library/reconcile", headers={"X-API-Key": "isolated-api", "X-N8N-Secret": "isolated-n8n"})
        assert response.status_code == 200


def test_long_paragraphs_are_bounded_and_binary_errors_are_not_evidence():
    chunks = chunk_text("a" * 10001)
    assert max(map(len, chunks)) <= 2000
    assert "".join(chunks) == "a" * 10001
    with pytest.raises(ExtractionError):
        extract_document_bytes(b"not pdf", "application/pdf")
    with pytest.raises(ExtractionError):
        extract_document_bytes(b"\xff", "text/plain")


def test_text_pdf_preserves_page_locator_and_mixed_scan_requires_review():
    import io
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 30 350 Td (Final exam: January 18) Tj ET")
    page[NameObject("/Contents")] = stream
    original = io.BytesIO()
    writer.write(original)
    passages = extract_document_bytes(original.getvalue(), "application/pdf")
    assert passages[0].page == 1 and "January 18" in passages[0].content
    writer.add_blank_page(width=400, height=400)
    mixed = io.BytesIO()
    writer.write(mixed)
    with pytest.raises(ExtractionError, match="Page 2"):
        extract_document_bytes(mixed.getvalue(), "application/pdf")


async def test_rename_reindexes_title_without_changing_citation_identity(hall):
    doc, rev = hall.client.add("Old title", "Exact source text")
    await index_all(hall)
    before = await hall.service.read_passage(hall.context, doc)
    hall.client.records[doc]["document"]["title"] = "Renamed archive"
    hall.client.records[doc]["document"]["metadata_version"] += 1
    await hall.indexer.reconcile(full=True)
    found = await hall.service.search(hall.context, "Renamed")
    assert found.results[0].document_title == "Renamed archive"
    assert found.results[0].citation_id == before.citation_ids[0]


async def test_legacy_default_source_adoption_keeps_existing_evidence(hall):
    doc, rev = hall.client.add("Retained", "Original evidence")
    await index_all(hall)
    async with hall.sessions() as db:
        db.add(LibrarySource(id="default", hisar_url="http://legacy-alias"))
        await db.flush()
        record = await db.get(LibraryDocument, doc)
        record.source_id = "default"
        await db.commit()
    await hall.indexer.reconcile(full=True)
    async with hall.sessions() as db:
        assert (await db.get(LibraryDocument, doc)).source_id == hall.indexer.source_id
        assert await db.get(LibrarySource, "default")
        assert len((await db.scalars(select(LibraryPassage))).all()) == 1
    assert (await hall.service.read_passage(hall.context, doc)).results[0].content == "Original evidence"
