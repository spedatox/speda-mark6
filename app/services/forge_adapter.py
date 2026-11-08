"""Identity-free Forge calls use the same provider policy, receipts and root allowance."""
import asyncio
import copy
from dataclasses import asdict
from sqlalchemy import select
from app.models.model_policy import ToolExecutionReceipt
from app.models.coordination import CellReceipt
from app.models.coordination import WorkerCheck, ProjectTask, TaskInput
from uuid import uuid4
from app.services.coordination_budget import CoordinationError, digest
from forge.model.base import TextDelta, ToolUseRequest, UsageReport, TurnEnd
from forge.warden.tool import Tool, ToolResult
from app.skills.damage_control import IncidentObservation, ReportIncidentSkill
from app.services.damage_control import turn_facts


class ForgeIncidentTool(Tool):
    name = ReportIncidentSkill.name
    description = ReportIncidentSkill.description
    Args = IncidentObservation

    def __init__(self, protocol, context):
        self.skill = ReportIncidentSkill(protocol)
        self.context = context

    def check_permissions(self, args, ctx):
        from forge.warden.permissions import ALLOW
        return ALLOW

    async def call(self, args, ctx):
        return ToolResult(await self.skill.execute(args.model_dump(), self.context))


class ForgeIncidentProvider:
    name = "damage_control"

    def __init__(self, protocol, context):
        self.tool = ForgeIncidentTool(protocol, context)

    async def provide(self, cfg, request):
        return {self.tool.name: self.tool}

    async def close(self):
        pass


class ForgeModel:
    def __init__(self, context):
        self.context = context
        self.model_id = context.model
        self.continuations = {}

    async def stream(self, *, system, messages, tools, signal):
        if signal.is_set():
            raise asyncio.CancelledError()
        parent = asyncio.current_task()
        async def watch():
            await signal.wait()
            parent.cancel()
        watcher = asyncio.create_task(watch())
        try:
            history = copy.deepcopy(messages)
            for message in history:
                if message["role"] != "assistant" or not isinstance(message["content"], list):
                    continue
                key = tuple(b["id"] for b in message["content"] if b["type"] == "tool_use")
                if key in self.continuations:
                    message["content"].append(self.continuations[key])
            ctx = self.context
            async with ctx.budget_handle.stream(ctx, model=ctx.model, policy=ctx.effective_policy,
                    system=system, messages=history, tools=tools, max_tokens=ctx.output_token_cap,
                    user_id=ctx.user_id, agent_id=ctx.agent_id, role="worker") as stream:
                async for kind, text in stream.event_stream():
                    if kind == "text":
                        yield TextDelta(text)
                result = await stream.get_final_message()
            # Only expose tool requests after the paid response is durably saved.
            calls = [b for b in result.content if b.type == "tool_use"]
            if result.continuation and calls:
                self.continuations[tuple(b.id for b in calls)] = result.continuation
                ctx.extra['forge_continuations'] = [{'call_ids': list(ids), 'state': state}
                    for ids, state in self.continuations.items()]
            for call in calls:
                yield ToolUseRequest(call.id, call.name, call.input)
            usage = result.usage
            yield UsageReport(usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                estimated=not bool(usage))
            yield TurnEnd(result.stop_reason)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)


class ForgeEffects:
    """Mandatory pre-effect journal, outside Forge's best-effort observers."""
    def __init__(self, sessions, context, protocol=None):
        self.sessions, self.context = sessions, context
        self.protocol = protocol

    async def cell_lifecycle(self, status, backend, handle, workspace):
        async with self.sessions() as db, db.begin():
            row = await db.get(CellReceipt, self.context.step_id)
            if row is None:
                db.add(CellReceipt(task_id=self.context.step_id, status=status, backend=backend,
                    handle=handle, workspace=str(workspace)))
            else:
                row.status, row.handle = status, handle

    async def __call__(self, call, execute):
        ctx = self.context
        await ctx.coordination.ensure_active(ctx)
        hashed = digest(call.input)
        check_id, workspace = None, None
        async with self.sessions() as db, db.begin():
            prior = (await db.execute(select(ToolExecutionReceipt).where(
                ToolExecutionReceipt.user_id == ctx.user_id, ToolExecutionReceipt.request_id == ctx.request_id,
                ToolExecutionReceipt.call_id == call.id))).scalar_one_or_none()
            if prior:
                if prior.name != call.name or prior.arguments_hash != hashed:
                    raise CoordinationError("effect_conflict", "Worker tool ID reused with different arguments")
                if prior.status != "completed":
                    raise CoordinationError("effect_unknown", "Worker effect requires reconciliation")
                return ToolResult(**prior.result)
            pending = await db.scalar(select(TaskInput.id).where(TaskInput.task_id == ctx.step_id,
                TaskInput.status.in_(['queued', 'delivering'])).limit(1))
            if pending:
                result = ToolResult('Execution checkpoint: new steering is pending. This tool was not run. '
                    'Read the instruction at the next model boundary and replan the call.', is_error=True)
                db.add(ToolExecutionReceipt(user_id=ctx.user_id, request_id=ctx.request_id,
                    call_id=call.id, name=call.name, arguments_hash=hashed,
                    status='completed', result=asdict(result)))
                return result
            db.add(ToolExecutionReceipt(user_id=ctx.user_id, request_id=ctx.request_id,
                call_id=call.id, name=call.name, arguments_hash=hashed, status="started"))
            if call.name == "run_command":
                from app.services.projects import inventory
                from pathlib import Path
                task = await db.get(ProjectTask, ctx.step_id)
                workspace = Path(task.workspace)
                revision, _ = await asyncio.to_thread(inventory, workspace)
                check_id = uuid4().hex
                db.add(WorkerCheck(id=check_id, task_id=task.id, call_id=call.id,
                    before_revision=revision, command=call.input))
        result = await execute()
        if self.protocol and result.is_error:
            self.protocol.capture({**turn_facts(ctx), "event": "forge_tool_returned_error",
                                   "tool": call.name, "ended_turn": False})
        async with self.sessions() as db, db.begin():
            receipt = (await db.execute(select(ToolExecutionReceipt).where(
                ToolExecutionReceipt.user_id == ctx.user_id, ToolExecutionReceipt.request_id == ctx.request_id,
                ToolExecutionReceipt.call_id == call.id))).scalar_one()
            receipt.status, receipt.result = "completed", asdict(result)
            if check_id:
                check = await db.get(WorkerCheck, check_id)
                check.after_revision = (await asyncio.to_thread(inventory, workspace))[0]
                check.result = asdict(result)
                check.status = "failed" if result.is_error else "completed"
        return result
