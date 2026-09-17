# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Small explicit investigation/feedback surface for ACE."""

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.context import AgentContext
from app.models.observation import Observation
from app.models.pattern import Countermeasure, CountermeasurePattern, PatternEvidence, PatternState
from app.services.countermeasures import record_outcome, retire_pattern_countermeasures
from app.services.tactical_context import tactical_context_for_message
from app.skills.base import Skill


class InspectPatternsSkill(Skill):
    name = "inspect_patterns"
    description = (
        "Inspect the evidence-scored pattern models behind ACE when the owner asks why "
        "a tactic is being used or what the system has learned about a subject. Use it "
        "for explicit investigation, evidence, linked countermeasures, or status history; "
        "ordinary turns already receive relevant tactical context automatically. Returns "
        "pattern claims with confidence, lifecycle and traceable evidence, never hidden reasoning."
    )
    deferred = True
    search_keywords = "pattern evidence countermeasure learned why tactic"
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["relevant", "subject", "evidence", "countermeasures", "history"],
            },
            "pattern_id": {"type": "integer"},
            "subject": {"type": "string"},
        },
        "required": ["mode"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        mode = args.get("mode", "relevant")
        if mode == "relevant":
            return await tactical_context_for_message(
                context.user_id,
                context.db,
                context.conversation_history,
                agent_id=context.agent_id,
                request_id=context.request_id,
            ) or "No active pattern is relevant to the current objective."

        query = (
            select(PatternState, Observation)
            .join(Observation, Observation.id == PatternState.observation_id)
            .where(PatternState.user_id == context.user_id)
        )
        if args.get("pattern_id") is not None:
            query = query.where(PatternState.observation_id == int(args["pattern_id"]))
        if args.get("subject"):
            query = query.where(PatternState.scope_key == args["subject"])
        rows = list((await context.db.execute(query)).all())
        if not rows:
            return "No matching patterns."

        payload = []
        for state, observation in rows[:20]:
            item = {
                "id": state.observation_id,
                "claim": observation.content,
                "namespace": state.namespace,
                "scope": state.scope_key,
                "status": state.status,
                "confidence": round(state.confidence_score, 3),
                "support": state.support_count,
                "independent_sources": state.independent_source_count,
                "contradictions": state.contradiction_count,
            }
            if mode == "evidence":
                evidence = list((await context.db.execute(
                    select(PatternEvidence).where(
                        PatternEvidence.pattern_observation_id == state.observation_id
                    )
                )).scalars().all())
                item["evidence"] = [
                    {
                        "role": row.role,
                        "ref": row.evidence_ref,
                        "source_group": row.source_group,
                        "trust": row.trust_class,
                        "excerpt": row.excerpt,
                        "locator": row.locator,
                    }
                    for row in evidence
                ]
            if mode == "countermeasures":
                cms = list((await context.db.execute(
                    select(Countermeasure)
                    .join(CountermeasurePattern, CountermeasurePattern.countermeasure_id == Countermeasure.id)
                    .where(CountermeasurePattern.pattern_observation_id == state.observation_id)
                )).scalars().all())
                item["countermeasures"] = [
                    {
                        "id": cm.id,
                        "title": cm.title,
                        "strategy": cm.strategy,
                        "status": cm.status,
                        "effectiveness": cm.effectiveness_score,
                        "attempts": cm.attempt_count,
                    }
                    for cm in cms
                ]
            payload.append(item)
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


class PatternFeedbackSkill(Skill):
    name = "pattern_feedback"
    description = (
        "Record the owner's explicit verdict that a pattern is incorrect/retired or that "
        "a specific countermeasure run worked or failed. Use it only for direct feedback "
        "or objective outcome evidence, never to infer success from silence or from the "
        "assistant's own prose. Returns the durable state change and preserves the old "
        "record for audit rather than deleting it."
    )
    deferred = True
    search_keywords = "pattern wrong retire worked failed outcome feedback"
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "feedback": {
                "type": "string",
                "enum": ["worked", "did_not_work", "incorrect_pattern", "retire"],
            },
            "pattern_id": {"type": "integer"},
            "run_id": {"type": "integer"},
            "outcome_source": {
                "type": "string",
                "enum": ["objective_measurement", "owner_feedback", "tool_result", "subsequent_behavior"],
                "default": "owner_feedback",
            },
        },
        "required": ["feedback"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        feedback = args.get("feedback")
        if feedback in {"worked", "did_not_work"}:
            if args.get("run_id") is None:
                return "run_id is required for countermeasure outcome feedback."
            run = await record_outcome(
                context.db,
                run_id=int(args["run_id"]),
                user_id=context.user_id,
                outcome="success" if feedback == "worked" else "failure",
                outcome_source=args.get("outcome_source", "owner_feedback"),
            )
            return f"Recorded {run.outcome} for countermeasure run {run.id}."

        if args.get("pattern_id") is None:
            return "pattern_id is required for pattern feedback."
        state = await context.db.get(PatternState, int(args["pattern_id"]))
        if state is None or state.user_id != context.user_id:
            return "Pattern not found."
        state.status = "rejected" if feedback == "incorrect_pattern" else "retired"
        state.updated_at = datetime.now(timezone.utc)
        retired = await retire_pattern_countermeasures(
            context.db, user_id=context.user_id, pattern_id=state.observation_id
        )
        await context.db.commit()
        return f"Pattern P{state.observation_id} marked {state.status}; {retired} linked countermeasure(s) retired."
