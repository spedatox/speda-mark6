"""Owner-bound Telegram input over Coordinator; no independent turn engine."""
import hmac
import re
import secrets
import time
from uuid import uuid4,uuid5,NAMESPACE_URL
from sqlalchemy import select,update
from app.models.delivery import TelegramOwner,TelegramLink,TelegramInput
from app.models.user import User
from app.models.coordination import Room,CoordinationRun
from app.models.session import Session
from app.schemas.coordination import RoomInput
from app.services.coordination_budget import CoordinationError,digest


class TelegramGateway:
    def __init__(self,coordinator,deliveries,config):
        self.coordinator,self.sessions,self.deliveries,self.config=coordinator,coordinator.sessions,deliveries,config

    def agent(self,agent):
        aid=self.coordinator.profiles.canonical(agent)
        if not self.deliveries.token(aid): raise CoordinationError('telegram_unconfigured','No bot is configured for this agent',503)
        return aid

    async def link(self,owner,agent):
        aid=self.agent(agent)
        bot=await self.deliveries.telegram(aid,'getMe',{})
        username=bot.get('username') if isinstance(bot,dict) else None
        if not username or not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username):
            raise CoordinationError('telegram_identity','Telegram bot username could not be verified',503)
        nonce=secrets.token_urlsafe(24)
        async with self.sessions() as db,db.begin():
            await db.execute(update(User).where(User.id==owner).values(name=User.name))
            row=await db.get(TelegramLink,(owner,aid))
            if not row: row=TelegramLink(user_id=owner,agent_id=aid); db.add(row)
            row.nonce_hash=digest(nonce); row.nonce_expires=time.time()+600
            linked=bool(row.chat_id)
        return {'agent_id':aid,'link':'https://t.me/'+username+'?start='+nonce,'linked':linked,'expires_in':600}

    async def status(self,owner):
        async with self.sessions() as db:
            links={r.agent_id:r for r in await db.scalars(select(TelegramLink).where(TelegramLink.user_id==owner))}
            principal=await db.get(TelegramOwner,owner)
        agents=[p['agent_id'] for p in self.coordinator.profiles.roster() if self.deliveries.token(p['agent_id'])]
        return {'configured':bool(agents),'mode':'webhook','owner_linked':bool(principal),
            'bots':[{'agent_id':aid,'linked':aid in links and bool(links[aid].chat_id)} for aid in agents]}

    async def disconnect(self,owner,agent):
        aid=self.coordinator.profiles.canonical(agent)
        async with self.sessions() as db,db.begin():
            await db.execute(update(TelegramLink).where(TelegramLink.user_id==owner,TelegramLink.agent_id==aid).values(
                chat_id=None,nonce_hash=None,nonce_expires=0,version=TelegramLink.version+1))
        return {'agent_id':aid,'linked':False}

    async def receive(self,owner,agent,event):
        aid=self.agent(agent)
        if event.get('callback_query'): return await self.callback(owner,aid,event)
        update_id=event.get('update_id')
        message=event.get('message')
        if type(update_id) is not int or update_id<0 or not isinstance(message,dict):
            raise CoordinationError('telegram_update','A new text message update is required',422)
        sender=message.get('from',{}).get('id'); chat=message.get('chat',{})
        if type(sender) is not int or chat.get('type')!='private' or chat.get('id')!=sender or message.get('from',{}).get('is_bot'):
            raise CoordinationError('telegram_scope','Only the linked owner in a private chat is accepted',403)
        if message.get('forward_origin') or message.get('sender_chat'):
            return {'accepted':False,'status':'forwarded_material','detail':'Send an owner instruction with any quoted material separately.'}
        text=message.get('text','')
        if not isinstance(text,str) or not text.strip() or len(text)>8000:
            return {'accepted':False,'status':'unsupported_message','detail':'Send text up to 8000 characters.'}
        paired=False
        identity=uuid5(NAMESPACE_URL,f'speda:telegram:{aid}:{update_id}').hex
        async with self.sessions() as db,db.begin():
            await db.execute(update(User).where(User.id==owner).values(name=User.name))
            principal=await db.get(TelegramOwner,owner)
            configured=self.config.telegram_owner_id
            if (principal and principal.sender_id!=sender) or (configured and configured!=sender):
                raise CoordinationError('telegram_owner','Sender is not the configured owner',403)
            link=await db.get(TelegramLink,(owner,aid))
            start=re.fullmatch(r'/start(?:@[A-Za-z0-9_]+)?\s+([A-Za-z0-9_-]{20,64})',text.strip())
            if start:
                if not link or not link.nonce_hash or link.nonce_expires<time.time() or not hmac.compare_digest(link.nonce_hash,digest(start.group(1))):
                    raise CoordinationError('telegram_nonce','Use a fresh pairing link from authenticated Settings',403)
                if not principal: db.add(TelegramOwner(user_id=owner,sender_id=sender))
                link.chat_id=sender; link.version+=1; link.nonce_hash=None; link.nonce_expires=0
                paired=True
            else:
                if not principal or not link or link.chat_id!=sender:
                    raise CoordinationError('telegram_unlinked','Pair this bot from authenticated Settings first',403)
                from app.core.runtime_state import get_telegram_cutover_offset
                if update_id < get_telegram_cutover_offset(aid):
                    return {'accepted':True,'status':'historical_update_retained','duplicate':True}
                prior=await db.get(TelegramInput,(aid,update_id))
                fingerprint=digest({'sender':sender,'chat':chat['id'],'text':text})
                if prior:
                    if prior.user_id!=owner or prior.request_hash!=fingerprint:
                        raise CoordinationError('input_conflict','This Telegram update identity belongs to different input')
                    room_id=prior.room_id
                    model=prior.model
                    saved=await db.scalar(select(CoordinationRun).where(CoordinationRun.user_id==owner,
                        CoordinationRun.room_id==prior.room_id,CoordinationRun.input_id==identity))
                    if saved:
                        prior.run_id=saved.id
                        return {'accepted':True,'run_id':saved.id,'status':saved.status,'duplicate':True}
                else:
                    if not link.room_id:
                        session=Session(user_id=owner,agent_id=aid,triggered_by='user',model_used='unassigned',title='Telegram · '+aid)
                        db.add(session); await db.flush()
                        room=Room(id=uuid4().hex,user_id=owner,session_id=session.id,title=session.title,lead=aid,members=[aid])
                        db.add(room); await db.flush(); link.room_id=room.id
                    room_id=link.room_id
                    _,document=await self.coordinator.policy.snapshot(owner)
                    model=document.telegram_models.get(aid)
                    db.add(TelegramInput(agent_id=aid,update_id=update_id,user_id=owner,request_hash=fingerprint,room_id=room_id,text=text,model=model))
        if paired: return {'accepted':True,'status':'linked','agent_id':aid}
        result=await self.coordinator.start(owner,room_id,RoomInput(input_id=identity,message=text,agent_id=aid,model=model),
            origin='user',output_mode='push',trigger_payload={'channel':'telegram','agent_id':aid,'update_id':update_id},client_context={'platform':'telegram'})
        async with self.sessions() as db,db.begin():
            await db.execute(update(TelegramInput).where(TelegramInput.agent_id==aid,TelegramInput.update_id==update_id,
                TelegramInput.user_id==owner).values(run_id=result['run_id']))
        return {'accepted':True,**result}

    async def callback(self,owner,agent,event):
        callback=event.get('callback_query',{})
        message=callback.get('message',{})
        sender=callback.get('from',{}).get('id'); chat=message.get('chat',{})
        async with self.sessions() as db: principal=await db.get(TelegramOwner,owner)
        if not principal or principal.sender_id!=sender or chat.get('id')!=sender or chat.get('type')!='private':
            raise CoordinationError('telegram_owner','Callback does not belong to the linked owner',403)
        match=re.fullmatch(r'rm:(\d+):(\d+):(\d+)',callback.get('data',''))
        reminders=getattr(self.coordinator,'reminders',None)
        if not match or not reminders: raise CoordinationError('telegram_callback','Unsupported question callback',422)
        cycle,version,index=map(int,match.groups())
        result=await reminders.answer(owner,cycle,'','telegram',expected_version=version,
            callback=(agent,sender,message.get('message_id'),index))
        return {'accepted':True,**result}

    async def drain(self,owner,limit=8):
        async with self.sessions() as db:
            rows=list(await db.scalars(select(TelegramInput).where(TelegramInput.user_id==owner,
                TelegramInput.run_id.is_(None)).limit(max(1,min(limit,8)))))
            principal=await db.get(TelegramOwner,owner)
        if not principal: return {'inputs':[]}
        results=[]
        for row in rows:
            try:
                results.append(await self.receive(owner,row.agent_id,{'update_id':row.update_id,'message':{
                    'from':{'id':principal.sender_id},'chat':{'id':principal.sender_id,'type':'private'},'text':row.text}}))
            except CoordinationError as exc: results.append({'accepted':False,'status':exc.code})
        return {'inputs':results}
