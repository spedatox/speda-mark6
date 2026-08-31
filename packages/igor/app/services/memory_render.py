# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Rendering the derived memory surfaces from the observation record (v3 §4.1).

Six of the eight /memories files stop being written by anyone and become the
output of a pure function over `observations`. That is the whole v3 bargain: a
file cannot be misfiled, duplicated, or left stale if it is not stored but
computed.

Two properties this module must hold, because everything downstream leans on
them:

**Purity.** Same record in, byte-identical file out. No clock reads outside the
compression cut-offs (which take `today` as a parameter for exactly this
reason), no LLM, no ordering that depends on dict iteration or row arrival.
Byte-stability is what keeps the injected prompt block cacheable — under v2 that
took careful watermarking, here it is a property of the renderer.

**Totality.** Every live observation appears on exactly one surface. The routing
function `observations.target_file` is total by construction, and
`render_all` dispatches on it, so no fact can be recorded and then silently
render nowhere. The one thing worse than a misfiled fact is an invisible one.

The two remaining files — owner.md and current.md — are NOT rendered here. They
are prose and Orion composes them (v3 §4.2); assembling a biography out of
bullet points would be a worse artifact for the human who reads it.
"""

import logging
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.observation import Observation
from app.services.observations import target_file

logger = logging.getLogger(__name__)

# ── DERIVATION IS OFF (docs/MEMORY_ARCHITECTURE_V4.md §1) ────────────────────
#
# This was the six-file set rendered from the observation record. It is empty
# because rendering was measured against the owner's real files and every single
# one came out worse than what it replaced:
#
#   finance.md   15 KB of monthly ledgers with 71 table rows -> 56 loose
#                sentences. Every figure survived; every relationship between
#                them — which month, which statement, which repayment schedule —
#                did not.
#   dossier.md   six sections including "Explicit prohibitions" (hard behavioural
#                rules that govern how agents answer) collapsed into one heading
#                called "General", indistinguishable from a mild preference.
#   social.md    top-level headings were CATEGORIES (Professional, Personal), not
#                people; they became two fictional persons and the real people
#                were buried inside them as text.
#   projects.md  24 project sections with Features/Tech Stack/Team flattened.
#
# The mistake was not the file list. It was assuming one shape — a flat list of
# atomic attributed facts — describes all of memory. It describes one quarter of
# it (v4 §2). Structure is information: a month heading over an income table is
# not decoration around the facts, it IS the fact that those figures belong to
# that month.
#
# The record itself is not affected and keeps every property it earned — it is
# now the semantic INDEX over these documents rather than their source (v4 §3.4).
# Re-enable a file here only once it has a declared shape and a renderer that
# reproduces that shape; `compare_to_stored` is how you check before flipping.
RENDERED_FILES: tuple[str, ...] = ()

# sessions.md compression thresholds (v2 §2.2, now a rendering rule rather than
# an edit Orion has to perform). Raw detail is never the durable asset; the trend
# is. Expressed in days so the arithmetic is obvious at the call site.
SESSIONS_FULL_DETAIL_DAYS = 28    # ≤ 4 weeks: every entry in full
SESSIONS_WEEKLY_DAYS = 84         # ≤ 12 weeks: one line per week; older: per month

_HEADER_NOTE = (
    "<!-- RENDERED FILE — do not edit. Generated from the observation record\n"
    "     (docs/MEMORY_ARCHITECTURE_V3.md §4.1). Edits here are overwritten on\n"
    "     the next render; correct the underlying facts instead. -->"
)


def _sort_key(obs: Observation):
    """Stable ordering: newest first, ties broken by id so output is deterministic
    even when two observations share a timestamp to the microsecond."""
    return (-obs.created_at.timestamp(), -obs.id)


def _dated(obs: Observation) -> str:
    """The date that matters for display: when the fact started holding, falling
    back to when it was recorded."""
    return (obs.valid_from or obs.created_at.date()).isoformat()


def _entry(obs: Observation, *, show_observer: bool = True) -> str:
    """One observation as a markdown bullet, carrying its id so the owner (and
    Orion) can act on it without a lookup."""
    tag = f"[{_dated(obs)}"
    if show_observer:
        tag += f", {obs.observer}"
    tag += "]"
    line = f"- {tag} {obs.content}"
    extras = []
    if obs.reinforcement_count > 1:
        extras.append(f"seen {obs.reinforcement_count}×")
    if obs.confidence:
        extras.append(f"{obs.confidence} confidence")
    if obs.level != "explicit":
        extras.append(obs.level)
    extras.append(f"id:{obs.id}")
    return f"{line}  <sub>({' · '.join(extras)})</sub>"


def _fact_lines(text: str) -> int:
    """Count real content entries in a memory file.

    Bullets only, and not the `- _(none recorded)_` placeholders the renderers
    emit for an empty section — those are structure, not knowledge. This is what
    `commit_rendered`'s blanking interlock measures, so it has to distinguish a
    file that says nothing from a file that has nothing.
    """
    count = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("- ", "* ")) and not stripped[2:].lstrip().startswith("_"):
            count += 1
    return count


def _person(subject: str) -> str:
    return subject.split(":", 1)[1] if ":" in subject else subject


def _empty(title: str, note: str) -> str:
    return f"# {title}\n\n{_HEADER_NOTE}\n\n_{note}_\n"


# ── Per-file renderers ────────────────────────────────────────────────────────

def render_dossier(rows: list[Observation]) -> str:
    """Observed preferences, grouped by the kind of pattern they are.

    v2 kept four hand-maintained sections (Likes / Dislikes / Wants / Open
    questions). They are retired here, deliberately: the split was prose
    scaffolding for a file nobody could query, it required every writer to agree
    on which bucket a preference fell into, and nothing checked that they did.
    `pattern_type` is a vocabulary the record already validates, so grouping by
    it is consistent by construction rather than by convention.
    """
    if not rows:
        return _empty(
            "Dossier — what we've observed about how he wants to be treated",
            "Nothing observed yet.",
        )
    groups: dict[str, list[Observation]] = defaultdict(list)
    for obs in rows:
        groups[obs.pattern_type or "general"].append(obs)

    out = [
        "# Dossier — what we've observed about how he wants to be treated",
        "",
        _HEADER_NOTE,
        "",
        "_What he likes, dislikes and wants, and in what manner — stated and "
        "inferred. Act on it silently; never read it aloud or cite it to him._",
        "",
    ]
    for kind in sorted(groups):
        out.append(f"## {kind.capitalize()}")
        out.append("")
        for obs in sorted(groups[kind], key=_sort_key):
            out.append(_entry(obs))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_sessions(rows: list[Observation], today: date) -> str:
    """The training log, compressed by age.

    Recent sessions in full, then one line per week, then per month. The
    compression is a rendering rule rather than something Orion performs, so it
    can never be half-applied and the raw observations behind an old rolled-up
    week are still queryable — the file gets shorter, the record does not.
    """
    if not rows:
        return _empty("Sessions — training log", "No sessions logged yet.")

    recent, weekly, monthly = [], defaultdict(list), defaultdict(list)
    for obs in rows:
        day = obs.valid_from or obs.created_at.date()
        age = (today - day).days
        if age <= SESSIONS_FULL_DETAIL_DAYS:
            recent.append(obs)
        elif age <= SESSIONS_WEEKLY_DAYS:
            iso = day.isocalendar()
            weekly[(iso[0], iso[1])].append(obs)
        else:
            monthly[(day.year, day.month)].append(obs)

    out = ["# Sessions — training log", "", _HEADER_NOTE, ""]
    if recent:
        out += ["## Recent (last 4 weeks)", ""]
        for obs in sorted(recent, key=_sort_key):
            out.append(_entry(obs, show_observer=False))
        out.append("")
    if weekly:
        out += ["## Weekly summaries (4–12 weeks ago)", ""]
        for (year, week) in sorted(weekly, reverse=True):
            items = weekly[(year, week)]
            out.append(f"- **{year}-W{week:02d}** — {len(items)} session(s): "
                       + "; ".join(o.content for o in sorted(items, key=_sort_key)[:4]))
        out.append("")
    if monthly:
        out += ["## Monthly trend (older)", ""]
        for (year, month) in sorted(monthly, reverse=True):
            items = monthly[(year, month)]
            out.append(f"- **{year}-{month:02d}** — {len(items)} session(s) logged")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_finance(rows: list[Observation]) -> str:
    """The financial ledger — current figures only.

    Superseded figures are absent by construction: they carry a `valid_until`
    and therefore render into history.md instead. This file cannot show a stale
    balance, which is the failure v2's `str_replace`-in-place was one missed edit
    away from at all times.
    """
    if not rows:
        return _empty("Finance — the owner's financial source of truth",
                      "No financial facts recorded yet.")
    out = [
        "# Finance — the owner's financial source of truth",
        "",
        _HEADER_NOTE,
        "",
        "_Current figures. Superseded ones are in history.md with their date "
        "ranges — nothing is overwritten._",
        "",
    ]
    for obs in sorted(rows, key=_sort_key):
        out.append(_entry(obs))
    return "\n".join(out).rstrip() + "\n"


def render_projects(rows: list[Observation]) -> str:
    """One section per project, newest entry first within each."""
    if not rows:
        return _empty("Active Projects", "No active projects recorded.")
    groups: dict[str, list[Observation]] = defaultdict(list)
    for obs in rows:
        groups[_person(obs.subject)].append(obs)

    out = ["# Active Projects", "", _HEADER_NOTE, "",
           "_Active and paused work. Finished projects are in history.md._", ""]
    for name in sorted(groups):
        out.append(f"## {name}")
        out.append("")
        for obs in sorted(groups[name], key=_sort_key):
            out.append(_entry(obs))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_social(rows: list[Observation]) -> str:
    """One section per person: a Who block from biography facts, an Events log
    from dated events. The v2 schema, now guaranteed rather than requested — a
    person cannot exist here without the shape, because the shape is the
    renderer."""
    if not rows:
        return _empty("Social — people who matter to the owner",
                      "No people recorded yet.")
    groups: dict[str, list[Observation]] = defaultdict(list)
    for obs in rows:
        groups[_person(obs.subject)].append(obs)

    out = ["# Social — people who matter to the owner", "", _HEADER_NOTE, ""]
    for name in sorted(groups):
        items = groups[name]
        who = [o for o in items if o.domain == "biography"]
        events = [o for o in items if o.domain != "biography"]
        out.append(f"## {name}")
        out.append("")
        out.append("**Who:**")
        if who:
            for obs in sorted(who, key=_sort_key):
                out.append(_entry(obs))
        else:
            out.append("- _(not yet established)_")
        out.append("")
        out.append("**Events:**")
        if events:
            for obs in sorted(events, key=_sort_key):
                out.append(_entry(obs, show_observer=False))
        else:
            out.append("- _(none recorded)_")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_history(rows: list[Observation]) -> str:
    """Everything that has stopped being true, whatever it was about.

    Under v2 this file was filled by demotion — a text move that could half-fail
    and lose the fact. Here a row appears because `valid_until` is set, so the
    file is a view and nothing was ever relocated to produce it.
    """
    if not rows:
        return _empty("History — what is no longer true",
                      "Nothing has ended yet.")
    groups: dict[str, list[Observation]] = defaultdict(list)
    for obs in rows:
        label = _person(obs.subject) if obs.subject != "owner" else obs.domain.capitalize()
        groups[label].append(obs)

    out = [
        "# History — what is no longer true",
        "",
        _HEADER_NOTE,
        "",
        "_Facts with an end date, carrying their active range. Nothing here is "
        "deleted and nothing here is current — consult it only to answer "
        "\"when did X?\"._",
        "",
    ]
    for label in sorted(groups):
        out.append(f"## {label}")
        out.append("")
        for obs in sorted(groups[label], key=lambda o: (-(o.valid_until or date.min).toordinal(), -o.id)):
            span = f"{obs.valid_from} → {obs.valid_until}" if obs.valid_from else f"until {obs.valid_until}"
            extras = [f"id:{obs.id}"]
            if obs.superseded_by:
                extras.append(f"replaced by id:{obs.superseded_by}")
            out.append(f"- [{span}] {obs.content}  <sub>({' · '.join(extras)})</sub>")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ── Dispatch ──────────────────────────────────────────────────────────────────

async def render_all(
    db: AsyncSession, user_id: int, *, today: date | None = None
) -> dict[str, str]:
    """
    Render every derived surface for one owner.

    Partitions the live record by `target_file` — the same total function the
    write path documents — so a fact recorded with any valid (subject, domain,
    validity) combination lands in exactly one bucket here. If a new routing
    branch is ever added without a renderer, the assertion below fails loudly
    rather than dropping the facts on the floor.
    """
    # Derivation is off (see RENDERED_FILES). Returning nothing here makes every
    # caller a no-op — commit_rendered writes nothing, the post-turn job does
    # nothing — without any of them needing to know why.
    if not RENDERED_FILES:
        return {}

    day = today or date.today()
    rows = list(
        (
            await db.execute(
                select(Observation).where(
                    Observation.user_id == user_id,
                    Observation.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    buckets: dict[str, list[Observation]] = defaultdict(list)
    for obs in rows:
        buckets[target_file(obs)].append(obs)

    unknown = set(buckets) - set(RENDERED_FILES) - {"/memories/owner.md", "/memories/current.md"}
    if unknown:
        raise RuntimeError(
            f"target_file routed observations to files with no renderer: {sorted(unknown)}. "
            f"Every routing branch needs a surface, or the facts become invisible."
        )

    return {
        "/memories/dossier.md": render_dossier(buckets["/memories/dossier.md"]),
        "/memories/sessions.md": render_sessions(buckets["/memories/sessions.md"], day),
        "/memories/finance.md": render_finance(buckets["/memories/finance.md"]),
        "/memories/projects.md": render_projects(buckets["/memories/projects.md"]),
        "/memories/social.md": render_social(buckets["/memories/social.md"]),
        "/memories/history.md": render_history(buckets["/memories/history.md"]),
    }


# ── Shadow mode and commit (v3 §10, phases 3 and 4) ───────────────────────────

async def compare_to_stored(db: AsyncSession, user_id: int) -> list[dict]:
    """
    Shadow mode: render every surface and report how it differs from the stored
    file, WITHOUT writing anything.

    This is the dress rehearsal the migration plan insists on. Flipping the files
    to derived output is the one irreversible step in v3, and the question it
    turns on — "does the record actually reproduce what the owner has today?" —
    is answerable in advance. Run this, read the report, and only then flip.

    A large `only_in_stored` count is the signal that matters: it means the file
    holds knowledge the record does not, and re-indexing before flipping would
    lose it.
    """
    from app.models.memory_file import MemoryFile

    rendered = await render_all(db, user_id)
    stored_rows = (
        await db.execute(
            select(MemoryFile).where(MemoryFile.user_id == user_id)
        )
    ).scalars().all()
    stored = {f.path: f.content for f in stored_rows}

    def facts(text: str) -> set[str]:
        """Content lines only — headers, notes and the id annotations are
        formatting, not knowledge, and comparing them would drown the signal."""
        out = set()
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            body = line[2:].split("  <sub>")[0].strip()
            if body.startswith("["):
                body = body.split("]", 1)[-1].strip()
            if body and not body.startswith("_"):
                out.add(body.lower())
        return out

    report = []
    for path, new_text in sorted(rendered.items()):
        old_text = stored.get(path, "")
        old_facts, new_facts = facts(old_text), facts(new_text)
        report.append({
            "path": path,
            "kind": "rendered",
            "identical": old_text == new_text,
            "stored_bytes": len(old_text.encode("utf-8")),
            "rendered_bytes": len(new_text.encode("utf-8")),
            "only_in_stored": sorted(old_facts - new_facts)[:20],
            "only_in_stored_count": len(old_facts - new_facts),
            "only_in_rendered_count": len(new_facts - old_facts),
        })

    # ── The composed files ───────────────────────────────────────────────────
    # owner.md and current.md are not rendered, so the loop above never sees
    # them — and they are the MOST prose-heavy files in the set, which makes
    # their absence the single most dangerous blind spot this report could have.
    # An early version of it reported "0 at risk" while owner.md's entire
    # biography had no backing in the record.
    #
    # Their content cannot be fact-matched (prose against atomic observations),
    # so this reports coverage instead: how much text the file holds versus how
    # many observations exist to rebuild it from. It deliberately does NOT
    # produce a green light — it tells the owner to read the composed output.
    from app.services.memory_compose import CURRENT_PATH, OWNER_PATH

    for path, domain in ((OWNER_PATH, "biography"), (CURRENT_PATH, "state")):
        old_text = stored.get(path, "")
        backing = (
            await db.execute(
                select(Observation).where(
                    Observation.user_id == user_id,
                    Observation.deleted_at.is_(None),
                    Observation.subject == "owner",
                    Observation.domain == domain,
                    Observation.valid_until.is_(None),
                )
            )
        ).scalars().all()
        prose_bytes = sum(
            len(line) for line in old_text.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "-", "*", "_", "<!--"))
        )
        thin = prose_bytes > 200 and len(backing) < 3
        report.append({
            "path": path,
            "kind": "composed",
            "stored_bytes": len(old_text.encode("utf-8")),
            "prose_bytes": prose_bytes,
            "backing_observations": len(backing),
            "warning": (
                f"{prose_bytes} bytes of prose backed by only {len(backing)} "
                f"observation(s) — composing this file now would thin it out. "
                f"Run the seed with a model so the prose is captured, then check "
                f"again."
                if thin else None
            ),
            # Never a green light: a composition is a model's work and the only
            # honest verification is the owner reading it.
            "verify": "read this file after /admin/memory/compose before trusting it",
        })

    at_risk = sum(r.get("only_in_stored_count", 0) for r in report)
    warnings = [r["path"] for r in report if r.get("warning")]
    logger.info(
        "memory_render_shadow_report",
        extra={
            "user_id": user_id,
            "files": len(report),
            "at_risk": at_risk,
            "thin_compositions": warnings,
        },
    )
    return report


async def commit_rendered(
    db: AsyncSession, user_id: int, *, request_id: str = "", author: str = "render"
) -> list[str]:
    """
    Write the rendered surfaces to their MemoryFile rows.

    Only files whose content actually changed are written, so the revision trail
    records renders that did something and stays readable — and so `updated_at`
    does not churn every night, which the recall cache keys on.
    """
    from app.models.memory_file import MemoryFile
    from app.services.memory_store import record_revision

    rendered = await render_all(db, user_id)
    existing = {
        f.path: f
        for f in (
            await db.execute(select(MemoryFile).where(MemoryFile.user_id == user_id))
        ).scalars().all()
    }

    changed: list[str] = []
    skipped: list[str] = []
    for path, text in sorted(rendered.items()):
        current = existing.get(path)
        if current is not None and current.content == text:
            continue
        before = current.content if current else ""

        # SAFETY INTERLOCK — never replace a file that has content with a render
        # that has none.
        #
        # This runs from the post-turn queue, so on the first turn after a deploy
        # it fires against whatever the record happens to hold. If the record is
        # empty — a fresh install, a failed seed, a migration that has not been
        # run yet — the honest render of every surface is "nothing recorded yet",
        # and writing that would erase the owner's real memory on his first
        # message. It is recoverable from the revision trail, but a system that
        # needs its own audit log to undo what it did on boot is not one to trust
        # with the only copy.
        #
        # Emptiness here means "no fact lines", not "no bytes": every render has
        # a header and a note even when it has nothing to say.
        if _fact_lines(text) == 0 and _fact_lines(before) > 0:
            skipped.append(path)
            logger.error(
                "memory_render_refused_blanking",
                extra={
                    "user_id": user_id,
                    "path": path,
                    "stored_facts": _fact_lines(before),
                    "reason": (
                        "the record holds nothing for this surface — refusing to "
                        "overwrite existing memory. Run the rebuild."
                    ),
                },
            )
            continue
        if current is None:
            db.add(MemoryFile(user_id=user_id, path=path, content=text))
        else:
            from datetime import datetime, timezone
            current.content = text
            current.updated_at = datetime.now(timezone.utc)
        await record_revision(
            db, user_id=user_id, path=path, author=author,
            action="render", before=before, after=text, request_id=request_id,
        )
        changed.append(path)

    if changed:
        await db.commit()
    logger.info(
        "memory_surfaces_rendered",
        extra={
            "user_id": user_id,
            "request_id": request_id,
            "changed": changed,
            "refused": skipped,
        },
    )
    return changed
