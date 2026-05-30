from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: int | None = None
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None


class ChatResponse(BaseModel):
    session_id: int
    request_id: str
