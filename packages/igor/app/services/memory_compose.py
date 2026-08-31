# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Composing the two narrative memory surfaces (v3 §4.2).

Six of the eight files are assembled by `memory_render.py` — a pure function,
no model, no judgement. Two are not, and the distinction is not arbitrary:

  **owner.md** is a biography. Rendering it as forty dated bullets would be a
  worse artifact for the person who reads it than the prose it replaces, and the
  owner reads this one.

  **current.md** is a snapshot of what matters right now. The FILTER is
  mechanical (`domain=state, valid_until IS NULL`) but the SELECTION is not —
  a snapshot that lists everything currently true has stopped being a snapshot.

So these are composed by a model, from the record, nightly. Two guardrails make
that safe rather than a licence to invent:

1. **Every claim cites observation ids**, carried in an HTML comment so the
   prose stays readable. `verify_citations` then checks mechanically that every
   cited id exists and is live — the no-fabrication rule stops being something
   we ask for and becomes something we check.
2. **A failed composition never overwrites a good one.** If the model returns
   nothing usable, or cites ids that do not exist, the previous version stands.
   A stale biography is recoverable; a hallucinated one that silently replaced
   the true one is not.
"""

import logging
import re
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory_file import MemoryFile
from app.models.observation import Observation
from app.services.memory_store import record_revision

logger = logging.getLogger(__name__)

OWNER_PATH = "/memories/owner.md"
CURRENT_PATH = "/memories/current.md"

# ── COMPOSITION IS OFF, for the same reason rendering is (v4 §1) ─────────────
#
# owner.md is a 15 KB narrative in chapters — "Origins", "The Uludağ Years",
# "The Istanbul Summer (2024)". Composing it from the record would rewrite a life
# story from whatever facts happened to be extracted, and the record does not yet
# hold enough of it: the seed's bullet parser could not read prose at all, so the
# biography entered the record only through a best-effort model pass.
#
# Empty means `compose` runs only when an operator asks for it explicitly
# (POST /admin/memory/compose) and never from the post-turn fallback. Re-enable a
# path here when the record demonstrably backs it — `compare_to_stored` reports
# the backing count per composed file.
COMPOSED_FILES: tuple[str, ...] = ()

# The paths themselves, for the shadow report and the admin endpoint.
COMPOSABLE_PATHS: tuple[str, ...] = (OWNER_PATH, CURRENT_PATH)

_CITATION = re.compile(r"<!--\s*ids?:\s*([\d,\s]+)\s*-->", re.I)

_OWNER_SYSTEM = (
    "You maintain a person's biography from a list of recorded facts. You write "
    "plain, dense prose. You never state anything the facts do not support."
)

_OWNER_PROMPT = """\
Below are the recorded biographical facts about the owner, each with an id.
Compose his biography: who he is and what shaped him.

Rules:
- Organise by theme or era, not as a list. Short paragraphs.
- Every paragraph ends with an HTML comment citing the ids it rests on, exactly \
like `<!-- ids: 12, 13, 40 -->`. A paragraph with no citation is invalid.
- Use ONLY these facts. Do not add connective claims that are not supported — \
"he then moved to Ankara" is only allowed if a fact says so.
- Keep identity constants (name, codename, how he is addressed) at the top.
- No preamble, no headings above "## ", no commentary. Return the body only.

FACTS:
{facts}"""

_CURRENT_SYSTEM = (
    "You maintain a short snapshot of what is active in a person's life right "
    "now, from a list of recorded facts. You select ruthlessly and never invent."
)

_CURRENT_PROMPT = """\
Today is {today}. Below are the facts currently true of the owner's life, each
with an id. Produce the snapshot of what is genuinely ACTIVE right now.

Rules:
- 3-10 bullets. Selection is the job: if everything is listed it is not a snapshot.
- Each bullet ends with its citation, exactly like `<!-- ids: 7 -->`.
- Preserve causal and until-when phrasing where the facts carry it ("in Bursa \
because the semester ended") — that is what makes an entry self-expiring.
- Use ONLY these facts. Return the bullet list only, no header, no preamble.

