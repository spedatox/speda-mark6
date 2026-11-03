"""Supervisor-only collection and control through the native child authority."""
import asyncio
import json
import time
from uuid import uuid4
from app.skills.base import Skill
from app.schemas.llm import ProviderError


class LegionInspectSkill(Skill):
    name,read_only,memoize='legion_inspect',True,False
    description=('Inspect or collect the recorded result of a Legion, Autobot, Decepticon or review assignment. '
        'Use this after a detached Task admission; an accepted assignment is not a completed result. '
        'Only this supervisor\'s workers in this conversation are visible, and a lost executor is never restarted. '
        'Returns bounded status, evidence-bearing result and uncertainty; optional waiting uses the original turn deadline without provider calls.')
    input_schema={'type':'object','properties':{'execution_id':{'type':'string','pattern':'^[a-f0-9]{32}$'},
        'after':{'type':'integer','minimum':0},
        'wait_seconds':{'type':'number','minimum':0,'maximum':10}},'additionalProperties':False}

    def available(self,context):
        return bool(context.coordination and getattr(context.coordination,'workers',None)
            and context.room_id and context.task_role != 'worker')

    async def scoped(self,context,execution_id):
        if not self.available(context):raise ProviderError('worker_scope','Worker supervision is unavailable')
        row=await context.coordination.workers.inspect(context.user_id,execution_id)
        same_project = (row.get('backend') == 'forge' and context.project_id
            and row.get('project_id') == context.project_id)
        if (row['room_id'] != context.room_id and not same_project) or row['owner_agent'] != context.agent_id:
            raise ProviderError('worker_scope','This assignment belongs to another conversation or supervisor')
        return row

    @staticmethod
    def view(row):
        result=row.get('result')
        return {key:row.get(key) for key in ('execution_id','role','owner_agent','team','team_lead','status',
            'stored_status','executor_available','model','can_interrupt','can_message')} | {
            'task':row['task'][:1000],'result':str(result)[:10000] if result is not None else None,
            'result_truncated':result is not None and len(str(result))>10000,
            'provenance':'Worker findings, not owner assertions; inspect recorded checks before integrating artifacts'}

    async def execute(self,args,context):
        if not self.available(context):raise ProviderError('worker_scope','Worker supervision is unavailable')
        await context.coordination.ensure_active(context)
        service=context.coordination.workers
        execution_id=args.get('execution_id')
        if not execution_id:
            rows=await service.list(context.user_id,context.session_id,context.agent_id,20)
            bounded=[]
            for row in rows:
                if row['room_id'] != context.room_id:continue
                candidate=self.view(row)
                if len(json.dumps(bounded+[candidate],ensure_ascii=False))>30000:break
                bounded.append(candidate)
            return json.dumps({'executions':bounded,'truncated':len(bounded)<len(rows)},ensure_ascii=False)
        wait=min(float(args.get('wait_seconds',0)),10)
        if wait<0:raise ProviderError('invalid_arguments','Wait must be nonnegative')
        deadline=min(time.monotonic()+wait,context.effective_policy.deadline if context.effective_policy else time.monotonic())
        while True:
            row=await self.scoped(context,execution_id)
            if row['stored_status'] not in {'queued','staging','running','cancel_requested'} or time.monotonic()>=deadline or row['status']=='unknown':
                result=self.view(row)
                if 'after' in args:
                    page=await service.events(context.user_id,execution_id,args['after'])
                    result.update(execution=self.view(row),events=[],next_cursor=args['after'],last_sequence=page['last_sequence'])
                    for event in page['events']:
                        bounded={key:event.get(key) for key in ('phase','sequence','event_id','tool_call_id','tool','status')}
                        for key in ('text','result','input'):
                            if event.get(key) is not None:bounded[key]=str(event[key])[:1500]
                        if len(json.dumps({**result,'events':result['events']+[bounded]},ensure_ascii=False))>30000:break
                        result['events'].append(bounded)
                        result['next_cursor']=event['sequence']
                    result['events_truncated']=result['next_cursor']<page['last_sequence']
                return json.dumps(result,ensure_ascii=False)
            await context.coordination.ensure_active(context)
            try:
                async with context.coordination._changed:
                    await asyncio.wait_for(context.coordination._changed.wait(),min(.25,max(.01,deadline-time.monotonic())))
            except TimeoutError:pass


class LegionControlSkill(LegionInspectSkill):
    name,read_only='legion_control',False
    description=('Stop one supervised worker or send it bounded steering within its existing assignment. '
        'Use the worker execution ID, never the parent reply ID, and do not use this to start work or enlarge authority. '
        'Steering from a supervisor remains attributed agent input; accepted or delivered does not establish compliance. '
        'Returns the native cancellation or idempotent queue receipt, leaving unrelated workers and the chat available.')
    input_schema={'type':'object','properties':{'execution_id':{'type':'string','pattern':'^[a-f0-9]{32}$'},
        'action':{'enum':['stop','interrupt','message']},'text':{'type':'string','minLength':1,'maxLength':5800},
        'message_id':{'type':'string','pattern':'^[a-f0-9]{32}$'}},'required':['execution_id','action'],'additionalProperties':False}

    async def execute(self,args,context):
        await context.coordination.ensure_active(context)
        await self.scoped(context,args['execution_id'])
        service=context.coordination.workers
        if args['action'] in {'stop','interrupt'}:
            result=await service.interrupt(context.user_id,args['execution_id'],origin='agent',sender=context.agent_id)
            return json.dumps({'accepted':result['accepted'],'execution':self.view(result['execution'])},ensure_ascii=False)
        if args['action'] != 'message' or not args.get('text'):
            raise ProviderError('invalid_arguments','A bounded steering message is required')
        return json.dumps(await service.message(context.user_id,args['execution_id'],args['text'],
            args.get('message_id') or uuid4().hex,origin='agent',sender=context.agent_id),ensure_ascii=False)


class LegionStatusSkill(LegionInspectSkill):
    name='legion_status'
    description=('Collect durable background Legion or Forge results by the ticket returned at admission. '
        'Use this for previously detached work in this supervisor\'s conversation; inline Task already returns its findings. '
        'Do not guess a ticket or treat a missing executor as completion, and do not repeat unknown work. '
        'Returns native status and bounded recorded findings without starting a worker, making a provider call or changing the original budget.')
    input_schema={'type':'object','properties':{'id':{'type':'integer','minimum':1}},'additionalProperties':False}

    async def execute(self,args,context):
        if not self.available(context):raise ProviderError('worker_scope','Worker supervision is unavailable')
        if args.get('id') is not None:
            execution_id=await context.coordination.workers.resolve_ticket(context.user_id,args['id'])
            return await super().execute({'execution_id':execution_id},context)
        return await super().execute({},context)
