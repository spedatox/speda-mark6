import json
from app.skills.base import Skill
from app.schemas.iacon import Command


class IaconProposeSkill(Skill):
    name = "iacon_propose"
    read_only = False
    description = ("Propose an evidence-backed addition or correction to Iacon's structured catalogue. "
        "Use exact source IDs and character spans from owner evidence; updates require the current record version. "
        "For an ongoing serious incident, propose an active state with high salience so future chats can track it. "
        "Update an existing state when the owner confirms a change or recovery. "
        "Do not rewrite native documents or claim the write is complete before review. "
        "Returns pending_review; Orion validates and reviews the proposal within persistent maintenance budgets.")
    input_schema = Command.model_json_schema()

    def __init__(self, orion):
        self.orion = orion

    def available(self, context):
        # Automatic intake handles ordinary conversations. Keep the large write
        # contract out of the answering model's everyday input-token bill.
        return context.agent_id == "orion"

    async def execute(self, args, context):
        try:
            return json.dumps(await self.orion.propose(context, Command.model_validate(args)))
        except ValueError as exc:
            return json.dumps({"status": "rejected", "reason": str(exc)[:500]})


class DeclareStateSkill(Skill):
    name = "declare_state"
    read_only = False
    description = (
        "Declare or update an active or upcoming situational life state in working memory "
        "(such as travel/relocation, an exam period, a vacation, illness, or an open-ended commitment). "
        "Use immediately whenever you observe or infer a multi-day condition that affects the owner's "
        "location, availability, schedule, or routines — even from calendar/email during a briefing. "
        "Omit ends_on when the end or return date is unknown and provide the boundary question to ask."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Concise name of the situational state."},
            "summary": {"type": "string", "description": "Practical situation and downstream life/schedule implications."},
            "status": {"type": "string", "enum": ["planned", "active", "waiting", "completed", "cancelled"], "default": "active"},
            "starts_on": {"type": "string", "description": "Optional start date (YYYY-MM-DD)."},
            "ends_on": {"type": "string", "description": "Optional end date (YYYY-MM-DD). Omit if unknown."},
            "salience": {"type": "string", "enum": ["high", "normal", "low"], "default": "high"},
            "question": {"type": "string", "description": "Single high-leverage boundary or confirmation question."},
            "basis": {"type": "string", "enum": ["owner_statement", "tool_observation"], "default": "owner_statement"},
            "evidence_note": {"type": "string", "description": "Brief factual source pointer when basis is tool_observation."},
        },
        "required": ["title", "summary"],
    }

    def __init__(self, orion):
        self.orion = orion

    def available(self, context):
        return (
            self.orion is not None
            and context.task_role != "worker"
            and context.agent_id in {"speda", "ultron", "friday", "atomix", "sentinel", "orion"}
        )

    async def execute(self, args, context):
        try:
            return json.dumps(await self.orion.declare_state(context, args), ensure_ascii=False)
        except ValueError as exc:
            return json.dumps({"status": "rejected", "reason": str(exc)[:500]})
