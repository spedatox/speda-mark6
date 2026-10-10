from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class LibraryCitationInfo(BaseModel):
    id: str
    document_title: str
    document_id: str
    revision_id: str
    issue_date: Optional[str]
    page: Optional[int]
    section: Optional[str]

class LibrarySearchResult(BaseModel):
    passage_id: str
    document_title: str
    document_type: Optional[str]
    document_id: str
    revision_id: str
    content: str
    page: Optional[int]
    section: Optional[str]
    issue_date: Optional[str]
    score: float
    citation_id: str

class LibrarySearchResponse(BaseModel):
    status: str
    query_date: str
    search_mode: str
    results: List[LibrarySearchResult]
    warnings: List[str] = Field(default_factory=list)

class LibraryListResult(BaseModel):
    id: str
    title: str
    category: Optional[str]
    type: Optional[str]
    active_revision_id: Optional[str]
    index_status: Optional[str]

class LibraryListResponse(BaseModel):
    documents: List[LibraryListResult]

class LibraryReadResponse(BaseModel):
    document_id: str
    revision_id: str
    passages: List[str]
    citation_ids: List[str]
    status: str
