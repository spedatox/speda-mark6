from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.library import LibraryCitation, LibraryPassage
from app.middleware.auth import current_user
from app.services.hisar_library_client import get_library_client

router = APIRouter(prefix="/library", tags=["library"])

@router.get("/sources/{citation_id}")
async def get_citation_source(citation_id: str, _: str = Depends(current_user)):
    async with AsyncSessionLocal() as session:
        citation = await session.get(LibraryCitation, citation_id)
        if not citation:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Citation not found")
            
        passage = await session.get(LibraryPassage, citation.passage_id)
        if not passage:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Passage not found")
            
        # Optional: could check access again here using client.resolve_access
        
        # If client wants full blob instead of text passage, they might request it,
        # but usually citation resolves to the exact excerpt text.
        return {
            "citation_id": citation_id,
            "document_id": passage.revision.document_id if passage.revision else None,
            "content": passage.content,
            "page": passage.page,
            "section": passage.section
        }
