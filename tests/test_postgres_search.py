import asyncio

from sqlalchemy import delete

from app.models.memory_serving import MemoryChange
from app.services.library import LibraryService
from app.services.library_indexer import LibraryIndexer
from app.services.memory_store import capture_source
from tests.test_coordination_postgres import memory
from tests.test_iacon_hisar import Catalog, test_hisar_original_ids_editions_citations_and_live_denial


async def matches(store, db, term, limit=20):
    return [row.id for row in await store._search(db, MemoryChange, "sources", 1, [term], limit)]


async def test_postgres_ranking_is_relevant_owner_scoped_and_snapshot_consistent(memory):
    store = memory.store
    async with memory.sessions() as db, db.begin():
        strong = await capture_source(db, user_id=1, source_key="strong", content="routing " * 12)
        weak = await capture_source(db, user_id=1, source_key="weak", content="routing with several other words")
        await capture_source(db, user_id=2, source_key="private", content="routing " * 20)
        await capture_source(db, user_id=1, source_key="ineligible", content="routing " * 30,
                             provenance={"eligible_for_context": False})
        strong_id, weak_id = strong.id, weak.id
    async with store.reader() as older:
        assert await matches(store, older, "routing", limit=1) == [strong_id]
        await store.revoke(memory.context(), strong_id)
        async with store.reader() as newer:
            assert await matches(store, newer, "routing") == [weak_id]
        assert await matches(store, older, "routing") == [strong_id, weak_id]
    async with memory.sessions() as db, db.begin():
        await db.execute(delete(MemoryChange).where(MemoryChange.id == weak_id))
    async with store.reader() as newest:
        assert await matches(store, newest, "routing") == []


async def test_postgres_ranking_observes_versions_committed_out_of_order(memory):
    store = memory.store
    async with memory.sessions() as first, first.begin():
        pending = await capture_source(first, user_id=1, source_key="first", content="analysis")
        async with memory.sessions() as second, second.begin():
            published = await capture_source(second, user_id=1, source_key="second", content="analysis")
        async with store.reader() as reader:
            assert await matches(store, reader, "analysis") == [published.id]
    async with store.reader() as reader:
        assert await matches(store, reader, "analysis") == [published.id, pending.id]


async def test_postgres_hall_reindexes_titles_and_concurrent_citations(memory):
    client = Catalog()
    doc, edition = client.add("Araştırmalar", "Study notes from the library.")
    indexer = LibraryIndexer(client, memory.sessions, enqueue=client.enqueue)
    service = LibraryService(indexer)
    await indexer.reconcile(full=True)
    await indexer.index_revision(doc, edition)
    first = await service.search(memory.context(), "araştırma")
    assert first.search_mode == "keyword_bm25" and first.results[0].revision_id == edition

    metadata = client.records[doc]["document"]
    metadata["title"] = "Deneysel çalışma"
    metadata["metadata_version"] += 1
    await indexer._mirror(client.records[doc])
    assert not (await service.search(memory.context(), "araştırma")).results
    answers = await asyncio.gather(*(service.search(memory.context(), "deneysel") for _ in range(8)))
    assert all(answer.results[0].citation_id == first.results[0].citation_id for answer in answers)
