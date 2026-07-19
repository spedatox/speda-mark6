"""Legion status skill — retrieve background legionnaire results.

The Legion's background mode (Task tool with run_in_background=true) returns a
ticket immediately and finishes detached; this skill is how the deploying agent
gets the result back. Tickets live in agent_messages with kind="legion" — the
same table dispatch uses, so the comms tray shows them too.
"""

from app.core.context import AgentContext
from app.skills.base import Skill


class LegionStatusSkill(Skill):
    name = "legion_status"
    description = (
        "Checks on background legionnaires you deployed with the Task tool "
        "(run_in_background=true) — Legion workers that keep running after your "
        "turn ends. Use it when the owner asks whether a background research job "
        "is done, or when you need a background worker's findings to continue. "
        "Do NOT use it for normal (blocking) Task deployments — those already "
        "returned their result in-turn — and NOT for persona dispatches to the "
        "Superior Six (that is dispatch_status). Pass a ticket 'id' to check one "
        "worker, or omit it to list your recent background workers. Returns each "
        "worker's status (running / ok / error), how long it ran, and the full "
        "result text once finished."
    )
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "id": {
                "type": "integer",
                "description": "A legion ticket id (from a background deployment). Omit to list recent ones.",
            },
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        from sqlalchemy import select

        from app.database import AsyncSessionLocal
        from app.models.agent_message import AgentMessage

        ticket_id = args.get("id")
        async with AsyncSessionLocal() as db:
            if ticket_id is not None:
                row = await db.get(AgentMessage, int(ticket_id))
                if row is None or row.kind != "legion" or row.from_agent != context.agent_id:
                    return f"No legion ticket #{ticket_id} that you deployed was found."
                return _fmt_worker(row)
            stmt = (
                select(AgentMessage)
                .where(
                    AgentMessage.kind == "legion",
                    AgentMessage.from_agent == context.agent_id,
                )
                .order_by(AgentMessage.id.desc())
                .limit(10)
            )
            rows = list((await db.execute(stmt)).scalars().all())
        if not rows:
            return "You have not deployed any background legionnaires yet."
        return "Your recent background legionnaires:\n" + "\n".join(
            _fmt_worker(r, brief=True) for r in rows
        )


def _fmt_worker(row, brief: bool = False) -> str:
    from app.legion.roster import MAX_WORKER_RESULT_CHARS

    dur = f"{row.duration_ms}ms" if row.duration_ms is not None else "…"
    head = f"#{row.id} → {row.to_agent} [{row.status}] ({dur})"
    if row.status == "running":
        return f"{head}: still working" + ("" if brief else f"\n  task: {row.task[:200]}")
    if brief:
        result = " ".join((row.result or "").split())[:160]
        return f"{head}: {result}"
    return f"{head}\n  task: {row.task[:200]}\n  result:\n{(row.result or '')[:MAX_WORKER_RESULT_CHARS]}"
