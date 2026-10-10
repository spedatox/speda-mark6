import datetime
from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship

from app.database import Base

class LibrarySource(Base):
    """Maps one configured Hisar instance to an owner and records event cursor."""
    __tablename__ = "library_sources"

    id = Column(String, primary_key=True)  # Usually the owner's ID
    hisar_url = Column(String, nullable=False)
    sync_cursor = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

class LibraryDocument(Base):
    """Authoritative catalog mirror in Igor."""
    __tablename__ = "library_documents"

    id = Column(String, primary_key=True)
    source_id = Column(String, ForeignKey("library_sources.id"), nullable=False)
    title = Column(String, nullable=False)
    aliases = Column(JSON, nullable=False)
    category = Column(String, nullable=True)
    doc_type = Column(String, nullable=True)
    institution = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    active_revision_id = Column(String, nullable=True)
    access_policy = Column(JSON, nullable=False)
    is_archived = Column(Boolean, default=False, nullable=False)
    is_tombstone = Column(Boolean, default=False, nullable=False)
    metadata_version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

class LibraryRevision(Base):
    """Authoritative revision mirror."""
    __tablename__ = "library_revisions"

    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("library_documents.id"), nullable=False)
    sequence = Column(Integer, nullable=False)
    blob_sha256 = Column(String, nullable=False)
    original_filename = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    issue_date = Column(String, nullable=True)
    coverage_start = Column(String, nullable=True)
    coverage_end = Column(String, nullable=True)
    term = Column(String, nullable=True)
    superseded_by = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)

class LibraryIndexGeneration(Base):
    """Extraction and embedding jobs status."""
    __tablename__ = "library_index_generations"

    id = Column(String, primary_key=True)
    revision_id = Column(String, ForeignKey("library_revisions.id"), nullable=False)
    status = Column(String, nullable=False)  # pending, processing, completed, failed
    fingerprint = Column(String, nullable=False) # e.g. text_extractor_v1 + embedding_model
    failure_details = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

class LibraryPassage(Base):
    """Lexical passages."""
    __tablename__ = "library_passages"

    id = Column(String, primary_key=True)
    generation_id = Column(String, ForeignKey("library_index_generations.id"), nullable=False)
    revision_id = Column(String, ForeignKey("library_revisions.id"), nullable=False)
    content = Column(String, nullable=False)
    page = Column(Integer, nullable=True)
    section = Column(String, nullable=True)
    coverage_start = Column(String, nullable=True)
    coverage_end = Column(String, nullable=True)
    needs_review = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

class LibraryEmbedding(Base):
    """Embeddings for passages."""
    __tablename__ = "library_embeddings"

    passage_id = Column(String, ForeignKey("library_passages.id"), primary_key=True)
    vector = Column(JSON, nullable=False)  # We will use JSON to store vector if we don't have pgvector, since we just do cosine score in memory or use a specialized sqlite-vss/vec extension. Or just store json and decode for numpy.
    model_fingerprint = Column(String, nullable=False)

class LibraryCitation(Base):
    """Durable reference for chat resolution."""
    __tablename__ = "library_citations"

    id = Column(String, primary_key=True)
    passage_id = Column(String, ForeignKey("library_passages.id"), nullable=False)
    excerpt_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
