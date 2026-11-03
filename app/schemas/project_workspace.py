from pydantic import Field
from app.schemas.coordination import Contract


class WorkspaceCreate(Contract):
    agent_id: str = 'optimus'
    name: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=2000)
    description: str = Field(default='', max_length=4000)
    instructions: str = Field(default='', max_length=6000)
    icon: str = Field(default='', max_length=32)
    color: str = Field(default='', max_length=32)


class WorkspacePatch(Contract):
    expected_version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    instructions: str | None = Field(default=None, max_length=6000)
    icon: str | None = Field(default=None, max_length=32)
    color: str | None = Field(default=None, max_length=32)
    pinned: bool | None = None
    archived: bool | None = None


class WorkspaceDirectory(Contract):
    parent: str = Field(min_length=1, max_length=2000)
    name: str = Field(min_length=1, max_length=100)
