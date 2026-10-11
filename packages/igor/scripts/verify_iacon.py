"""Exercise both real Hall APIs and the durable queue using disposable data.

Usage: python packages/igor/scripts/verify_iacon.py --hisar-checkout /path/to/hisar
No production connection, credentials, vault or database are used.
"""
import argparse
import asyncio
import io
import os
import sys
import tempfile
from pathlib import Path


async def verify(hisar_checkout):
    scratch = Path(tempfile.mkdtemp(prefix="iacon-contract-")).resolve()
    if scratch.parent != Path(tempfile.gettempdir()).resolve():
        raise RuntimeError("Verification scratch must stay in the OS temporary directory")
    os.environ.update(
        HISAR_SANDBOX_ROOT=str(scratch / "vault"), HISAR_STATIC_DIR=str(scratch / "no-build"),
        HISAR_JWT_SECRET="isolated-contract-secret-for-local-verification-only", HISAR_COOKIE_SECURE="false",
        HISAR_LIBRARY_SYNC_TOKEN="isolated-sync", HISAR_LIBRARY_READER_TOKEN="isolated-reader",
        HISAR_IGOR_API_KEY="", HISAR_IGOR_N8N_SECRET="",
        DATABASE_URL="sqlite+aiosqlite:///" + (scratch / "derived.db").as_posix(),
    )
    sys.path.insert(0, str(hisar_checkout))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from server.auth import hash_password
    os.environ["HISAR_OWNER_PASSWORD_HASH"] = hash_password("isolated-password")
    from server.config import get_settings
    get_settings.cache_clear()
    from server.main import create_app
    import httpx
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.core.context import AgentContext
    from app.database import Base
    import app.models
    from app.models.user import User
    from app.models.background_job import BackgroundJob
    from app.models.library import LibraryIndexGeneration
    from app.services.hisar_library_client import HisarLibraryClient
    from app.services.library import LibraryService
    from app.services import library_indexer, task_queue
    from app.skills.library import SearchLibrarySkill
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        db.add(User(id=1, name="Test owner", timezone="Europe/Istanbul"))
        await db.commit()

    hisar = create_app()
    def new_indexer():
        source = HisarLibraryClient("http://isolated-hisar", "isolated-sync", "isolated-reader",
                                   transport=httpx.ASGITransport(app=hisar))
        return library_indexer.LibraryIndexer(source, sessions)
    indexer = new_indexer()
    library_indexer.indexer_from_settings = new_indexer
    task_queue.AsyncSessionLocal = sessions
    context = AgentContext(agent_id="ultron", user_id=1, session_id=0, request_id="contract-test",
                           triggered_by="user", trigger_payload={}, output_mode="respond", model="test",
                           system_prompt="", conversation_history=[], db=None, timezone="Europe/Istanbul")
    service = LibraryService(indexer)
    try:
        async with hisar.router.lifespan_context(hisar):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hisar), base_url="http://isolated-hisar") as owner:
                login = await owner.post("/auth/login", json={"password": "isolated-password"})
                assert login.status_code == 200, login.text
                upload = await owner.post("/library/records", data={"title": "Demo calendar", "category": "Academics"},
                                          files={"file": ("calendar.md", "Final sınavları 18 Ocak tarihinde başlar.".encode(), "text/markdown")})
                assert upload.status_code == 200, upload.text
                record = upload.json()
                document_id = record["document"]["id"]
                old_revision = record["active_revision"]["id"]
                accepted = await indexer.reconcile(full=True)
                assert accepted["queued"] == 1
                async with sessions() as db:
                    job = (await db.scalars(select(BackgroundJob))).one()
                    assert job.session_id is None and job.status == "pending"
                # Simulate a process stopping after acceptance, before any index work.
                await indexer.client.aclose()
                indexer = new_indexer()
                service = LibraryService(indexer)
                drained = await task_queue.drain()
                assert drained["succeeded"] == 1 and drained["failed"] == 0, drained
                result = await SearchLibrarySkill(service).execute({"query": "sinav ne zaman"}, context)
                import json
                result = json.loads(result)
                assert result["status"] == "success" and result["results"][0]["revision_id"] == old_revision, result
                citation = await service.citation_source(result["results"][0]["citation_id"])
                assert citation["content"] == "Final sınavları 18 Ocak tarihinde başlar."
                view = await owner.get("/library/documents")
                assert view.json()["records"][0]["index_status"] == "ready_keyword"
                replacement = await owner.post(f"/library/documents/{document_id}/revisions",
                    data={"expected_version": record["document"]["metadata_version"]},
                    files={"file": ("calendar.md", "Final sınavları 25 Ocak tarihinde başlar.".encode(), "text/markdown")})
                assert replacement.status_code == 200, replacement.text
                # No event replay yet: authority has changed, so old evidence must already be refused.
                assert not (await service.search(context, "sinav")).results
                assert (await service.read_passage(context, document_id, revision_id=old_revision)).results[0].historical
                await indexer.reconcile()
                assert (await task_queue.drain())["succeeded"] == 1
                assert "25 Ocak" in (await service.search(context, "sinav")).results[0].content
                current = (await owner.get("/library/documents")).json()["records"][0]
                revoked = await owner.patch(f"/library/documents/{document_id}", json={
                    "expected_version": current["document"]["metadata_version"], "access_policy": {"allowed_agents": []}})
                assert revoked.status_code == 200, revoked.text
                assert not (await service.search(context, "sinav")).results
                assert (await owner.get(f"/library/revisions/{old_revision}/download")).status_code == 200
        print("PASS: real upload -> durable queue -> restart -> hash-verified index -> Turkish search -> citation.")
        print("PASS: current-edition replacement, retained original/history, live access revocation and UI readiness receipt.")
        print(f"Disposable verification artifacts: {scratch}")
    finally:
        await indexer.client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hisar-checkout", type=Path, required=True)
    args = parser.parse_args()
    checkout = args.hisar_checkout.resolve()
    if not (checkout / "server" / "library.py").is_file():
        parser.error("Choose the local Hisar source checkout")
    asyncio.run(verify(checkout))
