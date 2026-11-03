from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TriggerRequest(BaseModel):
    payload: dict
    output_mode: Literal["push", "silent"]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=64, pattern=r'^[\w-]+$')

    @field_validator('payload')
    @classmethod
    def bounded(cls, value):
        import json
        if len(json.dumps(value, ensure_ascii=False).encode()) > 24000:
            raise ValueError('Automation payload exceeds 24 KiB')
        return value


class TriggerResponse(BaseModel):
    accepted: bool
    request_id: str
    run_id: str | None = None
