"""Bounded in-process read-only workers; policies use the same model boundary."""
import asyncio
import json
from dataclasses import replace
from uuid import uuid4, uuid5, NAMESPACE_URL

from app.core.context import AgentContext
from app.schemas.llm import ProviderError, ThinkingRequest
from app.schemas.sse import SSEEventType


class TaskRunner:
    def __init__(self, orchestrator, policy, activity=None):
        self.orchestrator, self.policy, self.activity = orchestrator, policy, activity

    async def __call__(self, args, parent):
        if parent.task_role == "worker":
            raise ProviderError("worker_depth", "Workers cannot start further workers")
        from app.profiles.workers import worker_spec
        spec = worker_spec(args.get('worker_id', args.get('subagent_type', 'general')))
        prompt = args["prompt"]
        if len(prompt) > 24000:
            raise ValueError("Worker prompt exceeds the bounded context allowance")
        effective = parent.effective_policy
        if effective is None:
            raise ProviderError("missing_policy", "Worker requires an admitted parent policy")
        activity = self.activity or getattr(parent.coordination, 'workers', None)
        if spec.backend == 'forge':
            if not parent.coordination or not parent.coordination.projects or not parent.run_id:
                raise ProviderError('worker_unavailable', 'Forge requires an admitted Coordinator run')
            from app.schemas.coordination import Delegate
            result = await parent.coordination.projects.delegate(parent, Delegate(
                role='reviewer' if spec.worker_id == 'forge_reviewer' else spec.worker_id,task=prompt,
                acceptance=args.get('acceptance') or ['Complete the assigned scope and report evidence and checks'],
                dependencies=args.get('dependencies', [])))
            result.update(execution_id=result['task_id'],backend='forge',worker=spec.worker_id,
                meaning='Admitted asynchronously; inspect progress or steer by execution ID while continuing the conversation')
            return json.dumps(result)
        if 'background' in args and 'run_in_background' in args and args['background'] != args['run_in_background']:
            raise ValueError('Conflicting worker background controls')
        background = args.get('background',args.get('run_in_background',False))
        if not isinstance(background,bool):
            raise ValueError('Worker background must be a boolean')
        if background and (not activity or not parent.run_id):
            raise ProviderError('worker_unavailable','Detached workers require an admitted Coordinator run')
        requested = ThinkingRequest.model_validate(args['thinking']) if 'thinking' in args else None
        resolved = await self.policy.resolve_worker_policy(spec.worker_id, model=parent.model, request=requested, parent=effective,
            user_id=parent.user_id, agent_id=parent.agent_id,
            snapshot=(effective.revision, effective.snapshot), deadline=effective.deadline)
        child_id = uuid5(NAMESPACE_URL, f"{parent.request_id}:{parent.tool_call_id}").hex if parent.run_id else uuid4().hex
        child = AgentContext(user_id=parent.user_id, session_id=parent.session_id,
            request_id=child_id, triggered_by="agent", trigger_payload={}, output_mode="silent",
            model=resolved.spec.ref, system_prompt="", conversation_history=[{"role": "user", "content": prompt}],
            db=None, timezone=parent.timezone, agent_id=parent.agent_id, task_role="worker",
            effective_policy=replace(resolved, fallbacks=()),
            memory_budget=parent.memory_budget, memory_packet=parent.memory_packet,
            room_id=parent.room_id, run_id=parent.run_id, step_id=child_id,
            parent_step_id=parent.step_id, project_id=parent.project_id,
            persona_snapshot=parent.persona_snapshot, allowed_tools=parent.allowed_tools,
            coordination=parent.coordination, budget_handle=parent.budget_handle,
            prompt_datetime=parent.prompt_datetime, output_token_cap=parent.output_token_cap)
        child.extra['mcp_grants'] = parent.extra.get('mcp_grants', frozenset())
        child.extra['mcp_bindings'] = dict(parent.extra.get('mcp_bindings', {}))
        child.extra['mcp_schemas'] = dict(parent.extra.get('mcp_schemas', {}))
        if 'active_servers' in parent.extra:
            child.extra['active_servers'] = frozenset(parent.extra['active_servers'])
        child.extra['worker_role'],child.extra['max_tool_iterations'] = spec.worker_id,spec.max_iterations
        if spec.tool_scope is not None:
            child.extra['worker_tool_scope'] = spec.tool_scope
            child.extra['mcp_grants'] = frozenset()
        if activity and parent.run_id:
            admission = await activity.begin(parent,child_id,spec.worker_id,prompt,child.model,background)
            if admission['duplicate']:
                if admission['status'] in {'succeeded','failed','cancelled','interrupted'}:
                    return admission['result'] or 'Worker ended without a completed result'
                raise ProviderError('worker_reconciliation','Existing worker admission must be inspected, not repeated')
            from app.services.worker_control import LegionInbox
            child.extra['worker_inbox'] = LegionInbox(activity,child)
            job = asyncio.create_task(self._run(child,activity))
            activity.jobs[child_id] = job
            def settled(done):
                if not done.cancelled():done.exception()
                activity.jobs.pop(child_id,None)
            job.add_done_callback(settled)
            if background:
                return json.dumps({'execution_id':child_id,'ticket':admission['ticket'],'status':admission['status'],
                    'background':True,'meaning':'Child admitted; its result is not available yet. Inspect or collect it separately.'})
            try:
                return await job
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                return 'Worker interrupted; inspect its recorded result and any uncertain outcomes.'
        return await self._run(child,None)

    async def _run(self, child, activity):
        text = []
        pending = ''
        status = 'succeeded'
        try:
            if activity and not await activity.start(child):
                return 'Worker cancelled before dispatch; no result was produced.'
            async for event in self.orchestrator.run(child):
                if event.type == SSEEventType.CHUNK:
                    text.append(event.data)
                    pending += event.data
                    if activity and len(pending) >= 256:
                        await activity.publish(child,{'phase':'text','text':pending})
                        pending=''
                elif event.type in {SSEEventType.TOOL,SSEEventType.TOOL_RESULT} and activity:
                    if pending:
                        await activity.publish(child,{'phase':'text','text':pending})
                        pending=''
                    await activity.publish(child,{'phase':event.type.value,**event.data,
                        'tool_call_id':event.data.get('id')})
                elif event.type == SSEEventType.ERROR:
                    status='failed'
                    text.append('Worker incomplete: ' + str(event.data))
            result = ''.join(text)[-32000:]
            if activity:
                if pending:await activity.publish(child,{'phase':'text','text':pending})
                finished=await activity.finish(child,status,result)
                if finished != 'succeeded' and status=='succeeded':
                    result=f'Worker {finished}; partial findings follow:\n'+result
            return result
        except BaseException as exc:
            if activity:
                reason = ''.join(text)[-24000:] + ('\nWorker interrupted; partial findings retained.' if isinstance(exc,asyncio.CancelledError)
                    else f'\nWorker stopped ({type(exc).__name__}); inspect saved outcomes.')
                await asyncio.shield(activity.finish(child,'interrupted' if isinstance(exc,asyncio.CancelledError) else 'failed',reason))
            raise
