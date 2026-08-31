# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Wire schemas for the Skyfall Protocol. See services/skyfall.py."""

from pydantic import BaseModel, Field


class SkyfallProject(BaseModel):
    """One launch target, as it leaves the server.

    Header VALUES are never here — `headers` carries the NAMES mapped to a mask.
    The owner needs to see which headers a project sends in order to manage them;
    nothing outside the service needs the values, and a project's
    `Authorization: Bearer …` must not be one round-trip away from a chat log.

    `has_body` exists for the same reason in the other direction: the pane shows
    that a body is configured without the body having to travel to draw a badge.
    """

    id: str
    name: str
    description: str = ""
    url: str
    method: str = "POST"
    body: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    has_body: bool = False
    countdown_seconds: int = 10
    created_at: str = ""
    updated_at: str = ""
    last_fired_at: str = ""
    last_result: str = ""


class SkyfallProjectWrite(BaseModel):
    """A project as the owner submits it. `id` empty = create.

    A header value sent back as the mask means "leave this one alone" — the form
    was rendered from masked data, so anything the owner did not retype must
    survive the save rather than being blanked.
    """

    id: str = Field(default="", max_length=64)
    name: str = Field(max_length=80)
    description: str = Field(default="", max_length=600)
    url: str = Field(max_length=2048)
    method: str = Field(default="POST", max_length=10)
    body: str = Field(default="", max_length=20_000)
    headers: dict[str, str] = Field(default_factory=dict)
    countdown_seconds: int = Field(default=10, ge=3, le=300)


class SkyfallArm(BaseModel):
    """What the client needs to draw the countdown — and nothing more.

    No body, no headers. The request is assembled server-side when the clock
    reaches zero, so a client that never holds the secret cannot leak it and a
    client that cannot alter the payload cannot turn an armed countdown into a
    different request than the one the owner armed.
    """

    project_id: str
    name: str
    description: str = ""
    method: str = "POST"
    url: str = ""
    countdown_seconds: int = 10
    armed_at: str = ""


class SkyfallFireRequest(BaseModel):
    """Sent by the client when a countdown reached zero — never by an agent."""

    project_id: str = Field(max_length=64)


class SkyfallAbortRequest(BaseModel):
    """Sent when the owner stopped the clock. Recorded, because 'did that fire?'
    must be answerable afterwards and silence cannot answer it."""

    project_id: str = Field(max_length=64)
    remaining_seconds: float = 0.0


class SkyfallResult(BaseModel):
    """What happened when the clock hit zero.

    `fired` and `ok` are separate on purpose: a request that went out and came
    back 500 is not the same event as a request that never left, and the screen
    must not render them the same way.
    """

    fired: bool = False
    ok: bool = False
    status: int = 0
    body: str = ""
    truncated: bool = False
    error: str = ""
    started_at: str = ""
    finished_at: str = ""
