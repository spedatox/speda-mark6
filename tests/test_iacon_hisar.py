import copy
import hashlib
import uuid
import json
import pytest
from fastapi import HTTPException
from app.services.library_indexer import LibraryIndexer
from app.services.library import LibraryService
from app.skills.memory_lookup import MemoryLookupSkill
from app.services.memory_runtime import MemoryRuntime


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


async def test_hisar_original_ids_editions_citations_and_live_denial(memory):
    client = Catalog()
    doc, first = client.add("School handbook", "Library opens at nine.")
    indexer = LibraryIndexer(client, memory.sessions, enqueue=client.enqueue)
    service = LibraryService(indexer)
    await indexer.reconcile(full=True)
    await indexer.index_revision(doc, first)
    result = await service.search(memory.context(), "Library opens")
    assert result.results[0].document_id == doc
    citation = result.results[0].citation_id
    assert (await service.citation_source(citation))["content"] == "Library opens at nine."
    _, second = client.add("School handbook", "Library opens at ten.", doc_id=doc)
    await indexer.reconcile()
    await indexer.index_revision(doc, second)
    latest = await service.search(memory.context(), "Library opens")
    assert latest.results[0].revision_id == second
    assert (await service.citation_source(citation))["historical"]
    client.records[doc]["document"]["access_policy"] = {"allowed_agents": []}
    assert not (await service.search(memory.context(), "Library opens")).results
    with pytest.raises(HTTPException) as error:
        await service.citation_source(citation)
    assert error.value.status_code == 403


async def test_hisar_and_personal_lookup_share_one_batch(memory):
    client = Catalog()
    doc, revision = client.add("Course", "Calculus homework is due Friday.")
    indexer = LibraryIndexer(client, memory.sessions, enqueue=client.enqueue)
    await indexer.reconcile()
    await indexer.index_revision(doc, revision)
    skill = MemoryLookupSkill(MemoryRuntime(memory.store), LibraryService(indexer))
    context = memory.context()
    result = json.loads(await skill.execute({"query": "Calculus", "corpus": "hall_of_records"}, context))
    assert result["results"] and context.memory_budget.lookup_batches == 1
    assert context.memory_budget.used <= context.memory_budget.turn_limit
    assert "budget exhausted" in await skill.execute({"query": "Calculus"}, context)


async def test_hisar_corrupt_original_never_publishes(memory):
    client = Catalog()
    doc, revision = client.add("Course", "Original content")
    indexer = LibraryIndexer(client, memory.sessions, enqueue=client.enqueue)
    await indexer.reconcile()
    client.bad_bytes = True
    with pytest.raises(ValueError, match="verification"):
        await indexer.index_revision(doc, revision)
    assert not (await LibraryService(indexer).search(memory.context(), "Original")).results


