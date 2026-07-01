from pydantic import BaseModel


class ImageAttachment(BaseModel):
    media_type: str   # image/jpeg | image/png | image/gif | image/webp
    data: str         # base64-encoded image bytes (no data: URI prefix)


class DocumentAttachment(BaseModel):
    """A non-image file upload (PDF, DOCX, XLSX, PPTX, CSV, TXT, code, …). The
    backend extracts its text server-side and embeds it in the user turn as a
    text block, so it reaches the model identically on every provider."""
    name: str         # original filename, e.g. "q3_report.pdf"
    media_type: str   # MIME type as reported by the browser (may be "")
    data: str         # base64-encoded file bytes (no data: URI prefix)
    size: int = 0     # byte size, for the display chip


class ChatRequest(BaseModel):
    message: str
    session_id: int | None = None
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    attachments: list[ImageAttachment] = []
    documents: list[DocumentAttachment] = []

    # Regenerate / edit support. The DB is the source of truth for history, so a
    # client editing or regenerating must tell the backend to truncate first —
    # otherwise the model is re-fed its own prior answer and can't see the turn
    # afresh. Both are position-based (count of leading messages to keep),
    # which maps 1:1 onto the stored user/assistant rows in created_at order.
    keep_messages: int | None = None  # delete all but the first N messages, then proceed
    regenerate: bool = False          # re-run on existing history; do NOT add a new user message


class ChatResponse(BaseModel):
    session_id: int
    request_id: str
