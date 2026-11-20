"""Close a previously delivered watch question using the current owner reply."""
import json
from sqlalchemy import update
from app.database import write_session
from app.models.automation import AttentionItem
from app.skills.base import Skill


class ResolveFollowup(Skill):
    name = "resolve_followup"
    description = ("Record that the owner answered or dismissed a particular open question from this agent. "
                   "Use the supplied question ID only when the current reply resolves that question. "
                   "An ambiguous reply leaves the question open. Returns the resulting status.")
    input_schema = {"type": "object", "required": ["question_id", "status"], "properties": {
        "question_id": {"type": "string"}, "status": {"enum": ["answered", "dismissed"]}},
        "additionalProperties": False}

    def __init__(self, sessions):
        self.sessions = sessions

    def available(self, context):
        return getattr(context, "triggered_by", None) == "user" and bool(context.extra.get("open_attention_questions"))

    async def execute(self, args, context):
        from app.services.watch_attention import active_items
        from datetime import datetime
        key = args["question_id"]
        if key not in {q["id"] for q in context.extra.get("open_attention_questions", [])}:
            return '{"status":"unavailable"}'
        async with write_session(self.sessions) as db:
            eligible = await active_items(db, context.user_id, context.agent_id, "ask",
                                          datetime.utcnow(), lock=True)
            if key not in {q.key for q in eligible}:
                return '{"status":"unavailable"}'
            result = await db.execute(update(AttentionItem).where(
                AttentionItem.user_id == context.user_id, AttentionItem.key == key,
                AttentionItem.state == "ask").values(state=args["status"]))
            return json.dumps({"status": args["status"] if result.rowcount else "unavailable"})
