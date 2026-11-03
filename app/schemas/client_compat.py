"""Original client field names; unsupported paths fail explicitly during intake."""
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from app.schemas.llm import ThinkingRequest
from app.core.surface import ClientContext
from typing import Literal


class ImageAttachment(BaseModel):
    media_type: Literal['image/png', 'image/jpeg', 'image/gif', 'image/webp']
    data: str = Field(min_length=1, max_length=2800000)


class DocumentAttachment(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default='', max_length=255)
    data: str = Field(min_length=1, max_length=2800000)
    size: int = Field(default=0, ge=0, le=2097152)


class ClientChat(BaseModel):
    message: str = Field(max_length=32000)
    session_id: int | None = None
    request_id: str | None = None
    model: str | None = None
    thinking: ThinkingRequest | None = None
    system_prompt: str | None = Field(default=None, max_length=6000)
    temperature: float | None = None
    attachments: list[ImageAttachment] = Field(default_factory=list, max_length=4)
    documents: list[DocumentAttachment] = Field(default_factory=list, max_length=4)
    client_context: ClientContext | None = None
    project_id: int | None = None
    workshop_project_id: str | None = Field(default=None,max_length=128)
    cwd: str | None = Field(default=None,max_length=2000)
    keep_messages: int | None = Field(default=None, ge=0)
    regenerate: bool = False
    addressed_agent_id: str | None = Field(default=None, min_length=1, max_length=64)
    reply_to: int | None = Field(default=None, ge=1)

    @field_validator("request_id")
    @classmethod
    def uuid(cls, value):
        return str(UUID(value)) if value is not None else None
