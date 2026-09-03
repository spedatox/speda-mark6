# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The observation tier — Tier 1 skills over app/services/observations.py.

Two tools, deliberately. Honcho exposes eleven observation tools across its
Dialectic and Dreamer agents; Mark VI ships every skill's 3–4 sentence
description (Rule 11) on every call, so eleven descriptions is a per-request bill
the roster would pay forever. The capability is folded into a write tool and a
read tool with a `mode`, which keeps the model's choice small without losing a
single retrieval shape.

These sit ALONGSIDE the `memory` tool, not instead of it:

  memory              → the durable, injected, owner-readable narrative. What the
                        owner would recognise if he opened the file.
  record_observation  → the addressable, sourced fact beneath it. What lets a
                        claim be traced, ranked by repetition, and contradicted.

An agent that learns something important does both: files the sentence, records
the observation. The file is what gets read every turn; the observation is what
survives the file being rewritten.
"""

import logging
from datetime import datetime, timezone

from app.core.context import AgentContext
from app.services.observations import (
    CONFIDENCE_LEVELS,
    DOMAINS,
    LEVELS,
    PATTERN_TYPES,
    supersede,
    format_observation,
    format_observations,
    most_reinforced_observations,
    reasoning_chain,
    recent_observations,
    record_observations,
    search_observations,
    soft_delete_observations,
)
from app.services.surprisal import (
    find_near_duplicates,
    format_duplicates,
    rank_by_surprisal,
)
from app.skills.base import Skill

logger = logging.getLogger(__name__)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class RecordObservationSkill(Skill):
    """Write path for the observation store — the evidence ladder's entry point."""

    name = "record_observation"
    description = (
        "Record one or more discrete, sourced facts you have learned about the owner, "
        "as addressable observations that sit beneath the /memories files. Use it when "
        "something durable emerges in conversation — a preference, a constraint, a "
        "decision, a pattern you can now see across several past facts — and use it IN "
        "ADDITION to the `memory` tool, not instead of it: `memory` keeps the narrative "
        "the owner reads, this keeps the traceable fact behind it. Do NOT use it for "
        "transient state ('he is tired today'), for anything you would not want resurfacing "
        "in six months, or to restate something already recorded — a genuine repeat is "
        "recorded automatically as reinforcement when you record the same fact again. "
        "Anything above 'explicit' must cite the `source_ids` it rests on, which you get "
        "from `search_memory`; an uncited deduction is rejected. Returns the id of each "
        "stored observation, plus an explanation for anything refused."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "observations": {
                "type": "array",
                "description": "The facts to record. One fact per entry — never a paragraph.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": (
                                "The fact as ONE self-contained sentence, understandable "
                                "without the conversation it came from."
                            ),
                        },
                        "level": {
                            "type": "string",
                            "enum": list(LEVELS),
                            "description": (
                                "explicit: he stated it directly (no sources needed). "
                                "deductive: it follows necessarily from facts already "
                                "recorded (needs source_ids + premises). "
                                "inductive: a pattern across several facts (needs 2+ "
                                "source_ids, 2+ sources, pattern_type, confidence). "
                                "contradiction: two recorded facts cannot both hold "
                                "(needs 2+ source_ids + sources)."
                            ),
                        },
                        "source_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": (
                                "Ids of the observations this rests on, as returned by "
                                "search_memory in [id:N] form. Required for every level "
                                "above 'explicit'."
                            ),
                        },
                        "premises": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "(deductive) The readable text of each cited source.",
                        },
                        "sources": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "(inductive/contradiction) The readable evidence text.",
                        },
                        "pattern_type": {
                            "type": "string",
                            "enum": list(PATTERN_TYPES),
                            "description": "(inductive only) What kind of pattern this is.",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": list(CONFIDENCE_LEVELS),
                            "description": (
                                "(inductive only) 'high' for 5+ sources, 'medium' for 3-4, "
                                "'low' for 2."
                            ),
                        },
                        "subject": {
                            "type": "string",
                            "description": (
                                "WHO or WHAT the fact is about: 'owner' (the default), "
                                "'person:<Name>' for someone else, or 'project:<Name>'. "
                                "This decides where the fact surfaces — you no longer "
                                "pick a file."
                            ),
                            "default": "owner",
                        },
                        "domain": {
                            "type": "string",
                            "enum": list(DOMAINS),
                            "description": (
                                "What KIND of fact this is. biography = who someone is "
                                "(durable background, never expires). preference = what "
                                "he likes, dislikes or wants and in what manner. state = "
                                "something true of his life now. project = a project's "
                                "status. training = a gym session. finance = a figure, "
                                "account or budget. event = a dated thing that happened, "
                                "usually to someone else."
                            ),
                        },
                        "valid_from": {
                            "type": "string",
                            "description": (
                                "YYYY-MM-DD — when this started being true. Omit for a "
                                "fact that has simply always held. Must be absolute; a "
                                "relative date is rejected."
                            ),
                        },
                        "valid_until": {
                            "type": "string",
                            "description": (
                                "YYYY-MM-DD — when this STOPPED being true. Setting it is "
                                "how a fact leaves the present tense; it is not deleted "
                                "or moved, it simply stops being current. Omit for "
                                "anything still true, which is the normal case."
                            ),
                        },
                        "supersedes": {
                            "type": "integer",
                            "description": (
                                "The id of an observation this one REPLACES — a changed "
                                "figure, a corrected fact. The old one is closed out with "
                                "an end date and a pointer here rather than overwritten, "
                                "so the previous value stays answerable."
                            ),
                        },
                    },
                    "required": ["content", "level", "domain"],
                },
            },
        },
        "required": ["observations"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        proposals = args.get("observations") or []
        if not isinstance(proposals, list) or not proposals:
            return "No observations provided — pass a non-empty `observations` list."

        clean = [p for p in proposals if isinstance(p, dict)]
        stored, rejections = await record_observations(
            context.db,
            user_id=context.user_id,
            observer=context.agent_id,
            proposals=clean,
            session_id=context.session_id,
            request_id=context.request_id,
        )

        # Supersession runs AFTER the replacements exist — the old row's pointer
        # has to reference a real id. Only proposals that actually stored can
        # close anything out, so this walks the accepted ones in order.
        superseded: list[str] = []
        accepted = [p for p in clean if p.get("supersedes")]
        for proposal, obs in zip(accepted, stored[: len(accepted)] if accepted else []):
            old_id = proposal.get("supersedes")
            try:
                ok = await supersede(
                    context.db,
                    user_id=context.user_id,
                    old_id=int(old_id),
                    new_id=obs.id,
                )
            except (TypeError, ValueError):
                ok = False
            superseded.append(
                f"id:{old_id} → id:{obs.id}" if ok
                else f"id:{old_id} (not found or already closed)"
            )

        lines: list[str] = []
        if stored:
            lines.append(f"Recorded {len(stored)} observation(s):")
            for obs in stored:
                suffix = (
                    f" (reinforced, now seen {obs.reinforcement_count}×)"
                    if obs.reinforcement_count > 1
                    else ""
                )
                lines.append(f"  [id:{obs.id}] {obs.content}{suffix}")
        if superseded:
            lines.append("")
            lines.append("Superseded: " + ", ".join(superseded))
        if rejections:
            lines.append("")
            lines.append(f"{len(rejections)} refused:")
            lines.extend(f"  - {r}" for r in rejections)
        return "\n".join(lines) if lines else "Nothing recorded."


class SearchMemorySkill(Skill):
    """Read path for the observation store — Honcho's Dialectic retrieval surface."""

    name = "search_memory"
    description = (
        "Search what the roster has LEARNED about the owner — the sourced facts beneath "
        "the /memories files — by meaning, with optional filters for level, observing "
        "agent and date. Use it before you assert something about him that is not already "
        "in your injected memory block, before deriving a new conclusion (you need the "
        "source ids it returns), and when you want to know what is well-established versus "
        "observed once. Do NOT use it to search raw conversation transcripts — that is "
        "`recall_conversations` for meaning and `search_history` for exact wording; this "
        "searches distilled facts, not what was said. Modes: 'search' ranks by relevance to "
        "your query, matching BOTH meaning and literal wording — so a course code, a "
        "person's name, an amount or an exact Turkish word is worth putting in the query "
        "verbatim rather than paraphrasing. 'recent' returns the newest facts, "
        "'established' returns those "
        "reinforced most often across conversations, 'chain' traces one observation to its "
        "premises and to everything derived from it, and — for consolidation work — 'novel' "
        "surfaces the facts most isolated from everything else in memory while 'duplicates' "
        "surfaces pairs that say the same thing in different words. Returns each fact with "
        "its [id:N], level, observing agent, date and reinforcement count. Narrow with "
        "`subject` when the question is about ONE person or project, and with "
        "`current_only` for ANY question about the present — he has held sixteen jobs and "
        "lived in four cities, so without it the past outranks the present simply by "
        "being more numerous."
    )
    read_only = True
    requires_network = True  # embeds the query for the 'search' mode
    input_schema = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": [
                    "search",
                    "recent",
                    "established",
                    "chain",
                    "novel",
                    "duplicates",
                ],
                "description": (
                    "Which retrieval shape to use. Defaults to 'search'. 'novel' and "
                    "'duplicates' are the two tails of one computation and are meant for "
                    "consolidation, not for answering a question about the owner."
                ),
                "default": "search",
            },
            "query": {
                "type": "string",
                "description": "What to look for, in natural language. Required for mode='search'.",
            },
            "observation_id": {
                "type": "integer",
                "description": "The observation to trace. Required for mode='chain'.",
            },
            "direction": {
                "type": "string",
                "enum": ["premises", "conclusions", "both"],
                "description": (
                    "(chain) 'premises' = what it rests on, 'conclusions' = what rests on "
                    "it, 'both' = the full picture. Defaults to 'both'."
                ),
                "default": "both",
            },
            "level": {
                "type": "string",
                "enum": list(LEVELS),
                "description": "Optional: only facts at this level of the evidence ladder.",
            },
            "observer": {
                "type": "string",
                "description": (
                    "Optional: only what this agent observed (speda, sentinel, atomix, …). "
                    "Convergence between independent agents is the strongest signal, so "
                    "leave it unset unless you specifically want one agent's view."
                ),
            },
            "after": {
                "type": "string",
                "description": "Optional: only facts recorded on/after this date (YYYY-MM-DD).",
            },
            "before": {
                "type": "string",
                "description": "Optional: only facts recorded before this date (YYYY-MM-DD).",
            },
            "subject": {
                "type": "string",
                "description": (
                    "Optional: only facts ABOUT this entity — 'owner', 'person:Sinan Kara', "
                    "'project:Siberay'. Use it when the question is about one person or one "
                    "project and you want everything known, rather than whatever happens to "
                    "rank against your wording."
                ),
            },
            "domain": {
                "type": "string",
                "enum": list(DOMAINS),
                "description": (
                    "Optional: only facts of this KIND. 'biography' for durable background, "
                    "'state' for what is true of his life now, 'preference' for how he wants "
                    "to be treated."
                ),
            },
            "current_only": {
                "type": "boolean",
                "description": (
                    "Optional: only facts that have NOT ended. Set this for any question "
                    "about the present — 'where does he work', 'what is he studying', 'what "
                    "does he earn'. Without it a job he left in 2023 and the one he holds "
                    "today are equally live, and the past routinely outranks the present "
                    "because there is more of it."
                ),
                "default": False,
            },
            "as_of": {
                "type": "string",
                "description": (
                    "Optional: what was true ON this date (YYYY-MM-DD) — started by then and "
                    "not yet ended. This is how you answer 'what was he doing last summer' "
                    "without reading history."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max facts to return (default 15, max 40).",
                "default": 15,
            },
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        mode = (args.get("mode") or "search").strip().lower()
        limit = min(max(int(args.get("limit", 15) or 15), 1), 40)
        db, user_id = context.db, context.user_id

        if mode == "chain":
            obs_id = args.get("observation_id")
            if not obs_id:
                return "mode='chain' needs an `observation_id` — the N from an [id:N] tag."
            chain = await reasoning_chain(
                db,
                user_id=user_id,
                observation_id=int(obs_id),
                direction=(args.get("direction") or "both"),
            )
            if chain["root"] is None:
                return f"No live observation with id {obs_id}."
            out = ["Observation:", format_observation(chain["root"]), ""]
            out.append(format_observations(chain["premises"], "Rests on"))
            out.append("")
            out.append(format_observations(chain["conclusions"], "Derived from it"))
            return "\n".join(out)

        if mode == "recent":
            rows = await recent_observations(
                db, user_id=user_id, limit=limit, observer=(args.get("observer") or None)
            )
            return format_observations(rows, "Most recently learned")

        if mode == "established":
            rows = await most_reinforced_observations(db, user_id=user_id, limit=limit)
            return format_observations(rows, "Most established (by reinforcement)")

        if mode == "novel":
            scored = await rank_by_surprisal(db, user_id=user_id, limit=limit)
            if not scored:
                return (
                    "Not enough embedded observations to compute novelty yet — this "
                    "needs a neighbourhood to compare against."
                )
            body = format_observations(
                [(s.observation, s.score) for s in scored],
                "Most isolated facts (surprisal, 1.0 = nothing in memory resembles it)",
            )
            return (
                body
                + "\n\nA high score means nothing else in memory connects to this fact. "
                "That is either a thread worth reasoning about — search for what it "
                "SHOULD connect to and record the deduction — or a sign it was misfiled. "
                "It is not, by itself, a reason to delete anything."
            )

        if mode == "duplicates":
            pairs = await find_near_duplicates(db, user_id=user_id, limit=limit)
            return format_duplicates(pairs)

        query = (args.get("query") or "").strip()
        if not query:
            return "mode='search' needs a `query`. Use mode='recent' to browse without one."

        try:
            scored = await search_observations(
                db,
                user_id=user_id,
                query=query,
                limit=limit,
                level=(args.get("level") or None),
                observer=(args.get("observer") or None),
                subject=(args.get("subject") or None),
                domain=(args.get("domain") or None),
                live_only=bool(args.get("current_only")),
                as_of=(args.get("as_of") or None),
                after=_parse_date(args.get("after")),
                before=_parse_date(args.get("before")),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "search_memory_failed",
                extra={"request_id": context.request_id, "error": str(e)},
            )
            return "Memory search failed. Try 'recent' to browse the record instead."

        if not scored:
            # Distinct from "nothing recorded yet": the store is not empty, this
            # query simply had no match above the relevance floor. Saying so
            # plainly is the whole point of the floor — the correct next move is
            # to ask the owner or search the transcript, NOT to answer from the
            # nearest unrelated fact, which is what recall used to hand back.
            return (
                f"Nothing in the record matches '{query}'. This is a genuine miss, not "
                f"an empty store — do not answer from a near-miss. Either ask him, or "
                f"try `recall_conversations` in case it was said but never distilled "
                f"into a fact. If he tells you, record it."
            )

        logger.info(
            "search_memory",
            extra={
                "request_id": context.request_id,
                "query": query,
                "results": len(scored),
            },
        )
        return format_observations(scored, f"Facts matching '{query}'")


class ForgetObservationSkill(Skill):
    """Demotion, not deletion — the observation store's half of §3.4."""

    name = "forget_observation"
    description = (
        "Demote observations that turned out to be wrong, superseded, or duplicated, so "
        "they stop surfacing in recall. Use it when the owner corrects a fact you recorded, "
        "or when consolidating and you find two records of the same thing. Do NOT use it to "
        "tidy facts that are merely old — a fact that has stopped being true is history, not "
        "an error, and the owner's past does not expire. Nothing is destroyed: the rows stay "
        "readable in the audit trail and only leave the recall set. Returns how many were "
        "demoted."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "observation_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Ids to demote, taken from the [id:N] tags in search results.",
            },
            "reason": {
                "type": "string",
                "description": "Why — one short line, for the log.",
            },
        },
        "required": ["observation_ids"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        ids = [int(i) for i in (args.get("observation_ids") or []) if str(i).isdigit()]
        if not ids:
            return "No observation ids provided."
        count = await soft_delete_observations(
            context.db,
            user_id=context.user_id,
            observation_ids=ids,
            request_id=context.request_id,
        )
        logger.info(
            "forget_observation",
            extra={
                "request_id": context.request_id,
                "agent_id": context.agent_id,
                "count": count,
                "reason": (args.get("reason") or "")[:200],
            },
        )
        return f"Demoted {count} observation(s) out of recall. They remain in the audit trail."
