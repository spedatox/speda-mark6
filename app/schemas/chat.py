from pydantic import BaseModel, field_validator
from app.schemas.llm import ThinkingRequest


class ChatRequest(BaseModel):
    message: str | list[dict]
    session_id: int | None = None
    model: str | None = None
    thinking: ThinkingRequest | None = None

    @field_validator("message")
    @classmethod
    def bounded_user_content(cls, value):
        if isinstance(value, str):
            if not value.strip() or len(value) > 32000:
                raise ValueError("A message must contain 1–32000 characters")
            return value
        if not value or len(value) > 16:
            raise ValueError("Expected up to 16 text/image blocks")
        text_size = 0
        for block in value:
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text_size += len(block["text"])
            elif block.get("type") == "image":
                source = block.get("source", {})
                if source.get("type") != "base64" or source.get("media_type") not in {
                    "image/png", "image/jpeg", "image/webp", "image/gif"} or not isinstance(source.get("data"), str) or len(source["data"]) > 8000000:
                    raise ValueError("Expected a bounded native base64 image")
            else:
                raise ValueError("Owner messages support text and native images only")
        if text_size > 32000:
            raise ValueError("Text exceeds the 32000-character message limit")
        return value


class ChatResponse(BaseModel):
    session_id: int
    request_id: str
