"""Authenticated owner controls and durable, content-free indexing progress."""
import asyncio
import datetime as dt
import hmac
import shutil
import subprocess

from fastapi import HTTPException
from sqlalchemy import select, func, update

from app.models.library import LibraryDocument, LibraryIndexGeneration, LibraryPassage, LibraryEmbedding
from app.models.background_job import BackgroundJob


class LibraryControls:
    def __init__(self, indexer, settings, *, writer=None):
        from app.config import write_managed_env
        self.indexer, self.settings = indexer, settings
        self.writer = writer or write_managed_env
        self.lock = asyncio.Lock()

    def authorize(self, secret):
        if not secret or not self.settings.n8n_secret or not hmac.compare_digest(secret, self.settings.n8n_secret):
            raise HTTPException(401, "Invalid library control secret")

    @staticmethod
    def ocr_runtime():
        executable = shutil.which("tesseract")
        languages = []
        if executable:
            try:
                result = subprocess.run([executable, "--list-langs"], capture_output=True, text=True,
                                        timeout=3, check=True, stdin=subprocess.DEVNULL)
                languages = [line.strip() for line in result.stdout.splitlines() if len(line.strip()) == 3]
            except (OSError, subprocess.SubprocessError):
                pass
        return {"available": bool(executable and shutil.which("pdftoppm") and languages), "languages": languages}

    async def snapshot(self, secret):
        self.authorize(secret)
        runtime = await asyncio.to_thread(self.ocr_runtime)
        s = self.settings
        return {"connected": self.indexer.client.configured, "semantic_enabled": s.library_semantic_enabled,
                "ocr_enabled": s.library_ocr_enabled, "ocr_languages": s.library_ocr_languages,
                "embedding_key_configured": bool(s.library_embedding_api_key or s.openai_api_key),
                "embedding_model": s.embedding_model, "ocr_runtime": runtime,
                "max_pages": s.library_max_pages, "max_bytes": s.library_max_bytes}

    async def save(self, secret, values):
        self.authorize(secret)
        async with self.lock:
            key = values.embedding_api_key.get_secret_value().strip() if values.embedding_api_key is not None else None
            if key is not None and (not key or "\n" in key or "\r" in key):
                raise HTTPException(422, "Enter a valid embedding API key, or leave it blank to keep the saved key")
            if values.semantic_enabled and not (key or self.settings.library_embedding_api_key or self.settings.openai_api_key):
                raise HTTPException(422, "Semantic search needs an embedding API key")
            if values.ocr_enabled:
                runtime = await asyncio.to_thread(self.ocr_runtime)
                missing = set(values.ocr_languages.split("+")) - set(runtime["languages"])
                if not runtime["available"] or missing:
                    raise HTTPException(422, "Install Tesseract, Poppler and the selected language packs on Igor before enabling OCR")
            updates = {"library_semantic_enabled": values.semantic_enabled, "library_ocr_enabled": values.ocr_enabled,
                       "library_ocr_languages": values.ocr_languages}
            if key is not None:
                updates["library_embedding_api_key"] = key
            self.writer({name.upper(): str(value).lower() if isinstance(value, bool) else value for name, value in updates.items()})
            for name, value in updates.items():
                setattr(self.settings, name, value)
            self.indexer.configure(self.settings)
            # Reconciliation enqueues missing extraction generations and vectors;
            # the route drains this durable work after responding.
            warning = None
            try:
                result = await self.indexer.reconcile(full=True)
                if result["status"] == "unconfigured":
                    warning = "Settings saved. Connect Igor to Hisar to start indexing."
            except Exception:
                result = {"queued": 0}
                warning = "Settings saved. Catalog synchronization is unavailable; reconnect and refresh to start indexing."
            return {**await self.snapshot(secret), "queued": result["queued"], "saved": True, "warning": warning}

    async def progress(self, secret, document_ids):
        self.authorize(secret)
        rows = []
        async with self.indexer.sessions() as db:
            docs = list((await db.scalars(select(LibraryDocument).where(
                LibraryDocument.source_id == self.indexer.source_id, LibraryDocument.id.in_(document_ids)))).all())
            for doc in docs:
                gen_id = self.indexer.generation_id(doc.active_revision_id)
                gen = await db.get(LibraryIndexGeneration, gen_id)
                jobs = list((await db.scalars(select(BackgroundJob).where(
                    BackgroundJob.user_id == self.indexer.owner_user_id,
                    BackgroundJob.kind.in_(("library_index", "library_embed")),
                    BackgroundJob.payload["source_id"].as_string() == self.indexer.source_id,
                    BackgroundJob.payload["revision_id"].as_string() == doc.active_revision_id,
                ).order_by(BackgroundJob.created_at.desc(), BackgroundJob.id.desc()))).all())
                # Only this generation/model's jobs can describe current progress.
                jobs = [j for j in jobs if (j.kind == "library_index" and j.unique_key == gen_id) or
                        (j.kind == "library_embed" and self.indexer.embedder and
                         j.payload.get("embedding_fingerprint") == self.indexer.embedder.fingerprint)]
                active = next((j for j in jobs if j.status in {"running", "pending"}), None)
                latest_by_kind = {kind: next((j for j in jobs if j.kind == kind), None) for kind in ("library_index", "library_embed")}
                terminal = next((j for j in latest_by_kind.values() if j and j.status == "failed"), None)
                total = await db.scalar(select(func.count()).select_from(LibraryPassage).where(LibraryPassage.generation_id == gen_id))
                indexed = 0
                if self.indexer.embedder:
                    indexed = await db.scalar(select(func.count()).select_from(LibraryEmbedding).join(LibraryPassage).where(
                        LibraryPassage.generation_id == gen_id,
                        LibraryEmbedding.model_fingerprint == self.indexer.embedder.fingerprint))
                state = gen.status if gen else "not_synced"
                details = gen.progress or {} if gen else {}
                if active:
                    state = "processing" if active.status == "running" else "pending"
                    if active.status == "pending" and active.last_error:
                        state = "retry_wait"
                    if active.status == "pending":
                        details = {"stage": "retry_wait" if active.last_error else "queued"}
                elif terminal and (not gen or gen.status not in {"needs_review"}):
                    if not (terminal.kind == "library_embed" and indexed == total and total):
                        state = "failed"
                rows.append({"document_id": doc.id, "revision_id": doc.active_revision_id, "state": state,
                             "progress": details, "passages": total,
                             "semantic": {"enabled": bool(self.indexer.embedder), "indexed": indexed, "total": total},
                             "message": gen.failure_details if gen and gen.status == "needs_review" else
                                        "Indexing failed. Retry to run this edition again." if state in {"failed", "retry_wait"} else None,
                             "retry_after": active.run_after.isoformat() + "Z" if active and state == "retry_wait" else None})
        found = {row["document_id"] for row in rows}
        rows.extend({"document_id": doc_id, "state": "not_synced", "progress": {"stage": "awaiting_sync"}}
                    for doc_id in document_ids if doc_id not in found)
        return {"records": rows}

    async def retry_due(self, secret):
        self.authorize(secret)
        # Explicit owner refresh/retry bypasses backoff only for this source's
        # pending library work; running claims are never reset.
        async with self.indexer.sessions() as db:
            await db.execute(update(BackgroundJob).where(
                BackgroundJob.user_id == self.indexer.owner_user_id,
                BackgroundJob.kind.in_(("library_index", "library_embed")), BackgroundJob.status == "pending",
                BackgroundJob.payload["source_id"].as_string() == self.indexer.source_id,
            ).values(run_after=dt.datetime.now(dt.timezone.utc)))
            await db.commit()
