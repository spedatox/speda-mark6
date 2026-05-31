from pydantic import BaseModel


class ImageAttachment(BaseModel):
    media_type: str   # image/jpeg | image/png | image/gif | image/webp
    data: str         # base64-encoded image bytes (no data: URI prefix)


class ChatRequest(BaseModel):
    message: str
    session_id: int | None = None
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    attachments: list[ImageAttachment] = []


class ChatResponse(BaseModel):
    session_id: int
    request_id: str
