# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from app.skills.base import Skill


class SendTelegramMessageSkill(Skill):
    name='send_telegram_message'
    deferred=True
    memoize=False
    search_keywords='telegram phone send text message notification ping'
    description=('Queue and send one owner-requested Telegram message through a durable receipt. '
        'Use for an explicit request to send a concise heads-up to the linked owner. '
        'Automatic push replies already use this transport; do not call in push or silent mode or from a worker. '
        'Returns accepted, unconfigured or unknown; unknown sends are held and must never be repeated.')
    input_schema={'type':'object','properties':{'text':{'type':'string','minLength':1,'maxLength':3800}},'required':['text'],'additionalProperties':False}
    async def execute(self,args,context):
        service=getattr(context.coordination,'deliveries',None)
        if not service: return '{"status":"unavailable"}'
        if context.task_role=='worker' or context.triggered_by!='user' or context.output_mode!='respond':
            return '{"status":"rejected","detail":"Explicit owner-facing sends only; automatic push already delivers the reply."}'
        await context.coordination.ensure_active(context)
        return json.dumps(await service.manual(context,text=args['text']),ensure_ascii=False)


class SendTelegramFileSkill(Skill):
    name='send_telegram_file'
    deferred=True
    memoize=False
    search_keywords='telegram phone send file document pdf mp3 artifact'
    description=('Send an existing artifact produced in this turn to the linked owner Telegram chat. '
        'Use after document or audio generation when the owner requested delivery to their phone. '
        'Select the exact produced filename; arbitrary filesystem paths and changed files are refused. '
        'Returns transport acceptance or an explicit held outcome, with no fallback resend.')
    input_schema={'type':'object','properties':{'filename':{'type':'string','minLength':1,'maxLength':255},
        'caption':{'type':'string','maxLength':500}},'required':['filename'],'additionalProperties':False}
    async def execute(self,args,context):
        service=getattr(context.coordination,'deliveries',None)
        if not service: return '{"status":"unavailable"}'
        if context.task_role=='worker' or context.triggered_by!='user' or context.output_mode=='silent': return '{"status":"rejected"}'
        await context.coordination.ensure_active(context)
        return json.dumps(await service.manual(context,filename=args['filename'],caption=args.get('caption','')),ensure_ascii=False)
