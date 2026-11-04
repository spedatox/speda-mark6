"""Hall of Records controls backed by the Renovated index and work queue."""
import asyncio

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.iacon import IaconWork
from app.models.library import LibraryDocument, LibraryIndexGeneration, LibraryPassage


class LibraryControls:
    def __init__(self, indexer, budget):
        self.indexer = indexer
        self.budget = budget

    async def reconcile(self, context, *, full=False):
        _, policy = await self.budget.policy(context.user_id)
        if policy.paused:
            return {"status": "paused"}
        try:
            # Hisar's HTTP client times out at 30 seconds.
            async with asyncio.timeout(min(25, policy.run_seconds)):
                return await self.indexer.reconcile(full=full)
        except TimeoutError:
            return {"status": "time_limit", "detail": "Committed event cursor retained; resume next run"}
        except Exception as exc:
            raise HTTPException(503, "Hall of Records synchronization failed; work remains retryable") from exc

    def settings(self):
        indexer = self.indexer
        return {"connected": indexer.client.configured, "semantic_enabled": False,
                "ocr_enabled": False, "ocr_languages": "tur+eng",
                "embedding_key_configured": False, "embedding_model": "",
                "ocr_runtime": {"available": False, "languages": []},
                "max_pages": indexer.max_pages, "max_bytes": indexer.client.max_bytes}

    def save(self, values):
        if values.semantic_enabled or values.embedding_api_key is not None:
            raise HTTPException(422, "Semantic indexing is not enabled in this Igor release. Keyword search is available.")
        if values.ocr_enabled or values.ocr_languages != "tur+eng":
            raise HTTPException(422, "Local OCR is not installed in this Igor release.")
        return {**self.settings(), "queued": 0, "saved": True, "warning": None}

    async def progress(self, document_ids):
        indexer = self.indexer
        async with indexer.sessions() as db:
            docs = (await db.scalars(select(LibraryDocument).where(
                LibraryDocument.source_id == indexer.source_id,
                LibraryDocument.id.in_(document_ids), LibraryDocument.is_tombstone.is_(False)))).all()
            generations = {doc.id: indexer.generation_id(doc.active_revision_id) for doc in docs if doc.active_revision_id}
            generation_rows = {gen.id: gen for gen in (await db.scalars(select(LibraryIndexGeneration).where(
                LibraryIndexGeneration.id.in_(generations.values())))).all()}
            jobs = {job.work_key: job for job in (await db.scalars(select(IaconWork).where(
                IaconWork.user_id == indexer.owner_user_id, IaconWork.kind == "library_index",
                IaconWork.work_key.in_([f"library_index:{gen_id}" for gen_id in generations.values()])))).all()}
            counts = dict((await db.execute(select(LibraryPassage.generation_id, func.count()).where(
                LibraryPassage.generation_id.in_(generations.values())).group_by(LibraryPassage.generation_id))).all())

        records = {}
        for doc in docs:
            gen_id = generations.get(doc.id)
            gen = generation_rows.get(gen_id)
            job = jobs.get(f"library_index:{gen_id}")
            state = gen.status if gen else "not_synced"
            details = (gen.progress or {}) if gen else {"stage": "awaiting_sync"}
            message = gen.failure_details if gen and state == "needs_review" else None
            if job and job.status in {"running", "pending"}:
                state = "processing" if job.status == "running" else "pending"
                details = details if job.status == "running" else {"stage": "queued"}
                message = None
            elif job and job.status in {"held", "quarantined"}:
                state, details = "failed", {"stage": "failed"}
                message = "Indexing failed. Retry to run this edition again."
            total = counts.get(gen_id, 0)
            records[doc.id] = {"document_id": doc.id, "revision_id": doc.active_revision_id, "state": state,
                               "progress": details, "passages": total,
                               "semantic": {"enabled": False, "indexed": 0, "total": total},
                               "message": message, "retry_after": None}
        return {"records": [records.get(doc_id, {"document_id": doc_id, "state": "not_synced",
                            "progress": {"stage": "awaiting_sync"}}) for doc_id in dict.fromkeys(document_ids)]}
