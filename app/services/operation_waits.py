"""Completion delivery and admission for suspended tool turns. No model polling."""
import asyncio
import json
import logging
import time
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from app.database import write_session
from app.models.operation_wait import (ToolBoundary, ToolBoundaryHistory, OperationBinding, OperationWaiter,
    ExecutorInbox, ExecutorCursor, OperationContinuation)
from app.models.model_policy import ToolExecutionReceipt
from app.models.coordination import AgentStep, CoordinationRun, ModelCallReceipt
from app.schemas.tool_outcome import PendingOperation
from app.schemas.llm import ProviderError
from app.services.coordination_budget import digest, run_expired


class OperationWaits:
    executor = 'orion-host-v1'

    def __init__(self, coordinator):
        self.c = coordinator
        self.sessions = coordinator.sessions
        self.task = None

    async def boundary(self, context, messages, iterations, continuations, cursor, prior_results):
        snapshot = dict(messages=messages, iterations=iterations, continuations=continuations,
            cursor=cursor, prior_results=list(prior_results), schemas=self.c.orchestrator._registry.list_tools(context),
            model_call_index=context.model_call_index, loaded_tools=sorted(context.extra.get('loaded_tools', [])),
            produced_files=context.extra.get('produced_files', []), display_tools=context.extra.get('display_tools', []),
            partial_text=context.extra.get('operation_partial_text', ''), model=context.model,
            policy=context.effective_policy.receipt() if context.effective_policy else None,
            persona=context.persona_snapshot, authority_revision=None)
        async with write_session(self.sessions) as db:
            run = await db.get(CoordinationRun, context.run_id)
            snapshot['authority_revision'] = run.policy['revision']
            row = await db.get(ToolBoundary, context.step_id)
            if row is None:
                db.add(ToolBoundary(step_id=context.step_id, generation=1, snapshot=snapshot))
            else:
                row.generation += 1
                row.snapshot, row.status = snapshot, 'running'
            db.add(ToolBoundaryHistory(step_id=context.step_id, generation=row.generation if row else 1, snapshot=snapshot))
            await self.c.persist_memory(db, context)

    async def submit(self, args, context):
        from app.services.host_operations import host_operation, operation_identity
        identity = operation_identity(args, context)
        fingerprint = digest({**args, 'operation_id': identity})
        async with write_session(self.sessions) as db:
            boundary = await db.get(ToolBoundary, context.step_id)
            if boundary is None:
                raise ProviderError('missing_boundary', 'Host dispatch requires a saved tool boundary')
            receipt = (await db.scalars(select(ToolExecutionReceipt).where(
                ToolExecutionReceipt.user_id == context.user_id,
                ToolExecutionReceipt.request_id == context.request_id,
                ToolExecutionReceipt.call_id == context.tool_call_id))).one()
            binding = (await db.scalars(select(OperationBinding).where(
                OperationBinding.executor == self.executor, OperationBinding.operation_id == identity))).one_or_none()
            inspecting = args['action'] == 'operation_status'
            if binding is not None and binding.user_id != context.user_id:
                raise ProviderError('operation_owner', 'Operation belongs to a different owner')
            if binding is None and not inspecting:
                binding = OperationBinding(executor=self.executor, operation_id=identity, user_id=context.user_id,
                    run_id=context.run_id, step_id=context.step_id, receipt_id=receipt.id, fingerprint=fingerprint,
                    deadline=time.time() + args['timeout'] if args.get('timeout') else None)
                db.add(binding)
                await db.flush()
            elif binding and not inspecting and binding.fingerprint != fingerprint:
                raise ProviderError('operation_conflict', 'Operation identity was reused with different arguments')
            if binding and inspecting and binding.result_hash and binding.state != 'reconciling':
                original = await db.get(ToolExecutionReceipt, binding.receipt_id)
                return original.result['text']
            if binding:
                receipt.status = 'prepared'
                if await db.get(OperationWaiter, receipt.id) is None:
                    db.add(OperationWaiter(receipt_id=receipt.id, binding_id=binding.id, step_id=context.step_id))
                pending = PendingOperation(self.executor, identity, binding.fingerprint, receipt.id, binding.deadline)
                if inspecting and binding.state in {'prepared', 'accepted', 'running'}:
                    receipt.status = 'waiting_external'
                    return pending
        reply = await host_operation(args, context)
        reply = {k:v for k,v in reply.items() if k not in {'source', 'operation_id'}}
        if binding is None:
            return json.dumps(reply, ensure_ascii=False)
        async with write_session(self.sessions) as db:
            binding = await db.get(OperationBinding, binding.id)
            receipt = await db.get(ToolExecutionReceipt, receipt.id)
            if receipt.status == 'completed':
                return receipt.result['text']
            if reply.get('status') in {'started', 'scheduled', 'accepted', 'running'}:
                binding.state, receipt.status = 'running', 'waiting_external'
                return pending
            if reply.get('status') in {'unknown', 'unavailable'}:
                binding.state, receipt.status = 'reconciling', 'reconciling'
            else:
                await self.attach(db, binding, reply, digest(reply), binding.version + 1)
        if reply.get('status') in {'unknown', 'unavailable'}:
            raise ProviderError('operation_reconciling', 'Host dispatch is uncertain; inspect saved execution evidence')
        return json.dumps(reply, ensure_ascii=False)

    async def attach(self, db, binding, reply, result_hash, version):
        binding.state = {'ok':'succeeded', 'timeout':'timed_out', 'denied':'failed', 'busy':'failed'}.get(reply['status'], reply['status'])
        binding.result_hash, binding.version = result_hash, version
        uncertain = reply['status'] in {'unknown', 'unavailable'}
        if uncertain:
            binding.state = 'reconciling'
        waiters = list(await db.scalars(select(OperationWaiter).where(OperationWaiter.binding_id == binding.id)))
        for waiter in waiters:
            receipt = await db.get(ToolExecutionReceipt, waiter.receipt_id)
            if uncertain:
                receipt.status = 'reconciling'
                boundary = await db.get(ToolBoundary, waiter.step_id)
                if boundary and boundary.status in {'waiting_operations', 'resume_ready'}:
                    boundary.status = 'held'
                continue
            if receipt.status != 'completed':
                receipt.status, receipt.result = 'completed', {'text':json.dumps(reply, ensure_ascii=False), 'files':[]}
            await self.ready(db, waiter.step_id)

    async def ready(self, db, step_id):
        boundary = await db.get(ToolBoundary, step_id)
        if not boundary or boundary.status != 'waiting_operations':
            return
        waiters = list(await db.scalars(select(OperationWaiter).where(OperationWaiter.step_id == step_id)))
        for waiter in waiters:
            if (await db.get(ToolExecutionReceipt, waiter.receipt_id)).status != 'completed':
                return
        boundary.status = 'resume_ready'
        step = await db.get(AgentStep, step_id)
        step.status = 'resume_ready' if step.status == 'waiting_operations' else step.status
        if await db.get(OperationContinuation, (step_id, boundary.generation)) is None:
            db.add(OperationContinuation(step_id=step_id, generation=boundary.generation))

    async def suspend(self, context, pending):
        async with write_session(self.sessions) as db:
            boundary = await db.get(ToolBoundary, context.step_id)
            boundary.status = 'waiting_operations'
            step = await db.get(AgentStep, context.step_id)
            step.status = 'waiting_operations'
            run = await db.get(CoordinationRun, context.run_id)
            room = await self.c.owned_room(db, context.user_id, context.room_id, lock=True)
            await self.c.event(db, room, 'operation_started', context.agent_id,
                {'operation_id':pending.operation_id, 'tool_receipt_id':pending.receipt_id}, run.id, step.id)
            await self.c.event(db, room, 'step_waiting', context.agent_id,
                {'operation_id':pending.operation_id, 'mode':step.mode,
                 'text':context.extra.get('operation_partial_text', '')}, run.id, step.id)
            await self.ready(db, step.id)
        await self.c.notify()

    async def accept(self, event):
        if (event.get('schema_version') != 1 or event.get('executor') != self.executor
                or event.get('result_hash') != digest(event.get('result'))
                or not isinstance(event.get('sequence'), int) or not isinstance(event.get('event_id'), str)
                or not isinstance(event.get('state_version'), int) or event['state_version'] < 1
                or not isinstance(event.get('result'), dict)
                or event['result'].get('status') not in {'ok', 'failed', 'timeout', 'cancelled', 'unknown', 'denied', 'busy'}):
            raise ValueError('Invalid completion envelope')
        payload_hash = digest(event)
        async with write_session(self.sessions) as db:
            cursor = await db.get(ExecutorCursor, self.executor, with_for_update=True)
            if cursor is None:
                cursor = ExecutorCursor(executor=self.executor, sequence=0)
                db.add(cursor)
            prior = await db.get(ExecutorInbox, (self.executor, event['event_id']))
            if prior:
                if prior.payload_hash != payload_hash:
                    prior.disposition = 'conflict'
                    binding = (await db.scalars(select(OperationBinding).where(
                        OperationBinding.executor == self.executor,
                        OperationBinding.operation_id == event.get('operation_id')))).one_or_none()
                    if binding:
                        await self.hold(db, binding)
                    return cursor.sequence
                return cursor.sequence
            if event['sequence'] != cursor.sequence + 1:
                raise ValueError('Non-contiguous event sequence')
            binding = (await db.scalars(select(OperationBinding).where(
                OperationBinding.executor == self.executor,
                OperationBinding.operation_id == event['operation_id']).with_for_update())).one_or_none()
            disposition = 'unmapped'
            if binding:
                if event['fingerprint'] != binding.fingerprint:
                    disposition = 'conflict'
                    await self.hold(db, binding)
                elif binding.result_hash and binding.result_hash != event['result_hash']:
                    disposition = 'conflict'
                    await self.hold(db, binding)
                elif event['state_version'] >= binding.version:
                    await self.attach(db, binding, event['result'], event['result_hash'], event['state_version'])
                    disposition = 'attached'
                    run = await db.get(CoordinationRun, binding.run_id)
                    room = await self.c.owned_room(db, binding.user_id, run.room_id, lock=True)
                    await self.c.event(db, room, 'operation_finished', 'system',
                        {'operation_id':binding.operation_id, 'status':binding.state}, run.id, binding.step_id)
                else:
                    disposition = 'stale'
            db.add(ExecutorInbox(executor=self.executor, event_id=event['event_id'], sequence=event['sequence'],
                payload_hash=payload_hash, disposition=disposition))
            cursor.sequence = event['sequence']
        await self.c.notify()
        return event['sequence']

    async def hold(self, db, binding):
        binding.state = 'reconciling'
        for waiter in await db.scalars(select(OperationWaiter).where(OperationWaiter.binding_id == binding.id)):
            boundary = await db.get(ToolBoundary, waiter.step_id)
            if boundary and boundary.status in {'waiting_operations', 'resume_ready'}:
                boundary.status = 'held'
                step = await db.get(AgentStep, waiter.step_id)
                if step.status in {'waiting_operations', 'resume_ready'}:
                    step.status = 'held'

    async def drain(self):
        """Claim ready continuations under the room lock, retaining root authority."""
        launch = []
        async with write_session(self.sessions) as db:
            rows = list(await db.scalars(select(OperationContinuation).where(OperationContinuation.status.in_(['ready', 'held']))))
            for queue in rows:
                step = await db.get(AgentStep, queue.step_id)
                run = await db.get(CoordinationRun, step.run_id)
                room = await self.c.owned_room(db, run.user_id, run.room_id, lock=True)
                if run.cancelled or step.status in {'cancelled','cancel_requested'}:
                    queue.status = 'suppressed'
                    continue
                revision, _ = await self.c.policy.snapshot(run.user_id)
                _, spending = await self.c.budget.policy(run.user_id)
                if (run_expired(run) or revision != run.policy['revision'] or spending.paused
                        or (step.mode != 'consult' and step.agent_id not in room.members)
                        or (not run.policy.get('shared_call_cap_exempt')
                            and run.calls >= run.limits['run']['calls'])):
                    queue.status = 'held'
                    continue
                # A saved boundary only resumes the original pinned provider route.
                boundary = await db.get(ToolBoundary, step.id)
                if boundary.generation != queue.generation or boundary.status != 'resume_ready':
                    queue.status = 'suppressed'
                    continue
                if run.id in self.c.live or room.active_run:
                    continue
                queue.status, queue.fence, queue.lease_until = 'admitted', queue.fence + 1, time.time() + 60
                room.active_run, run.status, run.executor = run.id, 'queued', None
                step.status = 'queued'
                launch.append(run.id)
        for run_id in launch:
            self.c.launch(run_id)

    async def restore(self, context):
        async with write_session(self.sessions) as db:
            boundary = await db.get(ToolBoundary, context.step_id)
            if not boundary or boundary.status != 'resume_ready':
                return
            run = await db.get(CoordinationRun, context.run_id, with_for_update=True)
            if run.executor != self.c.identity or run.lease_until <= time.time():
                raise ProviderError('executor_fenced', 'Continuation executor lost its lease')
            saved = boundary.snapshot
            if context.model != saved['model']:
                raise ProviderError('resume_provider_changed', 'Saved continuation requires its original provider route')
            context.extra['operation_boundary'] = saved
            context.model_call_index = saved['model_call_index']
            context.extra.update(loaded_tools=set(saved['loaded_tools']), produced_files=saved['produced_files'],
                display_tools=saved['display_tools'], operation_partial_text=saved['partial_text'])
            if self.c.orchestrator._registry.list_tools(context) != saved['schemas']:
                raise ProviderError('resume_schema_changed', 'Tool authority or schemas changed; continuation is held')
            boundary.status = 'running'
            queue = await db.get(OperationContinuation, (context.step_id, boundary.generation))
            queue.status = 'consumed'

    async def recover(self):
        """Recover only a known external wait after its executor lease expires."""
        async with write_session(self.sessions) as db:
            rows = list(await db.scalars(select(ToolBoundary).where(
                ToolBoundary.status.in_(['running', 'waiting_operations', 'resume_ready']))))
            for boundary in rows:
                step = await db.get(AgentStep, boundary.step_id)
                run = await db.get(CoordinationRun, step.run_id)
                if (run.cancelled or step.status in {'cancelled', 'cancel_requested', 'succeeded'}
                        or run.status not in {'running', 'waiting_operations'}
                        or run.lease_until > time.time()):
                    continue
                unknown = await db.scalar(select(func.count()).select_from(ModelCallReceipt).where(
                    ModelCallReceipt.run_id == run.id, ModelCallReceipt.status != 'complete'))
                waiters = list(await db.scalars(select(OperationWaiter).where(OperationWaiter.step_id == step.id)))
                if unknown or not waiters:
                    continue
                current_call = boundary.snapshot['messages'][-1]['content']
                calls = [b for b in current_call if b['type'] == 'tool_use']
                call = calls[boundary.snapshot['cursor']]
                receipt = (await db.scalars(select(ToolExecutionReceipt).where(
                    ToolExecutionReceipt.request_id == step.id, ToolExecutionReceipt.call_id == call['id']))).one_or_none()
                if not receipt or receipt.id not in {w.receipt_id for w in waiters}:
                    continue
                if receipt.status == 'reconciling':
                    boundary.status = 'held'
                    continue
                room = await self.c.owned_room(db, run.user_id, run.room_id, lock=True)
                boundary.status, step.status, run.status = 'waiting_operations', 'waiting_operations', 'waiting_operations'
                run.executor, run.lease_until = None, 0
                if room.active_run == run.id:
                    room.active_run = None
                await self.ready(db, step.id)

    async def listen(self):
        from app.services.host_operations import EVENT_SOCKET_PATH
        while True:
            writer = None
            try:
                await self.drain()
                async with self.sessions() as db:
                    cursor = await db.get(ExecutorCursor, self.executor)
                    after = cursor.sequence if cursor else 0
                reader, writer = await asyncio.open_unix_connection(EVENT_SOCKET_PATH, limit=512*1024)
                writer.write(json.dumps({'schema_version':1, 'after':after}).encode()+b'\n')
                await writer.drain()
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    event = json.loads(line)
                    if event.get('type') != 'heartbeat':
                        sequence = await self.accept(event)
                        writer.write(json.dumps({'ack':sequence}).encode()+b'\n')
                        await writer.drain()
                    await self.drain()
            except (OSError, ValueError, NotImplementedError, SQLAlchemyError):
                logging.getLogger(__name__).debug('operation_event_transport_retry', exc_info=True)
                await asyncio.sleep(1)
            finally:
                if writer:
                    writer.close()

    def start(self):
        self.task = asyncio.create_task(self.listen())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
