"""Durable, non-replaying transport effects. n8n drains pending work."""
import json
import hashlib
import asyncio
import time
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from sqlalchemy import select,update
from app.database import write_session
from app.models.delivery import Delivery,TelegramLink
from app.models.academic import Device
from app.services.coordination_budget import digest,insert_for,CoordinationError
from app.services.remote_tools import request_bytes
from app.services.speech_text import MAX_CHARS
from app.schemas.llm import ProviderError
from app.schemas.voice import SpeakRequest


def chunks(text,limit=3800):
    # UTF-16 units are the conservative Telegram length boundary.
    result=[]; chunk=''; size=0
    for character in text:
        width=2 if ord(character)>0xffff else 1
        if size+width>limit: result.append(chunk); chunk=''; size=0
        chunk+=character; size+=width
    if chunk: result.append(chunk)
    return result


class Deliveries:
    def __init__(self,sessions,config,profiles,*,voice=None):
        self.sessions,self.config,self.profiles=sessions,config,profiles
        self.voice=voice

    def token(self,agent):
        try: configured=json.loads(self.config.telegram_bot_tokens)
        except (ValueError,TypeError): configured={}
        value=configured.get(agent) if isinstance(configured,dict) else None
        if not value and agent=='speda': value=self.config.telegram_bot_token
        return value if isinstance(value,str) else ''

    async def telegram(self,agent,method,payload,*,files=None):
        token=self.token(agent)
        if not token: raise ProviderError('telegram_unconfigured','No Telegram bot is configured for this agent.')
        kwargs={'data':{k:str(v) for k,v in payload.items()},'files':files} if files else {'json':payload}
        status,body=await request_bytes('POST','https://api.telegram.org/bot'+token+'/'+method,
            maximum=64000,timeout=30 if files else 12,**kwargs)
        if status == 429:
            raise ProviderError('telegram_throttled', 'Telegram rejected this request due to rate limiting.')
        if method == 'sendVoice' and status in (400, 403):
            raise ProviderError('telegram_voice_rejected', 'Telegram rejected the voice message.')
        try: data=json.loads(body)
        except ValueError: raise ProviderError('delivery_unknown','Telegram returned an unverifiable response.')
        if status>=300 or not isinstance(data,dict) or not data.get('ok'):
            # No secret-bearing response/error body is retained or logged.
            raise ProviderError('delivery_unknown','Telegram did not confirm the requested operation.')
        return data.get('result')

    async def add(self,db,owner,agent,key,channel,payload,run_id=None):
        identity=uuid4().hex
        await db.execute(insert_for(db,Delivery).values(id=identity,user_id=owner,agent_id=agent,
            run_id=run_id,dedupe_key=digest(key),channel=channel,payload=payload,status='pending',
            created_at=datetime.utcnow(),updated_at=datetime.utcnow()).on_conflict_do_nothing(index_elements=['user_id','dedupe_key']))

    async def enqueue_run(self,db,run,room,step,text):
        if run.policy.get('output_mode')!='push' or step.mode=='consult' or not text.strip(): return
        link=await db.get(TelegramLink,(run.user_id,step.agent_id))
        if not link or not link.chat_id:
            # Explicit fallback only when this agent has no linked bot.
            link=await db.get(TelegramLink,(run.user_id,'speda'))
        if link and link.chat_id and self.token(link.agent_id):
            trigger=run.policy.get('trigger_payload') or {}
            speak=trigger.get('voice') is True
            prefix=f'[{step.agent_id}] ' if link.agent_id!=step.agent_id else ''
            limit=MAX_CHARS-len(prefix) if speak else 3800
            for index,chunk in enumerate(chunks(text,limit)):
                payload={'bot_agent':link.agent_id,'chat_id':link.chat_id,
                         'link_version':link.version,'text':prefix+chunk}
                if speak:
                    payload.update(voice=True,locale=trigger.get('language'))
                await self.add(db,run.user_id,step.agent_id,[run.id,step.id,'telegram',index],
                    'telegram',payload,run.id)
        elif run.user_id==1 and self.config.fcm_credentials_file:
            devices=list(await db.scalars(select(Device).where(Device.active.is_(True)).order_by(Device.id).limit(20)))
            for device in devices:
                await self.add(db,run.user_id,step.agent_id,[run.id,step.id,'fcm',device.id],'fcm',
                    {'device_id':device.id,'fid':device.fid,'token_hash':digest(device.token),
                     'title':step.agent_id,'body':text[:700]},run.id)
        else:
            await self.add(db,run.user_id,step.agent_id,[run.id,step.id,'unconfigured'],'unconfigured',
                {'detail':'No linked Telegram bot or configured device transport.'},run.id)

    async def manual(self,context,*,text=None,filename=None,caption=''):
        from app.models.user import User
        owner=context.user_id
        async with self.sessions() as db,db.begin():
            await db.execute(update(User).where(User.id==owner).values(name=User.name))
            held=await db.scalar(select(Delivery.id).where(Delivery.user_id==owner,Delivery.run_id==context.run_id,
                Delivery.status.in_(['sending','unknown'])).limit(1))
            if held: raise CoordinationError('delivery_held','This run already has an unconfirmed send; inspect it before sending another message')
            link=await db.get(TelegramLink,(owner,context.agent_id))
            if not link or not link.chat_id: link=await db.get(TelegramLink,(owner,'speda'))
            if not link or not link.chat_id or not self.token(link.agent_id): return {'status':'unconfigured'}
            payload={'bot_agent':link.agent_id,'chat_id':link.chat_id,'link_version':link.version}
            if context.run_id: payload.update(manual_step=context.step_id,executor=context.coordination.identity)
            if filename:
                card=next((f for f in context.extra.get('produced_files',[]) if f['name']==filename),None)
                path=(Path(self.config.temp_outputs_dir)/filename).resolve()
                if not card or not path.is_relative_to(Path(self.config.temp_outputs_dir).resolve()) or not path.is_file() or path.stat().st_size>10*1024*1024:
                    raise CoordinationError('telegram_file','Select a file produced in this turn, up to 10 MiB',422)
                content=await asyncio.to_thread(path.read_bytes)
                sha=hashlib.sha256(content).hexdigest()
                payload.update(file_path=str(path),file_hash=sha,file_name=filename,caption=caption)
                key=[context.run_id or context.request_id,'manual_file',sha]
            else:
                if not text or len(text.encode('utf-16-le'))>7600: raise CoordinationError('telegram_text','Use one concise message up to 3800 UTF-16 units',422)
                payload['text']=text; key=[context.run_id or context.request_id,'manual_text',text]
            await self.add(db,owner,context.agent_id,key,'telegram',payload,context.run_id)
            row=await db.scalar(select(Delivery).where(Delivery.user_id==owner,Delivery.dedupe_key==digest(key)))
            identity=row.id
        return await self.send(owner,identity)

    async def drain(self,owner,limit=8):
        async with self.sessions() as db:
            ids=list(await db.scalars(select(Delivery.id).where(Delivery.user_id==owner,
                Delivery.status=='pending').order_by(Delivery.created_at,Delivery.id).limit(max(1,min(limit,8)))))
        for identity in ids: await self.send(owner,identity)
        return {'processed':len(ids)}

    async def telegram_link_matches(self, db, owner, payload):
        link = await db.get(TelegramLink, (owner, payload['bot_agent']))
        return link and link.chat_id == payload['chat_id'] and link.version == payload['link_version']

    async def send_telegram_voice(self, owner, identity, agent, payload):
        fallback = 'Speech synthesis unavailable. Sent the reply as text.'
        audio = None
        try:
            if self.voice is None:
                raise CoordinationError('voice_unconfigured', 'Speech service is unavailable')
            speech = await self.voice.speak(owner, SpeakRequest(
                text=payload['text'], agent_id=agent, locale=payload.get('locale'),
                request_id=f'delivery-{identity}',
            ), cache_scope=identity)
            audio = await asyncio.to_thread(Path(speech['path']).read_bytes)
        except (CoordinationError, ProviderError, ValueError, OSError):
            # Synthesis failure cannot have sent a Telegram message.
            pass

        bot = payload['bot_agent']
        wire = {'chat_id': payload['chat_id']}
        changed_link = (None, 'cancelled', 'The Telegram link changed before delivery.')
        if audio:
            # Synthesis can take long enough for the owner to change the link.
            async with self.sessions() as db:
                if not await self.telegram_link_matches(db, owner, payload):
                    return changed_link
            caption = chunks(speech['spoken_text'], 1024)[0]
            try:
                result = await self.telegram(bot, 'sendVoice', {**wire, 'caption': caption},
                    files={'voice': ('automation-reply.mp3', audio, 'audio/mpeg')})
                return result, 'accepted', 'Telegram voice message accepted.'
            except ProviderError as exc:
                if exc.code != 'telegram_voice_rejected':
                    raise
                fallback = 'Telegram rejected the voice message. Sent the reply as text.'
        async with self.sessions() as db:
            if not await self.telegram_link_matches(db, owner, payload):
                return changed_link
        result = await self.telegram(bot, 'sendMessage', {**wire, 'text': payload['text']})
        return result, 'accepted', fallback

    async def send(self,owner,identity):
        async with write_session(self.sessions) as db:
            await db.execute(update(Delivery).where(Delivery.id==identity,Delivery.user_id==owner).values(updated_at=Delivery.updated_at))
            row=await db.get(Delivery,identity)
            if not row or row.user_id!=owner: raise CoordinationError('not_found','Delivery unavailable',404)
            if row.status!='pending': return self.view(row)
            if row.channel=='unconfigured': row.status='unconfigured'; return self.view(row)
            payload=dict(row.payload)
            if payload.get('reminder_cycle_id'):
                from app.models.reminder import ReminderCycle
                cycle=await db.get(ReminderCycle,payload['reminder_cycle_id'])
                if not cycle or cycle.user_id!=owner or cycle.status!='open' or cycle.version!=payload['reminder_version']:
                    row.status='cancelled'; row.detail='The reminder was answered or superseded before delivery.'; return self.view(row)
            if row.channel=='telegram':
                if not await self.telegram_link_matches(db,owner,payload):
                    row.status='cancelled'; row.detail='The Telegram link changed before delivery.'; return self.view(row)
                if not self.token(payload['bot_agent']): row.status='unconfigured'; return self.view(row)
            elif row.channel=='fcm':
                device=await db.get(Device,payload['device_id'])
                if not device or not device.active or device.fid!=payload['fid'] or digest(device.token)!=payload['token_hash']:
                    row.status='cancelled'; return self.view(row)
                fid,token=device.fid,device.token
            claimed=await db.execute(update(Delivery).where(Delivery.id==identity,Delivery.status=='pending').values(status='sending',updated_at=datetime.utcnow()))
            if claimed.rowcount!=1: return {'status':'busy'}
            channel=row.channel
            agent=row.agent_id
            if payload.get('manual_step'):
                from app.models.coordination import CoordinationRun,AgentStep
                run=await db.get(CoordinationRun,row.run_id)
                step=await db.get(AgentStep,payload['manual_step'])
                if not run or run.user_id!=owner or run.cancelled or run.executor!=payload['executor'] or run.status!='running' or run.deadline<=time.time() or run.lease_until<=time.time() or not step or step.run_id!=run.id or step.status!='running':
                    row.status='cancelled'; row.detail='The originating owner run stopped before dispatch.'; return self.view(row)
        detail=None
        try:
            if channel=='telegram':
                status='accepted'
                if payload.get('file_path'):
                    path=Path(payload['file_path']).resolve()
                    if not path.is_relative_to(Path(self.config.temp_outputs_dir).resolve()) or not path.is_file() or path.stat().st_size>10*1024*1024:
                        raise CoordinationError('telegram_file','Queued file is unavailable or outside the outputs folder')
                    content=await asyncio.to_thread(path.read_bytes)
                    if hashlib.sha256(content).hexdigest()!=payload['file_hash']:
                        raise CoordinationError('telegram_file','Queued file changed before upload')
                    result=await self.telegram(payload['bot_agent'],'sendDocument',{'chat_id':payload['chat_id'],'caption':payload.get('caption','')},
                        files={'document':(payload['file_name'],content,'application/octet-stream')})
                elif payload.get('voice') is True:
                    result,status,detail=await self.send_telegram_voice(owner,identity,agent,payload)
                else:
                    wire={'chat_id':payload['chat_id'],'text':payload['text']}
                    if payload.get('reply_markup'): wire['reply_markup']=payload['reply_markup']
                    result=await self.telegram(payload['bot_agent'],'sendMessage',wire)
                remote_id=None
                if status=='accepted':
                    if not isinstance(result,dict) or type(result.get('message_id')) is not int:
                        raise ProviderError('delivery_unknown','Telegram send was not confirmed.')
                    remote_id=str(result['message_id'])
            else:
                from app.services.fcm import send_data_message
                ok,detail=await send_data_message(fid=fid,token=token,
                    data={'type':'notification','title':payload['title'],'body':payload['body'],'delivery_id':identity},priority='normal')
                status='accepted' if ok else 'rejected' if detail=='unregistered' else 'unknown'
                remote_id=None
        except BaseException as exc:
            # Cancellation after claim is also uncertain; no reset to pending.
            async def hold():
                async with write_session(self.sessions) as db:
                    await db.execute(update(Delivery).where(Delivery.id==identity,Delivery.status=='sending').values(
                        status='unknown',detail='Transport acknowledgement unavailable. Inspect before any new send.',updated_at=datetime.utcnow()))
            await asyncio.shield(hold())
            if not isinstance(exc,Exception): raise
        else:
            async with write_session(self.sessions) as db:
                await db.execute(update(Delivery).where(Delivery.id==identity,Delivery.status=='sending').values(
                    status=status,remote_id=remote_id,detail=detail,updated_at=datetime.utcnow()))
        async with self.sessions() as db: return self.view(await db.get(Delivery,identity))

    @staticmethod
    def view(row):
        return {'id':row.id,'run_id':row.run_id,'agent_id':row.agent_id,'channel':row.channel,
            'status':row.status,'remote_id':row.remote_id,'detail':row.detail}

    async def list(self,owner,run_id=None):
        async with self.sessions() as db:
            query=select(Delivery).where(Delivery.user_id==owner)
            if run_id: query=query.where(Delivery.run_id==run_id)
            rows=await db.scalars(query.order_by(Delivery.created_at.desc()).limit(100))
            return [self.view(row) for row in rows]
