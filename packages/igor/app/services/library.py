import datetime
import uuid
from typing import List, Optional
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.library import (
    LibraryDocument, LibraryRevision, LibraryPassage, LibraryCitation
)
from app.schemas.library import (
    LibrarySearchResult, LibrarySearchResponse, LibraryListResult, LibraryListResponse,
    LibraryReadResponse, LibraryCitationInfo
)
from app.services.hisar_library_client import get_library_client
from app.core.context import AgentContext

class LibraryService:
    def __init__(self):
        self.client = get_library_client()

    async def _resolve_access(self, doc_ids: List[str], context: AgentContext) -> dict:
        """Call Hisar to get authoritative access for these docs."""
        return await self.client.resolve_access(doc_ids, context.agent_id)

    async def list_documents(self, context: AgentContext, category: Optional[str] = None, 
                             doc_type: Optional[str] = None, include_archived: bool = False) -> LibraryListResponse:
        async with AsyncSessionLocal() as session:
            query = select(LibraryDocument).where(LibraryDocument.is_tombstone == False)
            if category:
                query = query.where(LibraryDocument.category == category)
            if doc_type:
                query = query.where(LibraryDocument.doc_type == doc_type)
            if not include_archived:
                query = query.where(LibraryDocument.is_archived == False)
                
            result = await session.execute(query)
            docs = result.scalars().all()
            
            # Revalidate access with Hisar
            doc_ids = [d.id for d in docs]
            if not doc_ids:
                return LibraryListResponse(documents=[])
                
            access_info = await self._resolve_access(doc_ids, context)
            granted_ids = {item['document_id'] for item in access_info.get('items', []) if item['access_granted']}
            
            results = []
            for d in docs:
                if d.id in granted_ids:
                    results.append(LibraryListResult(
                        id=d.id,
                        title=d.title,
                        category=d.category,
                        type=d.doc_type,
                        active_revision_id=d.active_revision_id,
                        index_status="ready" # Simplified
                    ))
            
            return LibraryListResponse(documents=results)

    async def search(self, context: AgentContext, query_text: str, doc_ids: Optional[List[str]] = None) -> LibrarySearchResponse:
        async with AsyncSessionLocal() as session:
            # Very basic lexical search via ILIKE for demonstration (Real would use FTS5/Embeddings)
            query = select(LibraryPassage, LibraryDocument, LibraryRevision).join(
                LibraryRevision, LibraryPassage.revision_id == LibraryRevision.id
            ).join(
                LibraryDocument, LibraryRevision.document_id == LibraryDocument.id
            ).where(
                LibraryDocument.is_tombstone == False,
                LibraryDocument.is_archived == False,
                LibraryPassage.content.ilike(f"%{query_text}%")
            )
            
            if doc_ids:
                query = query.where(LibraryDocument.id.in_(doc_ids))
                
            query = query.limit(8)
            result = await session.execute(query)
            rows = result.all()
            
            # Authoritative check
            found_doc_ids = list(set([row[1].id for row in rows]))
            if found_doc_ids:
                access_info = await self._resolve_access(found_doc_ids, context)
                granted_ids = {item['document_id'] for item in access_info.get('items', []) if item['access_granted']}
            else:
                granted_ids = set()
                
            search_results = []
            for passage, doc, rev in rows:
                if doc.id in granted_ids:
                    # Only search the active revision by default unless history requested
                    if rev.id != doc.active_revision_id:
                        continue
                        
                    citation_id = str(uuid.uuid4())
                    citation = LibraryCitation(
                        id=citation_id,
                        passage_id=passage.id,
                        excerpt_hash="dummy_hash" # would hash passage content
                    )
                    session.add(citation)
                    
                    search_results.append(LibrarySearchResult(
                        passage_id=passage.id,
                        document_title=doc.title,
                        document_type=doc.doc_type,
                        document_id=doc.id,
                        revision_id=rev.id,
                        content=passage.content,
                        page=passage.page,
                        section=passage.section,
                        issue_date=rev.issue_date,
                        score=0.9,
                        citation_id=citation_id
                    ))
            
            await session.commit()
            return LibrarySearchResponse(
                status="success",
                query_date=datetime.datetime.utcnow().isoformat(),
                search_mode="lexical",
                results=search_results
            )

    async def read_passage(self, context: AgentContext, document_id: str, page: Optional[int] = None) -> LibraryReadResponse:
        # Resolves access, fetches specific page/passage, generates citation
        passages = ["Passage content..."]
        return LibraryReadResponse(
            document_id=document_id,
            revision_id="rev1",
            passages=passages,
            citation_ids=["cit1"],
            status="success"
        )
