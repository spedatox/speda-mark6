"""Persistent owner steering consumed only at Forge's legal message boundaries."""
import asyncio
from datetime import datetime
from pathlib import Path
from app.database import write_session
from sqlalchemy import select, update
from app.models.coordination import TaskInput, ProjectTask, ForgeBoundary
from app.services.coordination_budget import digest, CoordinationError


class TaskInbox:
    def __init__(self, context, sessions):
        self.context, self.sessions, self.claimed = context, sessions, []

    def __bool__(self):
        return True  # The queue lives in SQL; emptiness must be checked there.

    async def claim(self):
        ctx = self.context
        await ctx.coordination.ensure_active(ctx)
        async with write_session(self.sessions) as db:
            await ctx.coordination.owned_room(db, ctx.user_id, ctx.room_id, lock=True)
            task = await db.get(ProjectTask, ctx.step_id)
            if (not task or task.run_id != ctx.run_id or task.project_id != ctx.project_id
                    or task.owner_agent != ctx.agent_id):
                raise CoordinationError('input_scope', 'Inbox does not match the admitted project worker')
            rows = list((await db.execute(select(TaskInput).where(TaskInput.task_id == ctx.step_id,
                TaskInput.status == "queued").order_by(TaskInput.created_at, TaskInput.id).limit(16))).scalars())
            for row in rows:
                row.status = "delivering"
            self.claimed = [r.id for r in rows]
            return [r.text for r in rows]

    async def record_boundary(self, messages):
        ctx = self.context
        await ctx.coordination.ensure_active(ctx)
        from app.services.projects import inventory
        async with self.sessions() as db:
            task = await db.get(ProjectTask, ctx.step_id)
            if (not task or task.run_id != ctx.run_id or task.project_id != ctx.project_id
                    or task.owner_agent != ctx.agent_id):
                raise CoordinationError('input_scope', 'Checkpoint does not match the admitted worker')
        revision = (await asyncio.to_thread(inventory, Path(task.workspace)))[0] if task.workspace else None
        async with write_session(self.sessions) as db:
            room = await ctx.coordination.owned_room(db, ctx.user_id, ctx.room_id, lock=True)
            values = dict(messages=list(messages), continuations=ctx.extra.get('forge_continuations', []),
                model_call_index=ctx.model_call_index, artifact_revision=revision,
                boundary_hash=digest(messages), updated_at=datetime.utcnow())
            boundary = await db.get(ForgeBoundary, ctx.step_id)
            if boundary:
                for key, value in values.items():
                    setattr(boundary, key, value)
            else:
                db.add(ForgeBoundary(task_id=ctx.step_id, **values))
            if not self.claimed:
                return
            changed = await db.execute(update(TaskInput).where(TaskInput.id.in_(self.claimed),
                TaskInput.task_id == ctx.step_id, TaskInput.status == "delivering")
                .values(status="delivered", boundary_hash=digest(messages)))
            if changed.rowcount != len(set(self.claimed)):
                raise CoordinationError('input_scope', 'Steering receipt belongs to another task or boundary')
            await ctx.coordination.event(db, room, "task_input_delivered", "system",
                {"task_id": ctx.step_id, "input_ids": self.claimed,
                 "meaning": "Included at a model boundary; compliance still requires result evidence"}, ctx.run_id)
        self.claimed = []
        await ctx.coordination.notify()
