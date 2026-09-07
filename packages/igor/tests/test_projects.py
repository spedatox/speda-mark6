# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Project isolation and context injection.

The thing worth pinning here is not CRUD, it is the boundary: a project belongs
to one agent, and neither its chat list nor its knowledge base may reach another
one. Every leak this feature could produce runs through two functions —
`SessionManager.list_sessions` (which chats are in a project) and
`services.projects.build_project_block` (what a turn is told about it) — so both
are tested against a deliberately cross-agent lookup, not only the happy path.
"""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.config import settings
from app.core.session_manager import SessionManager
from app.database import Base
from app.models.project import Project, ProjectFile
from app.models.session import Session
from app.models.user import User
from app.services.projects import build_project_block


@pytest.fixture
async def db_factory(tmp_path):
    """A throwaway SQLite DB configured like production — FK enforcement ON."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'projects.db'}", poolclass=NullPool
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory) -> dict:
    """One project per agent, each with a chat and a knowledge file."""
    async with factory() as db:
        db.add(User(id=1, name="owner", timezone="UTC"))
        await db.commit()

        sentinel = Project(
            user_id=1, agent_id="sentinel", name="Q3 Budget",
            instructions="Always show the arithmetic.",
        )
        ultron = Project(user_id=1, agent_id="ultron", name="Finals")
        db.add_all([sentinel, ultron])
        await db.commit()
        await db.refresh(sentinel)
        await db.refresh(ultron)

        db.add_all([
            ProjectFile(
                project_id=sentinel.id, name="holdings.csv",
                size_bytes=120, chars=22, content="TICKER,QTY\nACME,100",
            ),
            Session(
                user_id=1, agent_id="sentinel", project_id=sentinel.id,
                triggered_by="user", model_used="m", title="in project",
            ),
            Session(
                user_id=1, agent_id="sentinel",
                triggered_by="user", model_used="m", title="loose chat",
            ),
            Session(
                user_id=1, agent_id="ultron", project_id=ultron.id,
                triggered_by="user", model_used="m", title="ultron chat",
            ),
        ])
        await db.commit()
        return {"sentinel": sentinel.id, "ultron": ultron.id}


async def test_project_chats_are_scoped_to_their_project(db_factory):
    ids = await _seed(db_factory)
    manager = SessionManager()
    async with db_factory() as db:
        in_project = await manager.list_sessions(
            db, user_id=1, agent_id="sentinel", project_id=ids["sentinel"]
        )
        assert [s.title for s in in_project] == ["in project"]

        # No project filter → the whole history, loose chats included. The
        # sidebar badges project rows rather than hiding them.
        everything = await manager.list_sessions(db, user_id=1, agent_id="sentinel")
        assert {s.title for s in everything} == {"in project", "loose chat"}


async def test_another_agents_project_id_matches_nothing(db_factory):
    """The isolation case: Ultron asking for Sentinel's project by id gets an
    empty list, not Sentinel's conversation."""
    ids = await _seed(db_factory)
    manager = SessionManager()
    async with db_factory() as db:
        leaked = await manager.list_sessions(
            db, user_id=1, agent_id="ultron", project_id=ids["sentinel"]
        )
        assert leaked == []


async def test_project_block_carries_instructions_and_knowledge(db_factory):
    ids = await _seed(db_factory)
    async with db_factory() as db:
        block = await build_project_block(db, ids["sentinel"], "sentinel")
    assert "Q3 Budget" in block
    assert "Always show the arithmetic." in block
    assert "holdings.csv" in block
    assert "ACME,100" in block


async def test_project_block_refuses_a_cross_agent_read(db_factory):
    """The same project, asked for by the wrong agent, yields nothing at all —
    the turn runs as an ordinary turn rather than being handed the workspace."""
    ids = await _seed(db_factory)
    async with db_factory() as db:
        assert await build_project_block(db, ids["sentinel"], "ultron") == ""


async def test_project_block_is_empty_when_projects_are_disabled(db_factory):
    ids = await _seed(db_factory)
    original = settings.projects_enabled
    settings.projects_enabled = False
    try:
        async with db_factory() as db:
            assert await build_project_block(db, ids["sentinel"], "sentinel") == ""
    finally:
        settings.projects_enabled = original


async def test_knowledge_budget_names_the_files_it_could_not_fit(db_factory):
    """A file that does not fit is NAMED, never silently dropped — otherwise the
    model reads a partial knowledge base as a complete one."""
    ids = await _seed(db_factory)
    async with db_factory() as db:
        db.add(ProjectFile(
            project_id=ids["sentinel"], name="huge.txt",
            size_bytes=1, chars=10, content="x" * 10,
        ))
        await db.commit()

    original = settings.projects_knowledge_max_chars
    settings.projects_knowledge_max_chars = 5   # fits neither file
    try:
        async with db_factory() as db:
            block = await build_project_block(db, ids["sentinel"], "sentinel")
    finally:
        settings.projects_knowledge_max_chars = original

    assert "Not included in this turn" in block
    assert "huge.txt" in block and "holdings.csv" in block
    assert "ACME,100" not in block


def test_unreadable_upload_is_refused_not_stored_as_a_note():
    """A chat attachment degrades to "[Attachment … could not be read]". A
    knowledge file must not: that sentence would be STORED, and every later turn
    in the project would read it as a fact about the subject."""
    import base64

    from app.services.attachments import extract_body, extract_text

    payload = base64.b64encode(b"notapdf").decode()

    body, note = extract_body("broken.pdf", "application/pdf", payload)
    assert body == ""
    assert "broken.pdf" in note          # the router raises this verbatim as a 400

    # The chat path still degrades rather than failing the turn.
    assert "broken.pdf" in extract_text("broken.pdf", "application/pdf", payload)


def test_a_readable_upload_extracts_its_body_without_the_chat_envelope():
    """The knowledge block writes its own `### filename` heading, so the stored
    text is the body alone — not the "[Attached file: …]" wrapper the chat turn
    needs."""
    import base64

    from app.services.attachments import extract_body

    body, note = extract_body(
        "notes.txt", "text/plain", base64.b64encode("VLAN 10".encode()).decode()
    )
    assert note == ""
    assert body == "VLAN 10"
