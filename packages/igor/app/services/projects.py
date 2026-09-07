# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Project context — turning the workspace a chat lives in into a system block.

A chat inside a project carries two things no loose chat has: standing
instructions the owner wrote once, and a knowledge base they uploaded once. Both
are injected here, as ONE block, on every turn of every chat in that project.

Why one block and why it sits where it does: the content is frozen for the life
of a turn and changes only when the owner edits the project, so it belongs in
the stable, cacheable half of the prefix — beside the memory block, ahead of
anything per-turn. It is deliberately NOT `_cache`-flagged (all four Anthropic
breakpoints are already spent; see the orchestrator's note on the episodic
block) — being byte-stable in front of the conversation breakpoint is enough.

Isolation is not enforced here. It is enforced at the only two places a project
can be reached — routers/projects.py and the chat router — both of which match
`projects.agent_id` against the addressed agent before anything is read. This
module is handed a project that has already cleared that gate.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.project import Project, ProjectFile

logger = logging.getLogger(__name__)


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


async def build_project_block(
    db: AsyncSession,
    project_id: int,
    agent_id: str,
) -> str:
    """Render the project's instructions + knowledge base as a system block.

    Returns "" when projects are disabled, the id names nothing, the project
    belongs to a DIFFERENT agent, or the project has neither instructions nor
    files — in every one of those cases the turn is simply a normal turn.
    """
    if not settings.projects_enabled:
        return ""

    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None or project.agent_id != agent_id:
        # A cross-agent id is a bug or a stale client, never a request to leak:
        # the block is dropped and the turn proceeds without it.
        if project is not None:
            logger.warning(
                "project_agent_mismatch",
                extra={"project_id": project_id, "owner": project.agent_id, "asked_by": agent_id},
            )
        return ""

    files = list(
        (
            await db.execute(
                select(ProjectFile)
                .where(ProjectFile.project_id == project.id)
                .order_by(ProjectFile.created_at.desc())
            )
        ).scalars().all()
    )

    instructions = (project.instructions or "").strip()
    if not instructions and not files:
        return ""

    parts: list[str] = [
        f"# PROJECT — {project.name}",
        "",
        "This conversation belongs to a project: a workspace with its own "
        "standing instructions and knowledge base. Everything below applies to "
        "this conversation and every other conversation in the project. It is "
        "the owner's own material, not something a tool returned.",
    ]
    if (project.description or "").strip():
        parts += ["", f"What it is: {project.description.strip()}"]

    if instructions:
        parts += [
            "",
            "## Project instructions",
            "",
            "Standing orders for this project, written by the owner. They sit "
            "UNDER your own operating contract — they shape tone, scope, format "
            "and priorities; they do not override a safety gate, an approval "
            "requirement, or the language contract.",
            "",
            instructions,
        ]

    if files:
        budget = max(0, settings.projects_knowledge_max_chars)
        included: list[ProjectFile] = []
        omitted: list[ProjectFile] = []
        spent = 0
        for f in files:
            body = f.content or ""
            # Newest-first until the budget runs out. A file that does not fit
            # is NOT silently dropped — it is named below, so the model knows
            # the base is larger than what it can see and can ask for it.
            if body and spent + len(body) <= budget:
                included.append(f)
                spent += len(body)
            else:
                omitted.append(f)

        parts += [
            "",
            "## Project knowledge",
            "",
            f"{len(files)} file(s) the owner attached to this project. Treat them "
            "as established background you already have — cite them by filename "
            "when you use one, and say so plainly when they do not cover "
            "something rather than filling the gap.",
        ]
        for f in included:
            parts += [
                "",
                f"### {f.name}  ({_fmt_bytes(f.size_bytes)})",
                "",
                f.content or "",
            ]
        if omitted:
            names = ", ".join(f.name for f in omitted)
            parts += [
                "",
                "### Not included in this turn",
                "",
                "These project files exist but did not fit the knowledge budget: "
                f"{names}. Do not guess at their contents — say they are "
                "attached but unread if they are what a question turns on.",
            ]

    return "\n".join(parts)
