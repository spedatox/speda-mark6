from typing import List, Optional
from pydantic import Field
from app.skills.base import BaseSkill
from app.core.context import AgentContext
from app.services.library import LibraryService

class ListLibrarySkill(BaseSkill):
    """
    List available documents in the shared IACON reference library.
    Use this to browse for relevant academic schedules, transcripts, menus, or ebooks.
    Do not use this to search within a document; use search_library for that.
    Returns metadata about available documents including their ID, title, and type.
    """
    name = "list_library"
    read_only = True

    category: Optional[str] = Field(None, description="Optional category to filter by")
    doc_type: Optional[str] = Field(None, description="Optional document type to filter by")
    include_archived: bool = Field(False, description="Include archived documents")

    async def _run(self, context: AgentContext, **kwargs) -> str:
        service = LibraryService()
        resp = await service.list_documents(
            context, 
            category=self.category, 
            doc_type=self.doc_type, 
            include_archived=self.include_archived
        )
        
        if not resp.documents:
            return "No documents found in the library."
            
        out = ["Available documents in IACON library:"]
        for d in resp.documents:
            out.append(f"- ID: {d.id} | Title: {d.title} | Type: {d.type or 'unknown'} | Status: {d.index_status}")
        return "\n".join(out)


class SearchLibrarySkill(BaseSkill):
    """
    Search the shared IACON reference library for specific information.
    Use this to find exact passages across all authorized library documents.
    Provide a specific search query. Returns bounded passages with citation IDs.
    You must cite the returned citation_id when presenting facts to the user.
    """
    name = "search_library"
    read_only = True

    query: str = Field(..., description="The search query")
    document_ids: Optional[List[str]] = Field(None, description="Restrict search to specific document IDs")

    async def _run(self, context: AgentContext, **kwargs) -> str:
        service = LibraryService()
        resp = await service.search(context, self.query, self.document_ids)
        
        if not resp.results:
            return "No matching passages found in the library."
            
        out = [f"Search results for '{self.query}':"]
        for r in resp.results:
            out.append(f"--- Document: {r.document_title} (ID: {r.document_id}) ---")
            if r.page:
                out.append(f"Page: {r.page}")
            out.append(f"Citation ID: {r.citation_id}")
            out.append(r.content)
            out.append("")
        return "\n".join(out)


class ReadLibrarySkill(BaseSkill):
    """
    Read specific pages or sections of a document in the IACON reference library.
    Use this when you know the exact document ID and want to read it linearly.
    Returns the exact passage content and citation IDs to cite in your response.
    """
    name = "read_library"
    read_only = True

    document_id: str = Field(..., description="The ID of the document to read")
    page: Optional[int] = Field(None, description="Specific page number to read")

    async def _run(self, context: AgentContext, **kwargs) -> str:
        service = LibraryService()
        resp = await service.read_passage(context, self.document_id, self.page)
        
        out = [f"Reading document {resp.document_id}:"]
        for p, cid in zip(resp.passages, resp.citation_ids):
            out.append(f"[Citation: {cid}]\n{p}")
        return "\n".join(out)
