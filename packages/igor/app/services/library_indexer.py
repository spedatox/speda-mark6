import asyncio
import uuid
import datetime
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.library import (
    LibrarySource, LibraryDocument, LibraryRevision, LibraryIndexGeneration,
    LibraryPassage, LibraryEmbedding
)
from app.services.hisar_library_client import get_library_client
from app.services.library_extract import extract_document_bytes
from app.config import settings

logger = logging.getLogger(__name__)

FINGERPRINT = "extract_v1_embed_v1"

async def sync_events():
    client = get_library_client()
    
    async with AsyncSessionLocal() as session:
        # For a single-user system, we assume 1 source
        source_id = "default"
        source = await session.get(LibrarySource, source_id)
        if not source:
            source = LibrarySource(id=source_id, hisar_url=client.hisar_url)
            session.add(source)
            await session.commit()
            
        cursor = source.sync_cursor
        
        while True:
            try:
                data = await client.get_events(after_sequence=cursor)
                events = data.get("events", [])
                
                for ev in events:
                    # Depending on event_type, we might need to fetch the document from Hisar
                    # Since this is an event feed, we could just trigger an index job
                    # For simplicity, if it's 'revision_added', we trigger index job
                    if ev['event_type'] == 'revision_added':
                        asyncio.create_task(index_revision(ev['document_id'], ev['revision_id']))
                    elif ev['event_type'] in ['document_archived', 'document_purged']:
                        await handle_tombstone(session, ev['document_id'], ev['event_type'])
                        
                if not data.get("has_more"):
                    # Update cursor
                    if events:
                        source.sync_cursor = events[-1]['sequence']
                        await session.commit()
                    break
                    
                cursor = events[-1]['sequence']
            except Exception as e:
                logger.error(f"Error syncing events: {e}")
                break

async def handle_tombstone(session: AsyncSession, document_id: str, event_type: str):
    doc = await session.get(LibraryDocument, document_id)
    if doc:
        if event_type == 'document_purged':
            doc.is_tombstone = True
        elif event_type == 'document_archived':
            doc.is_archived = True
        await session.commit()

async def index_revision(document_id: str, revision_id: str):
    client = get_library_client()
    
    async with AsyncSessionLocal() as session:
        # Check if already generating
        # In real implementation we use TaskQueue to prevent duplicates
        generation_id = str(uuid.uuid4())
        gen = LibraryIndexGeneration(
            id=generation_id,
            revision_id=revision_id,
            status="processing",
            fingerprint=FINGERPRINT
        )
        session.add(gen)
        await session.commit()
        
        try:
            # 1. Download blob
            blob = await client.download_revision_blob(revision_id)
            
            # 2. Extract
            # Determine mime type from some metadata API, or just assume PDF for now
            # (In reality, we'd fetch the LibraryRevision metadata first)
            passages = extract_document_bytes(blob, mime_type="application/pdf")
            
            # 3. Store passages
            for p in passages:
                passage_id = str(uuid.uuid4())
                lp = LibraryPassage(
                    id=passage_id,
                    generation_id=generation_id,
                    revision_id=revision_id,
                    content=p.content,
                    page=p.page,
                    section=p.section,
                    needs_review=p.needs_review
                )
                session.add(lp)
                
                # We would also compute embeddings here and add LibraryEmbedding
                
            gen.status = "completed"
            gen.completed_at = datetime.datetime.utcnow()
            await session.commit()
            
            # 4. Report receipt
            await client.report_index_receipt(document_id, revision_id, "ready")
            
        except Exception as e:
            gen.status = "failed"
            gen.failure_details = str(e)
            gen.completed_at = datetime.datetime.utcnow()
            await session.commit()
            await client.report_index_receipt(document_id, revision_id, "failed")
