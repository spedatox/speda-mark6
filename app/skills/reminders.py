# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from app.skills.base import Skill


class RemindersSkill(Skill):
    name='reminders'
    deferred=True
    memoize=False
    search_keywords='reminder ask nag confirm taken done medication followup'
    description=('Open, inspect and answer persistent owner-authorized reminder questions. '
        'An ask queues one exact question with buttons; n8n alone drives bounded re-asks, without model calls. '
        'Use list and an explicit cycle_id before answering; an ambiguous yes must not close an arbitrary question. '
        'Returns queued, already_open, delivery_unknown or an attributed answer; uncertain sends are held.')
    input_schema={'type':'object','properties':{'action':{'type':'string','enum':['ask','list','answer','history']},
        'reminder_id':{'type':'string','maxLength':64},'text':{'type':'string','minLength':1,'maxLength':2000},
        'options':{'type':'array','items':{'type':'string','minLength':1,'maxLength':24},'maxItems':6},
        'every_minutes':{'type':'integer','minimum':1,'maximum':1440},'max_asks':{'type':'integer','minimum':1,'maximum':200},
        'cycle_id':{'type':'integer','minimum':1},'expected_version':{'type':'integer','minimum':1},
        'answer':{'type':'string','minLength':1,'maxLength':64}},'required':['action'],'additionalProperties':False}

    async def execute(self,args,context):
        service=getattr(context.coordination,'reminders',None)
        if not service or context.task_role=='worker': return '{"status":"unavailable"}'
        await context.coordination.ensure_active(context)
        action=args['action']; owner=context.user_id
        if action in {'list','history'}: return json.dumps({action:await service.list(owner,context.agent_id,history=action=='history',reminder_id=args.get('reminder_id',''))},ensure_ascii=False)
        if action=='answer':
            if context.triggered_by!='user': return '{"status":"rejected","detail":"An answer requires the owner or a bound owner button."}'
            if not args.get('cycle_id') or not args.get('expected_version') or not args.get('answer'):
                return '{"status":"needs_information","detail":"List questions and select cycle_id, expected_version and an offered answer."}'
            result=await service.answer(owner,args['cycle_id'],args['answer'],'owner_chat:'+context.request_id,expected_version=args['expected_version'])
        else:
            identity=args.get('reminder_id')
            if not identity or not args.get('text'): return '{"status":"needs_information","detail":"reminder_id and text are required."}'
            if context.triggered_by!='user':
                manager=getattr(context.coordination,'automations',None)
                from app.models.automation import Automation
                from sqlalchemy import select
                async with service.sessions() as db:
                    row=await db.scalar(select(Automation).where(Automation.user_id==owner,Automation.public_key==context.trigger_payload.get('automation_key','')))
                if not row or row.deleted_at or row.status!='active' or not row.desired_active or row.content_version!=context.trigger_payload.get('automation_revision') or row.spec.get('template')!='proactive_ask' or row.spec.get('reminder_id')!=identity or row.agent_id!=context.agent_id:
                    return '{"status":"rejected","detail":"This event has no stored authorization for that persistent question."}'
                raw={k:row.spec[k] for k in ('options','every_minutes','max_asks') if k in row.spec}
            else: raw={k:v for k,v in args.items() if k in {'options','every_minutes','max_asks'}}
            result=await service.ask(owner,context.agent_id,identity,{'text':args['text'],**raw})
            await service.deliveries.drain(owner)
        return json.dumps(result,ensure_ascii=False)
