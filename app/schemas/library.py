"""Evidence and readiness returned by the Hall of Records."""
from pydantic import BaseModel, Field, SecretStr, ConfigDict


class LibrarySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semantic_enabled: bool
    ocr_enabled: bool
    ocr_languages: str = Field(default="tur+eng", pattern=r"^[a-z]{3}(?:\+[a-z]{3}){0,4}$")
    embedding_api_key: SecretStr | None = Field(default=None, max_length=512)


class LibraryProgressRequest(BaseModel):
    document_ids: list[str] = Field(max_length=100)


class LibrarySearchResult(BaseModel):
    passage_id: str
    document_title: str
    document_type: str | None = None
    document_id: str
    revision_id: str
    content: str
    page: int | None = None
    section: str | None = None
    issue_date: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    score: float = 0
    citation_id: str
    citation_url: str
    historical: bool = False
    needs_review: bool = False


class LibrarySearchResponse(BaseModel):
    status: str
    query_date: str
    search_mode: str = "keyword"
    results: list[LibrarySearchResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LibraryListResult(BaseModel):
    id: str
    title: str
    category: str | None = None
    type: str | None = None
    active_revision_id: str | None = None
    index_status: str
    issue_date: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None


class LibraryListResponse(BaseModel):
    documents: list[LibraryListResult] = Field(default_factory=list)
    has_more: bool = False


class LibraryReadResponse(BaseModel):
    document_id: str
    revision_id: str | None = None
    passages: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    results: list[LibrarySearchResult] = Field(default_factory=list)
    status: str
    next_offset: int | None = None
    warnings: list[str] = Field(default_factory=list)
