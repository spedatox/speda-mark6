# SPDX-License-Identifier: AGPL-3.0-or-later
"""Native adapter for the retained Onyx ticket contract."""
import json
import re
from urllib.parse import urlsplit
from sqlalchemy import select
from app.config import settings
from app.skills.base import Skill
from app.schemas.llm import ProviderError
from app.models.coordination import RoomEvent
from app.services.remote_tools import request_bytes


class OnyxSkill(Skill):
    name='onyx'
    read_only=False
    memoize=False
    requires_network=True
    deferred=True
    restricted_to=frozenset({'speda','ultron','optimus'})
    search_keywords='onyx ticket client work support issue task progress commercial deliverable'
    description=(
        'Inspect and update the owner\'s Onyx client tickets, progress and discussions. '
        'Use list/get to inspect actual records before changing a ticket; this is commercial work tracking, not course memory. '
        'Use mutations only for the assigned owner request. Deletion requires a direct owner instruction naming that ticket in this admitted turn. '
        'Returns bounded remote records or an acknowledged change. A transport interruption is an unknown outcome and must not be retried as another action.')
    input_schema={'type':'object','properties':{
        'action':{'type':'string','enum':['list','get','update_status','add_comment','complete','delete']},
        'ticket_number':{'type':'integer','minimum':1},
        'status':{'type':'string','enum':['OPEN','IN_PROGRESS','WAITING','COMPLETED','CLOSED','CANCELLED']},
        'content':{'type':'string','minLength':1,'maxLength':6000},
        'completion_summary':{'type':'string','minLength':1,'maxLength':4000},
        'completion_url':{'type':'string','maxLength':2000}},'required':['action'],'additionalProperties':False}

    async def execute(self,args,context):
        if context.agent_id not in self.restricted_to or context.task_role=='worker':
            return '{"status":"rejected","detail":"Onyx is not authorized for this worker."}'
        if not settings.onyx_api_url or not settings.onyx_api_token:
            return '{"status":"unconfigured"}'
        parsed=urlsplit(settings.onyx_api_url)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return '{"status":"unconfigured","detail":"Use a configured service URL without credentials or query parameters."}'
        action=args['action']
        if action!='list' and not args.get('ticket_number'):
            return '{"status":"rejected","detail":"A ticket number is required."}'
        required={'update_status':'status','add_comment':'content','complete':'completion_summary'}
        if action in required and not args.get(required[action]):
            return json.dumps({'status':'rejected','detail':required[action]+' is required.'})
        if action=='delete':
            if not context.coordination or context.triggered_by!='user':
                return '{"status":"rejected","detail":"Direct owner deletion authorization is required."}'
            async with context.coordination.sessions() as db:
                event=await db.scalar(select(RoomEvent).where(RoomEvent.run_id==context.run_id,
                    RoomEvent.room_id==context.room_id,RoomEvent.kind=='user_message').order_by(RoomEvent.sequence).limit(1))
            text=event.payload['text'] if event else ''
            number=str(args['ticket_number'])
            authorized = re.fullmatch(r'(?:please\s+)?(?:delete|remove)\s+(?:onyx\s+)?ticket\s*#?\s*'+number+r'(?:\s+permanently)?[.!]?',text.strip(),re.I)
            authorized = authorized or re.fullmatch(r'(?:onyx\s+)?'+number+r'\s+numaralı\s+(?:talebi|bileti)\s+sil[.!]?',text.strip(),re.I)
            if not authorized:
                return '{"status":"rejected","detail":"The current owner input must explicitly request deletion of this ticket."}'
        if context.coordination: await context.coordination.ensure_active(context)
        endpoint=settings.onyx_api_url.rstrip('/')+'/api/v1/speda'
        headers={'Authorization':'Bearer '+settings.onyx_api_token,'Accept':'application/json'}
        if action in {'list','get'}:
            status,body=await request_bytes('GET',endpoint,headers=headers,
                params={k:v for k,v in args.items() if k in {'status','ticket_number'}},maximum=64000)
        else:
            headers['Idempotency-Key']=f'{context.user_id}:{context.request_id}:{context.tool_call_id}'
            payload={**args,'action':{'complete':'complete_ticket','delete':'delete_ticket'}.get(action,action)}
            status,body=await request_bytes('POST',endpoint,headers=headers,json=payload,maximum=16000)
        if status>=300: return json.dumps({'status':'rejected','http_status':status,'detail':'Onyx refused the request; no response body or credentials disclosed.'})
        try: data=json.loads(body) if body else None
        except ValueError as exc: raise ProviderError('remote_effect_unknown','Onyx response was not a verifiable acknowledgement.') from exc
        if action not in {'list','get'} and isinstance(data,dict) and (data.get('success') is False or data.get('error')):
            raise ProviderError('remote_effect_unknown','Onyx reported an unconfirmed mutation outcome; inspect the ticket before another action.')
        if action in {'list','get'}:
            if isinstance(data,dict) and isinstance(data.get('tickets'),list) and len(data['tickets'])>30:
                data={**data,'tickets':data['tickets'][:30],'partial':True}
            encoded=json.dumps({'status':'success','data':data,'authority':'Onyx tool data, not owner assertions'},ensure_ascii=False)
            if len(encoded.encode())>24000:
                return '{"status":"needs_narrower_query","detail":"Inspect one ticket or filter by status."}'
            return encoded.replace(settings.onyx_api_token,'[redacted]')
        return json.dumps({'status':'accepted' if status==202 else 'acknowledged','action':action,
            'ticket_number':args['ticket_number'],'http_status':status,'data':data},ensure_ascii=False).replace(settings.onyx_api_token,'[redacted]')
