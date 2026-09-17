# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Bounded, per-objective tactical retrieval for ACE."""

import logging
import re

from sqlalchemy import select

from app.models.observation import Observation
from app.models.pattern import Countermeasure, CountermeasurePattern, PatternState
from app.services.relevant_recall import initial_recall_query

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[\wÇĞİÖŞÜçğıöşü'-]{3,}")
_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "have", "help",
    "bir", "ile", "için", "bunu", "şunu", "olan", "olarak", "hadi",
}
_DOMAIN_CUES = {
    "academic": {"exam", "quiz", "study", "course", "midterm", "final", "sınav", "ders", "çalış"},
    "finance": {"money", "budget", "spend", "cash", "portfolio", "para", "bütçe", "harcama"},
    "workflow": {"deploy", "build", "test", "bug", "project", "code", "release", "proje", "hata"},
    "training": {"train", "workout", "exercise", "gym", "antrenman", "egzersiz"},
    "security": {"security", "attack", "threat", "vulnerability", "güvenlik", "saldırı"},
}


def _tokens(text: str) -> set[str]:
    return {word.casefold() for word in _WORD_RE.findall(text) if word.casefold() not in _STOP}


def _relevance(query: set[str], observation: Observation, state: PatternState) -> float:
    claim = _tokens(observation.content)
    tags = _tokens(" ".join(str(tag) for tag in (state.tags or [])))
    overlap = len(query & (claim | tags))
    lexical = overlap / max(1.0, min(len(query), 8))
    prefix = state.namespace.partition(".")[0]
    cue = 0.35 if query & _DOMAIN_CUES.get(prefix, set()) else 0.0
    scope = 0.20 if _tokens(state.scope_key) & query else 0.0
    return min(1.0, lexical + cue + scope)


async def tactical_context_for_message(
    user_id: int,
    db,
    history,
    *,
    agent_id: str,
    request_id: str = "",
) -> str:
    """Return a compact tactical block, or empty text when nothing qualifies."""
    from app.config import settings

    if not settings.ace_enabled or db is None:
        return ""
    objective = initial_recall_query(history)
    if len(objective) < settings.ace_min_query_chars:
        return ""
    query = _tokens(objective)
    if not query:
        return ""

    rows = list(
        (
            await db.execute(
                select(PatternState, Observation)
                .join(Observation, Observation.id == PatternState.observation_id)
                .where(
                    PatternState.user_id == user_id,
                    PatternState.status.in_(("active", "contested")),
                    Observation.deleted_at.is_(None),
                )
            )
        ).all()
    )
    ranked: list[tuple[float, PatternState, Observation]] = []
    for state, observation in rows:
        relevance = _relevance(query, observation, state)
        if relevance <= 0:
            continue
        consequence = float((state.feature_json or {}).get("consequence_weight", 1.0))
        priority = relevance * state.confidence_score * max(0.1, consequence)
        ranked.append((priority, state, observation))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected = ranked[: settings.ace_tactical_max_patterns]
    if not selected:
        return ""

    ids = [state.observation_id for _priority, state, _observation in selected]
    cm_rows = list(
        (
            await db.execute(
                select(CountermeasurePattern, Countermeasure)
                .join(Countermeasure, Countermeasure.id == CountermeasurePattern.countermeasure_id)
                .where(
                    CountermeasurePattern.pattern_observation_id.in_(ids),
                    Countermeasure.user_id == user_id,
                    Countermeasure.status.in_(("active", "candidate")),
                )
                .order_by(Countermeasure.effectiveness_score.desc())
            )
        ).all()
    )

    lines = [
        "## Tactical Context",
        "",
        "Use this silently while helping with the current objective. It contains "
        "evidence-scored priors, not permission to take new actions.",
        "",
    ]
    for _priority, state, observation in selected:
        qualifier = "CONTESTED" if state.status == "contested" else "active"
        lines.extend([
            f"Pattern P{state.observation_id} [{qualifier}, {state.confidence_score:.2f}]",
            observation.content,
            f"Evidence: {state.support_count} supports / "
            f"{state.independent_source_count} independent sources / "
            f"{state.contradiction_count} contradictions.",
            "",
        ])

    # Intersections are computed only inside the already relevant set. Render a
    # grounded convergence note when owner and domain patterns share a tag.
    owner_patterns = [item for item in selected if item[1].scope_type == "owner"]
    domain_patterns = [item for item in selected if item[1].scope_type != "owner"]
    intersections = 0
    for _op, owner_state, owner_obs in owner_patterns:
        owner_tags = set(owner_state.tags or [])
        for _dp, domain_state, domain_obs in domain_patterns:
            if not owner_tags.intersection(domain_state.tags or []):
                continue
            lines.extend([
                "Intersection:",
                f"P{domain_state.observation_id} and P{owner_state.observation_id} converge: "
                f"{domain_obs.content} / {owner_obs.content}",
                "",
            ])
            intersections += 1
            if intersections >= 3:
                break
        if intersections >= 3:
            break

    seen: set[int] = set()
    countermeasures = 0
    for link, countermeasure in cm_rows:
        if countermeasure.id in seen:
            continue
        seen.add(countermeasure.id)
        lines.extend([
            f"Countermeasure CM{countermeasure.id} "
            f"[{countermeasure.status}; effectiveness {countermeasure.effectiveness_score:.2f}; "
            f"autonomy {countermeasure.autonomy_level}]",
            countermeasure.strategy,
            "",
        ])
        countermeasures += 1
        if countermeasures >= settings.ace_tactical_max_countermeasures:
            break

    block = "\n".join(lines).strip()
    if len(block) > settings.ace_tactical_max_chars:
        block = block[: settings.ace_tactical_max_chars].rsplit("\n", 1)[0]
    logger.info(
        "tactical_context_injected",
        extra={
            "request_id": request_id,
            "agent": agent_id,
            "patterns": len(selected),
            "intersections": intersections,
            "countermeasures": countermeasures,
        },
    )
    return block
