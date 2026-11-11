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