FACTS:
{facts}"""


def _facts_block(rows: list[Observation]) -> str:
    lines = []
    for obs in sorted(rows, key=lambda o: (o.valid_from or date.min, o.id)):
        when = f" (since {obs.valid_from})" if obs.valid_from else ""
        lines.append(f"[id:{obs.id}]{when} {obs.content}")
    return "\n".join(lines)


def verify_citations(text: str, allowed: set[int]) -> tuple[bool, list[int]]:
    """Check every cited id exists in the source set.

    Returns (ok, unknown_ids). A composition citing an id that is not in the
    facts it was given has invented something — either the claim or the evidence
    — and is rejected wholesale rather than partially trusted.
    """
    cited: set[int] = set()
    for match in _CITATION.finditer(text):
        for part in match.group(1).split(","):
            part = part.strip()
            if part.isdigit():
                cited.add(int(part))
    unknown = sorted(cited - allowed)
    return (not unknown and bool(cited)), unknown


async def _live_rows(db: AsyncSession, user_id: int, domain: str) -> list[Observation]:
    return list(
        (
            await db.execute(
                select(Observation).where(
                    Observation.user_id == user_id,
                    Observation.deleted_at.is_(None),
                    Observation.subject == "owner",
                    Observation.domain == domain,
                    Observation.valid_until.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )


async def _write(
    db: AsyncSession, user_id: int, path: str, body: str, request_id: str
) -> bool:
    existing = (
        await db.execute(
            select(MemoryFile).where(
                MemoryFile.user_id == user_id, MemoryFile.path == path
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.content == body:
        return False
    before = existing.content if existing else ""
    if existing is None:
        db.add(MemoryFile(user_id=user_id, path=path, content=body))
    else:
        existing.content = body
        existing.updated_at = datetime.now(timezone.utc)
    await record_revision(
        db, user_id=user_id, path=path, author="orion",
        action="compose", before=before, after=body, request_id=request_id,
    )
    await db.commit()
    return True


async def compose(
    user_id: int, model: str, *, request_id: str = "", today: date | None = None
) -> dict:
    """
    Compose owner.md and current.md from the record.

    No DB session is held across either model call: facts are read, the session
    closes, the model runs, a fresh session writes. Each file is independent —
    a failure to compose the biography does not stop the snapshot.
    """
    from app.database import AsyncSessionLocal
    from app.services.llm_client import LLMClient

    day = today or date.today()
    report: dict = {}

    # ── read ─────────────────────────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        bio_rows = await _live_rows(db, user_id, "biography")
        state_rows = await _live_rows(db, user_id, "state")
    bio_ids = {o.id for o in bio_rows}
    state_ids = {o.id for o in state_rows}

    client = LLMClient()

    # ── owner.md ─────────────────────────────────────────────────────────────
    if bio_rows:
        try:
            resp = await client.create_message(
                model=model,
                system=_OWNER_SYSTEM,
                messages=[{"role": "user",
                           "content": _OWNER_PROMPT.format(facts=_facts_block(bio_rows))}],
                max_tokens=2048,
            )
            body = (resp.content[0].text.strip() if resp.content else "")
            ok, unknown = verify_citations(body, bio_ids)
            if not body:
                report["owner"] = "skipped: empty response"
            elif not ok:
                report["owner"] = f"rejected: cited unknown ids {unknown}" if unknown \
                    else "rejected: no citations"
                logger.warning("compose_owner_rejected", extra={"unknown": unknown})
            else:
                text = (
                    "# Owner Profile — who he is, and what shaped him\n\n"
                    f"_Composed from the record on {day} by orion. Do not edit — "
                    f"correct the underlying facts instead._\n\n"
                    f"{body}\n"
                )
                async with AsyncSessionLocal() as db:
                    changed = await _write(db, user_id, OWNER_PATH, text, request_id)
                report["owner"] = "written" if changed else "unchanged"
        except Exception as e:  # noqa: BLE001
            report["owner"] = f"error: {e}"
            logger.error("compose_owner_error", extra={"error": str(e)})
    else:
        report["owner"] = "skipped: no biographical facts recorded"

    # ── current.md ───────────────────────────────────────────────────────────
    if state_rows:
        try:
            resp = await client.create_message(
                model=model,
                system=_CURRENT_SYSTEM,
                messages=[{"role": "user",
                           "content": _CURRENT_PROMPT.format(
                               today=day, facts=_facts_block(state_rows))}],
                max_tokens=1024,
            )
            body = (resp.content[0].text.strip() if resp.content else "")
            ok, unknown = verify_citations(body, state_ids)
            if not body:
                report["current"] = "skipped: empty response"
            elif not ok:
                report["current"] = f"rejected: cited unknown ids {unknown}" if unknown \
                    else "rejected: no citations"
                logger.warning("compose_current_rejected", extra={"unknown": unknown})
            else:
                text = (
                    "# Current — what's active right now\n\n"
                    f"_Composed from the record on {day} by orion. Do not edit — "
                    f"correct the underlying facts instead._\n\n"
                    f"{body}\n"
                )
                async with AsyncSessionLocal() as db:
                    changed = await _write(db, user_id, CURRENT_PATH, text, request_id)
                report["current"] = "written" if changed else "unchanged"
        except Exception as e:  # noqa: BLE001
            report["current"] = f"error: {e}"
            logger.error("compose_current_error", extra={"error": str(e)})
    else:
        report["current"] = "skipped: no active states recorded"

    logger.info("memory_composed", extra={"request_id": request_id, **report})
    return report