async def test_deployed_hisar_routes_authenticate_and_retry_on_the_new_queue(memory):
    import httpx
    from app.main import create_app
    from app.models.iacon import IaconWork
    from app.services.iacon_budget import MaintenanceBudget
    from app.services.iacon_catalogue import Catalogue
    from app.services.iacon_worker import Orion
    from app.services.library_controls import LibraryControls
    from sqlalchemy import select

    hisar = Catalog()
    doc, revision = hisar.add("Course", "Original calculus notes.")
    budget = MaintenanceBudget(memory.sessions, None)
    orion = Orion(Catalogue(memory.store), budget, None, None)
    indexer = LibraryIndexer(hisar, memory.sessions, enqueue=orion.enqueue)
    orion.library_indexer = indexer
    app = create_app()
    app.state.library = LibraryService(indexer)
    app.state.library_controls = LibraryControls(indexer, budget)
    app.state.orion = orion
    await orion.enqueue(user_id=1, kind="interpret", unique_key="unrelated", payload={})
    hisar.bad_bytes = True
    api_key = {"X-API-Key": "test-only-key"}
    scheduler = {"X-N8N-Secret": "test-only-n8n"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://igor") as client:
        assert (await client.get("/library/settings", headers=scheduler)).status_code == 401
        assert (await client.get("/library/settings", headers=api_key)).status_code == 401
        client.headers.update({**api_key, **scheduler})
        assert (await client.get("/library/settings")).json()["connected"]
        progress = {"document_ids": [doc]}
        accepted = await client.post("/library/reconcile?full=true")
        assert accepted.json()["queued"] == 1
        assert (await client.post("/library/progress", json=progress)).json()["records"][0]["state"] == "failed"

        # Hisar records a retry event before posting to the same reconcile route.
        hisar.bad_bytes = False
        hisar.events.append({"sequence": 2, "event_type": "index_requested", "document_id": doc})
        assert (await client.post("/library/reconcile")).json()["queued"] == 1
        ready = (await client.post("/library/progress", json=progress)).json()["records"][0]
        assert (ready["state"], ready["revision_id"], ready["passages"]) == ("ready_keyword", revision, 1)
        assert hisar.receipts[-1] == (doc, revision, "ready_keyword")
        match = (await app.state.library.search(memory.context(), "calculus")).results[0]
        assert (await client.get(f"/library/sources/{match.citation_id}")).json()["content"] == "Original calculus notes."

    async with memory.sessions() as db:
        assert await db.scalar(select(IaconWork.status).where(IaconWork.kind == "interpret")) == "pending"


def test_native_document_extractors_keep_tables_formulas_and_pdf_review():
    import io
    from docx import Document
    from openpyxl import Workbook
    from pypdf import PdfWriter
    from app.services.library_extract import extract_document_bytes, ExtractionError
    document = Document()
    document.add_heading("Fees", level=1)
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Tuition", "100"
    data = io.BytesIO()
    document.save(data)
    passages = extract_document_bytes(data.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert any("Tuition | 100" in p.content for p in passages)
    workbook = Workbook()
    workbook.active.append(["Fees", 100])
    workbook.active.append(["Total", "=B1"])
    data = io.BytesIO()
    workbook.save(data)
    passages = extract_document_bytes(data.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert any("formula: =B1" in p.content for p in passages)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    data = io.BytesIO()
    writer.write(data)
    with pytest.raises(ExtractionError, match="OCR"):
        extract_document_bytes(data.getvalue(), "application/pdf")




async def test_catalog_and_exact_document_reads_use_the_existing_shared_lookup_budget(memory):
    client = Catalog()
    doc, revision = client.add("Course handbook", "Exact edition evidence.\n" * 20)
    indexer = LibraryIndexer(client, memory.sessions, enqueue=client.enqueue)
    await indexer.reconcile()
    await indexer.index_revision(doc, revision)
    skill = MemoryLookupSkill(MemoryRuntime(memory.store), LibraryService(indexer))
    context = memory.context()
    listing = json.loads(await skill.execute({"query":"", "corpus":"hall_of_records", "operation":"list"}, context))
    assert listing["documents"][0]["id"] == doc
    assert context.memory_budget.lookup_batches == 1
    assert not skill.offered(context)
    exact_context = memory.context()
    read = json.loads(await skill.execute({"query":"", "corpus":"hall_of_records", "operation":"read",
        "document_ids":[doc], "revision_id":revision}, exact_context))
    assert read["results"][0]["revision_id"] == revision
    assert "Exact edition evidence" in read["results"][0]["content"]
    assert exact_context.memory_budget.lookup_batches == 1
    assert exact_context.memory_budget.used <= exact_context.memory_budget.lookup_limit
    client.records[doc]["document"]["access_policy"] = {"allowed_agents": []}
    denied = json.loads(await skill.execute({"query":"", "corpus":"hall_of_records", "operation":"read",
        "document_ids":[doc]}, memory.context()))
    assert denied["status"] == "not_found"


async def test_menu_lookup_recovers_the_exact_date_within_the_byte_budget(memory, monkeypatch):
    from datetime import date, datetime
    from types import SimpleNamespace
    from app.services import library

    class TurnClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 6, 17, 7, tzinfo=tz)

    monkeypatch.setattr(library, "dt", SimpleNamespace(datetime=TurnClock, date=date))
    client = Catalog()
    row = "6.11.2026 CUMA Mercimek çorbası"
    menu = row + "\n" + ("Yurtta servis saatleri değişebilir.\n" * 60) + (
        "26.11.2026 PERŞEMBE Dinner menu\n" + "Dinner menu special. " * 82
    )
    doc, revision = client.add("November Residence Meals", menu,
                               coverage_start="2026-11-01", coverage_end="2026-11-30")
    indexer = LibraryIndexer(client, memory.sessions, enqueue=client.enqueue)
    service = LibraryService(indexer)
    await indexer.reconcile()
    await indexer.index_revision(doc, revision)
    # The dated row contains none of the keyword query terms.
    baseline = await service.search(memory.context(), "dinner menu")
    assert baseline.results and all(row not in result.content for result in baseline.results)

    client.records[doc]["document"]["type"] = "cafeteria_menu"
    skill = MemoryLookupSkill(MemoryRuntime(memory.store), service)
    args = {"corpus": "hall_of_records", "query": "dinner menu"}
    context = memory.context()
    raw = await skill.execute(args, context)
    result = json.loads(raw)
    assert result["status"] == "success" and result["query_date"] == "2026-11-06"
    evidence, = result["results"]
    assert row in evidence["content"] and evidence["revision_id"] == revision
    assert (await service.citation_source(evidence["citation_id"]))["content"] == evidence["content"]
    assert result["warnings"] == ["Additional passages omitted by the shared turn budget"]
    assert context.memory_budget.lookup_batches == 1
    assert len(raw.encode()) == context.memory_budget.used <= context.memory_budget.lookup_limit

    later = json.loads(await skill.execute({**args, "on_date": "2026-11-26"}, memory.context()))
    assert "26.11.2026" in later["results"][0]["content"]
    client.records[doc]["document"]["access_policy"] = {"allowed_agents": []}
    denied = json.loads(await skill.execute(args, memory.context()))
    assert not denied["results"]
