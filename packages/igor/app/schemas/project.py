# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    """A new workspace. `agent_id` is the isolation key — the router refuses an
    unknown one, and everything about the project is thereafter invisible to
    every other agent."""
    agent_id: str = "speda"
    name: str
    description: str | None = None
    instructions: str | None = None
    icon: str | None = None
    color: str | None = None


class ProjectUpdate(BaseModel):
    """Partial update. Every field optional; `None` means "leave alone", which
    is why clearing a field is done by sending "" rather than null.

    `agent_id` is deliberately absent — a project cannot be handed to another
    agent, because its chats and knowledge were produced under one agent's
    contract and moving them would be a leak with extra steps."""
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    icon: str | None = None
    color: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class ProjectFileUpload(BaseModel):
    """One knowledge file. Same wire shape as a chat attachment (schemas/chat.py
    DocumentAttachment) so both clients reuse the one base64 encoder they
    already have. The bytes are extracted to text and the original discarded —
    see models/project.ProjectFile."""
    name: str
    media_type: str = ""
    data: str          # base64-encoded file bytes (no data: URI prefix)
    size: int = 0
