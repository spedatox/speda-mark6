"""Child activity over Coordinator records; Forge state is a read projection.

Inspection/reconnection never resurrects an executor. Only TaskRunner writes
Legion execution state; Projects/Workshop continue to own project mutations.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from uuid import uuid4
from app.database import write_session
from sqlalchemy import select, update, func
from app.models.coordination import Room, RoomEvent, CoordinationRun, ProjectTask, ModelCallReceipt, AgentStep
from app.models.legion import LegionExecution, WorkerTicket, LegionInput, WorkerEventArtifact
from app.models.model_policy import ToolExecutionReceipt
from app.services.coordination_budget import CoordinationError, digest, run_expired
from app.schemas.coordination import Steering

ACTIVE = {'admitted','queued','staging','running','cancel_requested'}
TERMINAL = {'succeeded','failed','cancelled','interrupted','reconciling','blocked','expired'}


class WorkerControl:
    def __init__(self, coordinator):
        self.coordinator, self.sessions = coordinator, coordinator.sessions
        self.jobs = {}

    @staticmethod
    def valid_id(value):
        if not re.fullmatch(r'[a-f0-9]{32}', value):
            raise CoordinationError('invalid_execution', 'Use a native worker execution ID', 400)
        return value

    async def ticket(self, db, execution_id, kind):
        # Call while holding the owning room's write lock.
        row = await db.scalar(select(WorkerTicket).where(WorkerTicket.execution_id == execution_id))
        if row is None:
            row = WorkerTicket(execution_id=execution_id, kind=kind)
            db.add(row)
            await db.flush()
        if row.kind != kind:
            raise CoordinationError('execution_conflict', 'Worker handle belongs to another execution')
        return row.id

    async def owned(self, db, owner, execution_id):
        self.valid_id(execution_id)
        row = await db.get(LegionExecution, execution_id)
        kind = 'legion'
        if row is None:
            row, kind = await db.get(ProjectTask, execution_id), 'forge'
        run = await db.get(CoordinationRun, row.run_id) if row else None
        if run is None or run.user_id != owner:
            raise CoordinationError('not_found', 'Worker execution unavailable', 404)
        room = await self.coordinator.owned_room(db, owner, run.room_id)
        return row, run, room, kind

    async def bound_context(self, db, context):
        row, run, room, kind = await self.owned(db,context.user_id,context.step_id)
        if (kind != 'legion' or row.run_id != context.run_id or room.id != context.room_id
                or row.owner_agent != context.agent_id or context.task_role != 'worker'):
            raise CoordinationError('worker_scope','Worker context does not match its durable admission')
        return row,run,room

    async def bounded_payload(self, db, execution_id, payload):
        encoded=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        if len(encoded) <= 16000:
            return payload
        key=hashlib.sha256(encoded).hexdigest()
        total=await db.scalar(select(func.count()).select_from(WorkerEventArtifact).where(WorkerEventArtifact.execution_id == execution_id))
        artifact=None
        if len(encoded) <= 256000 and total < 32:
            if not await db.get(WorkerEventArtifact,(execution_id,key)):
                db.add(WorkerEventArtifact(execution_id=execution_id,digest=key,data=encoded))
            artifact=key
        bounded={'truncated':True,'event_artifact':artifact,'original_bytes':len(encoded)}
        for field,ceiling in {'phase':40,'tool_call_id':128,'name':128,'status':40,'text':5000,'result':5000,'input':1500,'error':256}.items():
            if field in payload:
                value=payload[field]
                if isinstance(value,bool):bounded[field]=value
                else:
                    value=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
                    bounded[field]=value.encode('utf-8')[:ceiling].decode('utf-8',errors='ignore')
        return bounded

    async def begin(self, parent, execution_id, role, prompt, model, background):
        self.valid_id(execution_id)
        from app.profiles.workers import worker_spec
        spec=worker_spec(role)
        if spec.backend != 'legion':
            raise CoordinationError('worker_backend','Forge roles require native project delegation')
        role=spec.worker_id
        await self.coordinator.ensure_active(parent)
        async with write_session(self.sessions) as db:
            room = await self.coordinator.owned_room(db, parent.user_id, parent.room_id, lock=True)
            run=await db.get(CoordinationRun,parent.run_id)
            step=await db.get(AgentStep,parent.step_id)
            if (parent.task_role == 'worker' or run.room_id != room.id or step is None or step.run_id != run.id
                    or step.agent_id != parent.agent_id or step.status != 'running'
                    or 'Task' not in run.profiles.get(parent.agent_id,{}).get('tools',[])):
                raise CoordinationError('worker_scope','Worker admission requires its active named supervisor contribution')
            row = await db.get(LegionExecution, execution_id)
            input_hash = digest({'role':role,'prompt':prompt,'parent':parent.step_id,'background':background,'model':model})
            if row:
                if row.input_hash != input_hash or row.run_id != parent.run_id:
                    raise CoordinationError('worker_conflict', 'Worker ID belongs to another task')
                return {'duplicate':True,'status':row.status,'result':row.result}
            count = await db.scalar(select(func.count()).select_from(LegionExecution).where(LegionExecution.run_id == parent.run_id))
            count += await db.scalar(select(func.count()).select_from(ProjectTask).where(ProjectTask.run_id == parent.run_id))
            live = await db.scalar(select(func.count()).select_from(LegionExecution).where(
                LegionExecution.run_id == parent.run_id, LegionExecution.status.in_(ACTIVE)))
            if count >= 8 or live >= 3:
                raise CoordinationError('worker_limit', 'Root run permits eight child assignments and three active Legion workers')
            row = LegionExecution(id=execution_id, run_id=parent.run_id, parent_step=parent.step_id,
                owner_agent=parent.agent_id, role=role, task=prompt, model=model,
                input_hash=input_hash, background=background)
            db.add(row)
            ticket = await self.ticket(db, execution_id, 'legion')
            await self.coordinator.event(db, room, 'worker_event', role,
                {'execution_id':execution_id,'phase':'started','status':'queued','model':model,'background':background}, parent.run_id, execution_id)
        await self.coordinator.notify()
        return {'duplicate':False,'ticket':ticket,'status':'admitted'}

    async def start(self,context):
        async with write_session(self.sessions) as db:
            row,run,room=await self.bound_context(db,context)
            room=await self.coordinator.owned_room(db,context.user_id,room.id,lock=True)
            await db.refresh(row)
            if row.cancelled or row.status in TERMINAL:
                return False
            if (run.cancelled or run.status not in {'running','waiting_tasks'} or run.executor != self.coordinator.identity
                    or run.lease_until <= time.time() or run_expired(run)):
                row.status,row.result,row.finished_at='interrupted','Root admission ended before worker dispatch',datetime.utcnow()
                return False
            if row.status != 'admitted':
                raise CoordinationError('worker_replay','Worker dispatch already began; do not repeat it')
            row.status='running'
            await self.coordinator.event(db,room,'worker_event',row.role,
                {'execution_id':row.id,'phase':'started','status':'running','model':row.model},run.id,row.id)
        await self.coordinator.notify()
        return True

    async def publish(self, context, payload):
        async with write_session(self.sessions) as db:
            row,_,_ = await self.bound_context(db,context)
            room = await self.coordinator.owned_room(db, context.user_id, context.room_id, lock=True)
            await db.refresh(row)
            if row.status in TERMINAL:
                raise CoordinationError('worker_closed','Late worker output cannot replace a terminal result')
            if row.cancelled and payload.get('phase') in {'text','tool','tool_result'}:
                raise CoordinationError('worker_stopped','Output arrived after this task was stopped')
            payload = await self.bounded_payload(db,row.id,payload)
            await self.coordinator.event(db, room, 'worker_event', row.role,
                {**payload,'execution_id':row.id}, row.run_id, row.id)
        await self.coordinator.notify()

    async def finish(self, context, status, text):
        async with write_session(self.sessions) as db:
            row,_,_ = await self.bound_context(db,context)
            room = await self.coordinator.owned_room(db, context.user_id, context.room_id, lock=True)
            await db.refresh(row)
            if row.status in TERMINAL:
                return row.status
            unknown = await db.scalar(select(func.count()).select_from(ModelCallReceipt).where(
                ModelCallReceipt.run_id == row.run_id, ModelCallReceipt.call_key.startswith(row.id + ':'),
                ModelCallReceipt.status.in_(['started', 'unknown'])))
            unknown += await db.scalar(select(func.count()).select_from(ToolExecutionReceipt).where(
                ToolExecutionReceipt.request_id == row.id, ToolExecutionReceipt.status != 'completed'))
            row.status = 'reconciling' if unknown else 'interrupted' if row.cancelled else status
            row.result, row.finished_at = text[-32000:], datetime.utcnow()
            payload=await self.bounded_payload(db,row.id,{'phase':'finished','status':row.status,
                'result':row.result,'error':row.status != 'succeeded'})
            await self.coordinator.event(db, room, 'worker_event', row.role,
                {**payload,'execution_id':row.id}, row.run_id, row.id)
        await self.coordinator.notify()
        return row.status

    async def ensure_active(self, context):
        async with self.sessions() as db:
            row,_,_ = await self.bound_context(db,context)
            if row.cancelled or row.status != 'running':
                raise CoordinationError('worker_stopped', 'This Legion task is no longer admitted')

    async def snapshot(self, db, owner, execution_id):
        row, run, room, kind = await self.owned(db, owner, execution_id)
        role = row.role
        from app.profiles.workers import worker_spec
        spec=worker_spec(role)
        project = kind == 'forge'
        latest = await db.scalar(select(func.max(RoomEvent.sequence)).where(RoomEvent.room_id == room.id,
            (RoomEvent.payload['task_id'].as_string() == row.id) | (RoomEvent.payload['execution_id'].as_string() == row.id))) or 0
        started=await db.scalar(select(RoomEvent.payload).where(RoomEvent.room_id == room.id,
            RoomEvent.kind == 'project_started',RoomEvent.payload['task_id'].as_string() == row.id)
            .order_by(RoomEvent.sequence.desc()).limit(1)) if project else None
        handle = await db.scalar(select(WorkerTicket.id).where(WorkerTicket.execution_id == row.id))
        job = self.coordinator.projects.jobs.get(row.id) if project and self.coordinator.projects else self.jobs.get(row.id)
        executor = bool(job and not job.done() and run.executor == self.coordinator.identity and run.lease_until > time.time())
        stored = row.status
        status = 'unknown' if stored in ACTIVE and not executor and (stored != 'queued' or run.status not in {'running','waiting_tasks'}) else stored
        created = run.created_at if project else row.created_at
        return {'execution_id':row.id,'id':row.id,'ui_run_id':row.id,'ticket_id':handle,
            'run_id':run.id,'room_id':room.id,'project_id':row.project_id if project else None,
            'agent_id':row.owner_agent,'owner_agent':row.owner_agent,'supervisor_agent':row.owner_agent,
            'team_lead':spec.team_lead,'team':spec.team,
            'role':role,'worker':spec.worker_id,'backend':kind,'execution_type':kind,'session_id':room.session_id,
            'parent_request_id':run.input_id,'parent_step_id':row.parent_step,
            'background':True if project else row.background,
            'model':(started or {}).get('model',run.policy.get('document',{}).get('worker_models',{}).get(spec.worker_id,run.policy.get('model'))) if project else row.model,
            'task':row.task,'label':row.task[:120],'status':status,'stored_status':stored,
            'executor_available':executor,'interrupt_requested':row.cancelled,'last_sequence':latest,
            'result':row.result,'workspace':row.workspace if project else None,
            'created_at':created.isoformat(),'finished_at':row.finished_at.isoformat() if not project and row.finished_at else None,
            'can_interrupt':stored in ACTIVE and not row.cancelled and (executor or stored == 'queued'),
            'can_message':stored in ACTIVE and not row.cancelled and run.status in {'running','waiting_tasks'}
                and (executor or project and stored == 'queued' and run.executor == self.coordinator.identity and run.lease_until > time.time())}

    async def inspect(self, owner, execution_id):
        async with write_session(self.sessions) as db:
            row, run, room, kind = await self.owned(db, owner, execution_id)
            await self.coordinator.owned_room(db, owner, room.id, lock=True)
            await self.ticket(db, execution_id, kind)
            value = await self.snapshot(db, owner, execution_id)
            if kind == 'legion':
                inputs = list(await db.scalars(select(LegionInput).where(LegionInput.execution_id == row.id)
                    .order_by(LegionInput.created_at,LegionInput.id).limit(100)))
            else:
                from app.models.coordination import TaskInput
                inputs = list(await db.scalars(select(TaskInput).where(TaskInput.task_id == row.id)
                    .order_by(TaskInput.created_at,TaskInput.id).limit(100)))
            value['messages'] = [{'message_id':i.input_id,'id':i.id,'text':i.text,'status':i.status,'boundary_hash':i.boundary_hash} for i in inputs]
            receipts=list(await db.scalars(select(ToolExecutionReceipt).where(ToolExecutionReceipt.user_id == owner,
                ToolExecutionReceipt.request_id == execution_id).limit(100)))
            value['tool_receipts']=[{'call_id':receipt.call_id,'name':receipt.name,'status':receipt.status,
                'arguments_hash':receipt.arguments_hash} for receipt in receipts]
            return value

    async def list(self, owner, session_id=None, agent_id=None, limit=50):
        limit = max(1,min(limit,100))
        async with self.sessions() as db:
            rows = []
            for cls in (LegionExecution,ProjectTask):
                query = select(cls.id).join(CoordinationRun,cls.run_id == CoordinationRun.id).join(Room,CoordinationRun.room_id == Room.id).where(CoordinationRun.user_id == owner)
                if session_id is not None:
                    query = query.where(Room.session_id == session_id)
                if agent_id:
                    query = query.where(cls.owner_agent == self.coordinator.profiles.canonical(agent_id))
                rows.extend(list(await db.scalars(query.order_by(CoordinationRun.created_at.desc()).limit(limit))))
        values = [await self.inspect(owner, execution_id) for execution_id in rows]
        return sorted(values,key=lambda row:(row['created_at'],row['execution_id']),reverse=True)[:limit]

    async def active(self, owner, session_id=None):
        rows = await self.list(owner,session_id)
        return [{'ticket':row['ticket_id'],'execution_id':row['execution_id'],'agent':row['role'],
            'label':row['label'],'source':row['backend'],'owner_agent':row['owner_agent'],
            'room_session_id':row['session_id'],'status':row['status'],
            'running_s':max(0,time.time()-datetime.fromisoformat(row['created_at']).replace(tzinfo=timezone.utc).timestamp())}
            for row in rows if row['stored_status'] in ACTIVE and row['status'] != 'unknown']

    async def history_cards(self,owner,session_id):
        grouped={}
        for row in await self.list(owner,session_id,limit=50):
            page=await self.events(owner,row['execution_id'])
            card={'id':row['execution_id'],'executionId':row['execution_id'],'agent':row['worker'],
                'label':row['label'],'prompt':row['task'],'source':row['backend'],'executionType':row['backend'],
                'ownerAgent':row['owner_agent'],'workerRole':row['worker'],'status':row['status'],
                'running':row['stored_status'] in ACTIVE and row['status'] != 'unknown',
                'canInterrupt':row['can_interrupt'],'canMessage':row['can_message'],'model':row['model'],
                'ok':row['stored_status'] == 'succeeded','steps':[], 'lastSequence':page['next_cursor'],
                'seenEventIds':[event['event_id'] for event in page['events']],
                'lastStoredSequence':page['last_sequence'],
                'activityPartial':page['next_cursor'] < page['last_sequence']}
            result=row['result']
            card['report']=(result.get('report',result.get('reason','')) if isinstance(result,dict) else result) or ''
            for event in page['events']:
                phase=event.get('phase')
                if phase=='text' and event.get('text'):
                    text=str(event['text'])
                    if card['steps'] and card['steps'][-1]['kind']=='text':
                        combined=card['steps'][-1]['text']+text
                        if len(combined)>32000:card['activityPartial']=True
                        card['steps'][-1]['text']=combined[-32000:]
                    else:
                        if len(text)>32000:card['activityPartial']=True
                        card['steps'].append({'kind':'text','text':text[-32000:]})
                elif phase=='tool':
                    card['steps'].append({'kind':'tool','tool':event.get('tool',event.get('name','tool')),
                        'input':event.get('input'),'toolCallId':event.get('tool_call_id')})
                elif phase=='tool_result':
                    for tool in reversed(card['steps']):
                        if tool['kind']=='tool' and tool.get('toolCallId')==event.get('tool_call_id'):
                            result_text=str(event.get('result',''))
                            if len(result_text)>8000:card['activityPartial']=True
                            tool['result']=result_text[:8000]
                            break
            if len(card['steps'])>100:card['activityPartial']=True
            card['steps']=card['steps'][-100:]
            grouped.setdefault(row['parent_step_id'],[]).append(card)
        return grouped

    @staticmethod
    def project_payload(event):
        payload = event['payload']
        kind = event['kind']
        task_id = payload.get('task_id')
        if not task_id:
            return None
        common = {'execution_id':task_id,'id':task_id,'source':'forge','execution_type':'forge',
            'agent':payload.get('role',event['speaker']), 'owner_agent':payload.get('owner_agent'),
            'role':payload.get('role'), 'label':payload.get('role',event['speaker'])}
        if kind in {'project_queued','project_started'}:
            return {**common,'phase':'started','task':payload.get('task'),'status':'queued' if kind == 'project_queued' else 'running'}
        if kind == 'project_result':
            artifact = payload.get('artifact',{})
            return {**common,'phase':'finished','result':payload.get('text',''), 'status':artifact.get('status','succeeded'),
                'error':artifact.get('status','succeeded') != 'succeeded','artifact':artifact}
        if kind == 'artifact_reviewed':
            return {**common, 'phase': 'text', 'text': '\nSupervisor assessment by ' + event['speaker'] + ': '
                + payload['verdict'] + '. ' + payload['rationale'] + '\n', 'assessment': payload['verdict']}
        if kind in {'task_cancel_requested','task_input_queued','task_input_delivered'}:
            phase={'task_cancel_requested':'interrupt_requested','task_input_queued':'input_queued','task_input_delivered':'input_delivered'}[kind]
            if kind=='task_cancel_requested' and payload.get('status')=='cancelled':phase='finished'
            return {**common,'phase':phase,**payload}
        if kind == 'project_event':
            native = payload.get('event',{})
            event_type, data = native.get('type'), native.get('data')
            data = data if isinstance(data,dict) else {'text':str(data or '')}
            phase = {'text':'text','chunk':'text','tool':'tool','tool_use':'tool','tool_result':'tool_result'}.get(event_type)
            result=data.get('result')
            if result is None:result=data.get('display')
            if result is None:result=data.get('content')
            return {**common,**data,'phase':phase or 'progress','native_type':event_type,
                'tool_call_id':data.get('tool_call_id',data.get('tool_use_id',data.get('id'))),
                'tool':data.get('tool',data.get('name')),
                'result':result,'is_error':bool(data.get('is_error')),'error':str(result) if data.get('is_error') else None}
        if kind == 'project_held':
            return {**common,'phase':'finished','status':'reconciling','error':True,'result':payload.get('reason','Execution requires reconciliation')}
        return None

    def envelope(self, event, snapshot):
        if event['kind'] == 'worker_event':
            payload = event['payload']
        else:
            payload = self.project_payload(event)
        if payload is None:
            return None
        return {**payload,'id':snapshot['execution_id'],'execution_id':snapshot['execution_id'],
            'tool':payload.get('tool',payload.get('name')), 'prompt':snapshot['task'],
            'report':payload.get('report',payload.get('result')) if payload.get('phase') == 'finished' else None,
            'ok':payload.get('status') == 'succeeded' if payload.get('phase') == 'finished' else None,
            'agent':snapshot['role'],'role':snapshot['role'],'label':snapshot['label'],
            'source':snapshot['backend'],'execution_type':snapshot['backend'],'owner_agent':snapshot['owner_agent'],
            'team_lead':snapshot['team_lead'],'team':snapshot['team'],
            'parent_step_id':snapshot['parent_step_id'],
            'sequence':event['sequence'],'event_id':f"{event['room_id']}:{event['sequence']}"}

    async def events(self, owner, execution_id, after=0):
        snapshot = await self.inspect(owner,execution_id)
        if after < 0 or after > snapshot['last_sequence']:
            raise CoordinationError('invalid_cursor','Worker cursor is outside the saved event range',400)
        async with self.sessions() as db:
            row,run,room,_ = await self.owned(db,owner,execution_id)
            events = list(await db.scalars(select(RoomEvent).where(RoomEvent.room_id == room.id,
                RoomEvent.sequence > after,
                (RoomEvent.payload['task_id'].as_string() == execution_id) | (RoomEvent.payload['execution_id'].as_string() == execution_id))
                .order_by(RoomEvent.sequence).limit(200)))
        frames = [self.envelope({'kind':e.kind,'payload':e.payload,'speaker':e.speaker,'room_id':e.room_id,'sequence':e.sequence},snapshot) for e in events]
        return {'execution':snapshot,'events':[frame for frame in frames if frame],
            'last_sequence':snapshot['last_sequence'],'next_cursor':events[-1].sequence if events else after}

    async def subscribe(self, owner, execution_id, after=0):
        while not self.coordinator.restarting:
            page = await self.events(owner,execution_id,after)
            for payload in page['events']:
                yield 'data: ' + json.dumps({'type':'subagent','data':payload,'session_id':page['execution']['session_id'],
                    'request_id':execution_id},ensure_ascii=False) + '\n\n'
            after = page['next_cursor']
            if after < page['last_sequence']:
                continue
            if page['execution']['stored_status'] in TERMINAL:
                return
            if page['execution']['status'] == 'unknown':
                yield 'data: ' + json.dumps({'type':'subagent','data':{'id':execution_id,'execution_id':execution_id,
                    'phase':'executor_unavailable','status':'unknown'},'request_id':execution_id}) + '\n\n'
                return
            try:
                await self.coordinator.wait_for_event(1)
            except TimeoutError:
                yield ': keepalive\n\n'

    async def resolve_ticket(self, owner, ticket):
        async with self.sessions() as db:
            row = await db.get(WorkerTicket,ticket)
            if row is None:
                raise CoordinationError('not_found','Worker ticket unavailable',404)
            await self.owned(db,owner,row.execution_id)
            return row.execution_id

    async def session_room(self, owner, session_id):
        async with self.sessions() as db:
            from app.models.client_compat import ClientSession
            presentation = await db.get(ClientSession, session_id)
            if presentation and presentation.hidden:
                raise CoordinationError('not_found', 'Worker conversation unavailable', 404)
            room = await db.scalar(select(Room).where(Room.user_id == owner, Room.session_id == session_id))
            if room is None:
                raise CoordinationError('not_found', 'Worker conversation unavailable', 404)
            return room.id

    async def session_snapshot(self, owner, session_id):
        from app.models.client_compat import HiddenMessage
        room_id = await self.session_room(owner, session_id)
        async with self.sessions() as db:
            room = await self.coordinator.owned_room(db, owner, room_id)
            # Capture replay floor BEFORE reading child projections. Later events
            # may overlap those projections, but none can fall into a gap.
            sequence = room.sequence
            parents = {}
            hidden = set(await db.scalars(select(HiddenMessage.message_id).where(HiddenMessage.session_id == session_id)))
            hidden_steps = set()
            references = (await db.execute(select(RoomEvent.step_id, RoomEvent.payload['message_id'].as_integer()).where(
                RoomEvent.room_id == room_id, RoomEvent.kind.in_(['message', 'consult_result'])))).all()
            for step_id, message_id in references:
                if step_id and message_id:
                    if message_id in hidden:
                        hidden_steps.add(step_id)
                    else:
                        parents[step_id] = str(message_id)
            total = 0
            for cls in (LegionExecution, ProjectTask):
                total += await db.scalar(select(func.count()).select_from(cls).join(CoordinationRun,
                    cls.run_id == CoordinationRun.id).where(CoordinationRun.room_id == room_id,
                    cls.parent_step.not_in(hidden_steps))) or 0
        cards, size, omitted = [], 0, 0
        for step_id, runs in (await self.history_cards(owner, session_id)).items():
            if step_id in hidden_steps:
                continue
            for run in runs:
                run['lastSequence'] = run.pop('lastStoredSequence', run['lastSequence'])
                run['seenEventIds'] = run['seenEventIds'][-128:]
                for field, limit in {'prompt': 4000, 'report': 16000}.items():
                    encoded_field = str(run.get(field, '')).encode()
                    if len(encoded_field) > limit:
                        run[field] = encoded_field[:limit].decode(errors='ignore')
                        run['activityPartial'] = True
                while len(json.dumps(run, ensure_ascii=False).encode()) > 40000 and run['steps']:
                    run['steps'].pop(0)
                    run['activityPartial'] = True
                item = {'parent_step_id': step_id, 'parent_message_id': parents.get(step_id), 'run': run}
                encoded = len(json.dumps(item, ensure_ascii=False).encode())
                if size + encoded > 1000000:
                    omitted += 1
                    continue
                cards.append(item)
                size += encoded
        return {'session_id': session_id, 'sequence': sequence, 'cards': cards,
            'omitted': max(omitted, total - len(cards))}

    async def subscribe_session(self, owner, session_id, after=0):
        """One connection for all room workers, independent of public reply streams."""
        room_id = await self.session_room(owner, session_id)
        while not self.coordinator.restarting:
            page = await self.coordinator.events(owner, room_id, after)
            from app.models.client_compat import HiddenMessage
            async with self.sessions() as db:
                hidden_steps = set(await db.scalars(select(RoomEvent.step_id).where(RoomEvent.room_id == room_id,
                    RoomEvent.kind.in_(['message','consult_result']), RoomEvent.payload['message_id'].as_integer().in_(
                        select(HiddenMessage.message_id).where(HiddenMessage.session_id == session_id)))))
            snapshots = {}
            for event in page['events']:
                payload = event['payload']
                execution_id = payload.get('execution_id', payload.get('task_id'))
                if not execution_id:
                    continue
                if execution_id not in snapshots:
                    snapshots[execution_id] = await self.inspect(owner, execution_id)
                snapshot = snapshots[execution_id]
                if snapshot['parent_step_id'] in hidden_steps:
                    continue
                frame = self.envelope(event, snapshot)
                if frame:
                    yield 'data: ' + json.dumps({'type': 'subagent', 'data': frame,
                        'session_id': session_id, 'request_id': execution_id}, ensure_ascii=False) + '\n\n'
            after = page['next_cursor']
            if page['events']:
                continue
            try:
                await self.coordinator.wait_for_event(1)
            except TimeoutError:
                yield ': keepalive\n\n'

    async def interrupt(self, owner, execution_id, *, origin='owner', sender=None):
        before = await self.inspect(owner,execution_id)
        if origin != 'owner' and (origin != 'agent' or before['owner_agent'] != sender):
            raise CoordinationError('worker_scope','Only the admitted supervisor may stop this worker')
        if before['stored_status'] not in ACTIVE:
            return {'accepted':False,'execution':before}
        if before['backend'] == 'forge':
            if origin == 'owner':
                result=await self.coordinator.projects.stop_task(owner,execution_id)
            else:
                result=await self.coordinator.projects.stop_task(owner,execution_id,origin=origin,sender=sender)
            if isinstance(result,dict) and not result.get('task_control',{}).get('accepted',True):
                return {'accepted':False,'execution':await self.inspect(owner,execution_id)}
        else:
            async with write_session(self.sessions) as db:
                row,run,room,_ = await self.owned(db,owner,execution_id)
                await self.coordinator.owned_room(db,owner,room.id,lock=True)
                await db.refresh(row)
                if row.status not in ACTIVE:
                    return {'accepted':False,'execution':before}
                first=not row.cancelled
                row.cancelled=True
                if first:
                    await self.coordinator.event(db,room,'worker_event',sender if origin == 'agent' else 'owner',
                        {'execution_id':row.id,'phase':'interrupt_requested','origin':origin,'sender_agent':sender},run.id,row.id)
                unstarted=row.status=='admitted'
                if unstarted:
                    row.status,row.result,row.finished_at='interrupted','Cancelled before worker dispatch',datetime.utcnow()
                    await self.coordinator.event(db,room,'worker_event',row.role,
                        {'execution_id':row.id,'phase':'finished','status':'interrupted','result':row.result},run.id,row.id)
            job = self.jobs.get(execution_id)
            if job and not job.done():
                if not job.cancelling():job.cancel()
            elif not unstarted:
                raise CoordinationError('executor_unavailable','Request saved; executor is unavailable, reconcile before retrying')
        await self.coordinator.notify()
        return {'accepted':True,'execution':await self.inspect(owner,execution_id)}

    async def message(self, owner, execution_id, text, message_id, *, origin='owner', sender=None):
        self.valid_id(message_id)
        if not text.strip() or len(text) > 6000:
            raise CoordinationError('invalid_input','Worker steering requires 1–6000 characters',422)
        async with write_session(self.sessions) as db:
            row,run,room,kind = await self.owned(db,owner,execution_id)
            if origin != 'owner':
                if origin != 'agent' or row.owner_agent != sender:
                    raise CoordinationError('worker_scope','Only the admitted supervisor may steer this worker')
                text=f'Supervisor agent {sender}, within the existing assignment; not new owner authorization:\n{text}'
            if kind == 'forge':
                pass
            else:
                await self.coordinator.owned_room(db,owner,room.id,lock=True)
                await db.refresh(row)
                await db.refresh(run)
                prior = await db.scalar(select(LegionInput).where(LegionInput.execution_id == execution_id,LegionInput.input_id == message_id))
                if prior:
                    if prior.text != text:
                        raise CoordinationError('input_conflict','Message ID belongs to another instruction')
                    return {'message_id':message_id,'id':prior.id,'status':prior.status,'boundary_hash':prior.boundary_hash}
                if row.cancelled or row.status != 'running' or run.status not in {'running','waiting_tasks'}:
                    raise CoordinationError('worker_closed','Worker no longer accepts steering')
                count = await db.scalar(select(func.count()).select_from(LegionInput).where(
                    LegionInput.execution_id == execution_id,LegionInput.status.in_(['queued','delivering'])))
                if count >= 16:
                    raise CoordinationError('input_limit','Worker already has sixteen waiting instructions')
                item=LegionInput(id=uuid4().hex,execution_id=execution_id,input_id=message_id,text=text)
                db.add(item)
                await self.coordinator.event(db,room,'worker_event',sender if origin == 'agent' else 'owner',
                    {'execution_id':execution_id,'phase':'input_queued','message_id':message_id,'origin':origin,'sender_agent':sender},run.id,execution_id)
                result={'id':item.id,'message_id':message_id,'status':'queued','meaning':'Accepted; not yet included at a model boundary'}
        if kind == 'forge':
            body=Steering(input_id=message_id,message=text)
            result = (await self.coordinator.projects.steer(owner,execution_id,body) if origin == 'owner' else
                await self.coordinator.projects.steer(owner,execution_id,body,origin=origin,sender=sender))
            result['message_id']=message_id
        await self.coordinator.notify()
        return result

    async def artifact(self, owner, execution_id, key):
        if not re.fullmatch('[a-f0-9]{64}',key):
            raise CoordinationError('not_found','Event artifact unavailable',404)
        async with self.sessions() as db:
            await self.owned(db,owner,execution_id)
            row = await db.get(WorkerEventArtifact,(execution_id,key))
            if not row:
                raise CoordinationError('not_found','Event artifact unavailable',404)
            return row.data

    async def wait_run(self, run_id):
        async with self.sessions() as db:
            ids=list(await db.scalars(select(LegionExecution.id).where(LegionExecution.run_id == run_id)))
        await asyncio.gather(*(self.jobs[i] for i in ids if i in self.jobs),return_exceptions=True)

    async def stop_run(self, run_id):
        async with write_session(self.sessions) as db:
            rows=list(await db.scalars(select(LegionExecution).where(LegionExecution.run_id == run_id,
                LegionExecution.status.in_(ACTIVE))))
            ids=[row.id for row in rows]
            run=await db.get(CoordinationRun,run_id) if rows else None
            room=await self.coordinator.owned_room(db,run.user_id,run.room_id,lock=True) if run else None
            for row in rows:
                row.cancelled=True
                if row.status=='admitted':
                    row.status,row.result,row.finished_at='interrupted','Cancelled before worker dispatch',datetime.utcnow()
                    await self.coordinator.event(db,room,'worker_event',row.role,
                        {'execution_id':row.id,'phase':'finished','status':'interrupted','result':row.result},run_id,row.id)
        for execution_id in ids:
            job=self.jobs.get(execution_id)
            if job and not job.done() and not job.cancelling():job.cancel()
        await self.wait_run(run_id)


class LegionInbox:
    def __init__(self, control, context):
        self.control,self.context,self.claimed=control,context,[]

    async def claim(self):
        ctx=self.context
        await ctx.coordination.ensure_active(ctx)
        async with write_session(self.control.sessions) as db:
            await self.control.bound_context(db,ctx)
            await self.control.coordinator.owned_room(db,ctx.user_id,ctx.room_id,lock=True)
            rows=list(await db.scalars(select(LegionInput).where(LegionInput.execution_id == ctx.step_id,
                LegionInput.status == 'queued').order_by(LegionInput.created_at,LegionInput.id).limit(16)))
            for row in rows:row.status='delivering'
            self.claimed=[row.id for row in rows]
            return [row.text for row in rows]

    async def record_boundary(self,messages):
        if not self.claimed:return
        async with write_session(self.control.sessions) as db:
            ctx=self.context
            await self.control.bound_context(db,ctx)
            await self.control.coordinator.owned_room(db,ctx.user_id,ctx.room_id,lock=True)
            changed=await db.execute(update(LegionInput).where(LegionInput.id.in_(self.claimed),LegionInput.status == 'delivering')
                .where(LegionInput.execution_id == ctx.step_id)
                .values(status='delivered',boundary_hash=digest(messages)))
            if changed.rowcount != len(set(self.claimed)):
                raise CoordinationError('input_scope','Steering receipt belongs to another worker or boundary')
        await self.control.publish(self.context,{'phase':'input_delivered','input_ids':self.claimed,
            'meaning':'Included at a model boundary; compliance requires result evidence'})
        self.claimed=[]
