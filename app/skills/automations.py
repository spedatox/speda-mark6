# SPDX-License-Identifier: AGPL-3.0-or-later
"""Native adapter for the retained manage_automations contract."""
import json
from app.services.coordination_budget import digest
from app.skills.base import Skill


class AutomationsSkill(Skill):
    name='manage_automations'
    deferred=True
    memoize=False
    search_keywords='automation schedule recurring reminder briefing watch monitor n8n cron workflow'
    description=('Manage owner-authorized scheduled work and watchers through deterministic n8n templates. '
        'Use structured schedules with an explicit date for one-off work and HH:MM in the owner timezone; n8n is the sole scheduler. '
        'Use list before edits and supply expected_version; never invent a timer or raw workflow JSON. '
        'Returns actual pending, active, paused or reconciling states; reconciling effects must be inspected, never recreated.')
    input_schema={'type':'object','properties':{
        'action':{'type':'string','enum':['list','create','update','toggle','delete','test','runs','reconcile','sync']},
        'automation_id':{'type':'integer','minimum':1},'expected_version':{'type':'integer','minimum':1},
        'request_id':{'type':'string','maxLength':64},'agent_id':{'type':'string'},'active':{'type':'boolean'},
        'spec':{'type':'object','description':'name, instruction, template (briefing/task/reminder/proactive_ask/hook_keyword/hook_address/hook_mail), schedule {frequency, at, date/days/dom}; or kind with cron/url/feed_url/domain. Never workflow JSON.'}},
        'required':['action'],'additionalProperties':False}

    async def execute(self,args,context):
        service=getattr(context.coordination,'automations',None)
        if service is None: return '{"status":"unavailable"}'
        action=args['action']
        if context.task_role=='worker' or (context.triggered_by!='user' and action not in {'list','runs'}):
            return '{"status":"rejected","detail":"Automation changes require an admitted owner request."}'
        await context.coordination.ensure_active(context)
        owner=context.user_id
        identity=args.get('automation_id')
        if action=='list': result={'automations':await service.list(owner)}
        elif action=='create':
            row=await service.create(owner,{'spec':args.get('spec'), 'agent_id':args.get('agent_id',context.agent_id),
                'active':args.get('active',True),'request_id':args.get('request_id') or digest([context.request_id,context.tool_call_id])})
            result=await service.sync(owner,row['id'],context=context)
        else:
            if not identity: return '{"status":"needs_information","field":"automation_id"}'
            if action in {'update','toggle','delete'}:
                if not args.get('expected_version'): return '{"status":"needs_information","field":"expected_version","detail":"List the current definitions first."}'
                body={k:v for k,v in args.items() if k in {'spec','active','expected_version'}}
                await service.edit(owner,identity,body,remove=action=='delete')
                result=await service.sync(owner,identity,context=context)
            elif action=='test': result=await service.test(owner,identity)
            elif action=='runs': result={'runs':await service.runs(owner,identity)}
            else: result=await service.sync(owner,identity,observe_only=action=='reconcile',context=context)
        return json.dumps(result,ensure_ascii=False,default=str)
