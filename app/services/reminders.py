"""Zero-model reminder cycles. n8n is the only source of recurring ticks."""
import copy
import re
import time
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select,update
from app.models.reminder import ReminderDefinition,ReminderCycle
from app.models.delivery import Delivery,TelegramLink
from app.models.user import User
from app.services.coordination_budget import CoordinationError
from app.schemas.reminder import ReminderDefinitionIn


class Reminders:
    def __init__(self,coordinator,deliveries):
        self.coordinator,self.sessions,self.deliveries=coordinator,coordinator.sessions,deliveries

    def normalize(self,spec):
        spec=ReminderDefinitionIn.model_validate(spec).model_dump(exclude={'expected_version'})
        spec['agent']=self.coordinator.profiles.canonical(spec['agent'])
        if spec['at'] and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',spec['at']): raise CoordinationError('reminder_time','Use HH:MM in the owner timezone',422)
        if spec['days']!='*' and not re.fullmatch(r'[1-7](?:,[1-7])*',spec['days']): raise CoordinationError('reminder_days','Use * or ISO weekday numbers separated by commas',422)
        options=[]
        for raw in spec['options'] or ['done','skipped']:
            label=raw if isinstance(raw,str) else raw['label']
            value=label if isinstance(raw,str) else raw.get('value') or label
            if not label.strip() or len(label)>40 or len(value.encode())>24 or value in {o['value'] for o in options}:
                raise CoordinationError('reminder_options','Use distinct nonempty answers up to 24 bytes and labels up to 40 characters',422)
            options.append({'label':label,'value':value})
        spec['options']=options
        return spec

    async def definitions(self,owner,agent=''):
        async with self.sessions() as db:
            query=select(ReminderDefinition).where(ReminderDefinition.user_id==owner)
            if agent: query=query.where(ReminderDefinition.agent_id==self.coordinator.profiles.canonical(agent))
            return [{**r.spec,'id':r.id,'version':r.version,'enabled':r.enabled} for r in await db.scalars(query.order_by(ReminderDefinition.id).limit(100))]

    async def save(self,owner,identity,body,remove=False):
        if not re.fullmatch(r'[\w-]{1,64}',identity): raise CoordinationError('reminder_id','Invalid reminder identity',422)
        expected=body.pop('expected_version',None)
        spec=None if remove else self.normalize(body)
        async with self.sessions() as db,db.begin():
            await db.execute(update(User).where(User.id==owner).values(name=User.name))
            row=await db.get(ReminderDefinition,(owner,identity))
            if expected is not None and expected!=(row.version if row else 0): raise CoordinationError('reminder_version','Reminder changed; reread it')
            if not row:
                if remove: return {'deleted':True}
                row=ReminderDefinition(user_id=owner,id=identity,agent_id=spec['agent'],spec=spec,enabled=spec['enabled'])
                db.add(row)
            else:
                row.version+=1
                if not remove: row.spec=spec; row.agent_id=spec['agent']
                row.enabled=not remove and spec['enabled']
                await db.execute(update(ReminderCycle).where(ReminderCycle.user_id==owner,ReminderCycle.reminder_id==identity,
                    ReminderCycle.status=='open').values(status='cancelled',closed_at=time.time(),version=ReminderCycle.version+1))
            return {'id':identity,'version':row.version or 1,'deleted':remove,'enabled':row.enabled}

    async def open(self,db,owner,identity,spec,now,timezone_name):
        day=datetime.fromtimestamp(now,ZoneInfo(timezone_name)).date().isoformat()
        prior=await db.scalar(select(ReminderCycle).where(ReminderCycle.user_id==owner,ReminderCycle.reminder_id==identity,ReminderCycle.status=='open'))
        if prior: return prior,False
        row=await db.scalar(select(ReminderCycle).where(ReminderCycle.user_id==owner,ReminderCycle.reminder_id==identity,ReminderCycle.day==day))
        if row: return row,False
        row=ReminderCycle(user_id=owner,reminder_id=identity,agent_id=spec['agent'],day=day,spec=spec,
            opened_at=now,next_ask_at=now,status='open',asks=0,version=1)
        db.add(row); await db.flush()
        return row,True

    async def queue(self,db,row,now):
        if row.status!='open' or row.next_ask_at>now: return 'waiting'
        prior=list(await db.scalars(select(Delivery).where(Delivery.user_id==row.user_id,
            Delivery.payload['reminder_cycle_id'].as_integer()==row.id).order_by(Delivery.created_at.desc()).limit(1)))
        if prior and prior[0].status in {'pending','sending','unknown','rejected'}: return 'delivery_'+prior[0].status
        if row.asks>=row.spec['max_asks']:
            row.status='gave_up'; row.closed_at=now; row.version+=1; return 'gave_up'
        link=await db.get(TelegramLink,(row.user_id,row.agent_id))
        if not link or not link.chat_id or not self.deliveries.token(row.agent_id): return 'unconfigured'
        row.asks+=1; row.next_ask_at=now+60*row.spec['every_minutes']
        buttons=[[{'text':option['label'],'callback_data':f'rm:{row.id}:{row.version}:{index}'} for index,option in enumerate(row.spec['options'])]]
        await self.deliveries.add(db,row.user_id,row.agent_id,['reminder',row.id,row.version,row.asks],'telegram',
            {'bot_agent':row.agent_id,'chat_id':link.chat_id,'link_version':link.version,'text':row.spec['text'],
             'reply_markup':{'inline_keyboard':buttons},'reminder_cycle_id':row.id,'reminder_version':row.version})
        return 'queued'

    async def ask(self,owner,agent,identity,raw,now=None):
        if not re.fullmatch(r'[\w-]{1,64}',identity): raise CoordinationError('reminder_id','Invalid reminder identity',422)
        spec=self.normalize({**raw,'agent':agent}); now=time.time() if now is None else now
        async with self.sessions() as db,db.begin():
            await db.execute(update(User).where(User.id==owner).values(name=User.name))
            user=await db.get(User,owner)
            row,created=await self.open(db,owner,identity,spec,now,user.timezone)
            # Rephrasing an already-open question never sends a second copy.
            result=await self.queue(db,row,now) if created else 'already_open' if row.status=='open' else 'closed_today'
            return self.view(row)|{'status':result}

    async def tick(self,owner,agent,raw=(),now=None):
        agent=self.coordinator.profiles.canonical(agent); now=time.time() if now is None else now
        specs={s['id']:self.normalize({k:v for k,v in s.items() if k!='id'}) for s in raw}
        for saved in await self.definitions(owner,agent):
            specs[saved['id']]=self.normalize({k:v for k,v in saved.items() if k not in {'id','version'}})
        result={'status':'ok','agent':agent,'sent':[],'queued':[],'gave_up':[],'waiting':[],'skipped':[]}
        async with self.sessions() as db,db.begin():
            await db.execute(update(User).where(User.id==owner).values(name=User.name))
            user=await db.get(User,owner); local=datetime.fromtimestamp(now,ZoneInfo(user.timezone))
            for identity,spec in list(specs.items())[:100]:
                if not spec['enabled'] or spec['agent']!=agent or (spec['days']!='*' and str(local.isoweekday()) not in spec['days'].split(',')) or (spec['at'] and local.strftime('%H:%M')<spec['at']):
                    result['skipped'].append(identity); continue
                await self.open(db,owner,identity,spec,now,user.timezone)
            rows=list(await db.scalars(select(ReminderCycle).where(ReminderCycle.user_id==owner,ReminderCycle.agent_id==agent,
                ReminderCycle.status=='open').order_by(ReminderCycle.id).limit(100)))
            for row in rows:
                status=await self.queue(db,row,now)
                result['queued' if status=='queued' else 'gave_up' if status=='gave_up' else 'waiting'].append(self.view(row) if status=='queued' else row.reminder_id)
        return result

    @staticmethod
    def view(row):
        return {'cycle_id':row.id,'reminder_id':row.reminder_id,'agent_id':row.agent_id,'day':row.day,'version':row.version,
            'status':row.status,'question':row.spec['text'],'asks':row.asks,'max_asks':row.spec['max_asks'],
            'opened_at':datetime.fromtimestamp(row.opened_at,timezone.utc).isoformat(),'answer':row.answer,'via':row.answered_via,
            'closed_at':datetime.fromtimestamp(row.closed_at,timezone.utc).isoformat() if row.closed_at else None}

    async def list(self,owner,agent='',*,history=False,reminder_id='',limit=30):
        async with self.sessions() as db:
            query=select(ReminderCycle).where(ReminderCycle.user_id==owner,
                ReminderCycle.status!='open' if history else ReminderCycle.status=='open')
            if agent: query=query.where(ReminderCycle.agent_id==self.coordinator.profiles.canonical(agent))
            if reminder_id: query=query.where(ReminderCycle.reminder_id==reminder_id)
            return [self.view(r) for r in await db.scalars(query.order_by(ReminderCycle.id.desc()).limit(max(1,min(limit,100))))]

    async def answer(self,owner,identity,answer,via,*,reminder_id='',expected_version=None,callback=None):
        async with self.sessions() as db,db.begin():
            await db.execute(update(User).where(User.id==owner).values(name=User.name))
            if not identity:
                rows=list(await db.scalars(select(ReminderCycle).where(ReminderCycle.user_id==owner,ReminderCycle.status=='open',
                    ReminderCycle.reminder_id==reminder_id if reminder_id else True).limit(2)))
                if len(rows)!=1: raise CoordinationError('reminder_ambiguous','Select one open cycle explicitly',422)
                row=rows[0]
            else: row=await db.get(ReminderCycle,identity)
            if not row or row.user_id!=owner: raise CoordinationError('not_found','Reminder cycle unavailable',404)
            if row.status!='open': return self.view(row)
            if expected_version is not None and expected_version!=row.version: raise CoordinationError('reminder_version','Question changed; refresh it')
            if callback:
                agent,chat,message,index=callback
                if row.agent_id!=agent or not 0<=index<len(row.spec['options']): raise CoordinationError('reminder_scope','Callback does not match this question',403)
                link=await db.get(TelegramLink,(owner,agent))
                delivery=await db.scalar(select(Delivery).where(Delivery.user_id==owner,Delivery.payload['reminder_cycle_id'].as_integer()==row.id,
                    Delivery.payload['reminder_version'].as_integer()==row.version,Delivery.status.in_(['accepted','unknown','sending'])).order_by(Delivery.created_at.desc()).limit(1))
                if not link or link.chat_id!=chat or not delivery or delivery.payload.get('link_version')!=link.version or (delivery.remote_id and delivery.remote_id!=str(message)):
                    raise CoordinationError('reminder_scope','Callback is not bound to the linked owner message',403)
                answer=row.spec['options'][index]['value']
            if answer not in {o['value'] for o in row.spec['options']}:
                raise CoordinationError('reminder_answer','Choose an answer offered by this question',422)
            row.status='answered'; row.answer=answer; row.answered_via=via; row.closed_at=time.time(); row.version+=1
            return self.view(row)
