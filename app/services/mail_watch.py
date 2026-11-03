"""Bounded, owner-authorized Gmail/Graph watches with local acknowledgement."""
import asyncio
import base64
import html
import json
import re
from datetime import datetime,timedelta,timezone
from email.utils import getaddresses
from urllib.parse import quote,urlsplit
from sqlalchemy import select,update
from app.models.automation import Automation
from app.models.oauth import OAuthIdentity
from app.models.mail_watch import MailBatch,MailSeen,MailCursor
from app.models.automation_admission import AutomationAdmission
from app.models.coordination import CoordinationRun
from app.services.coordination_budget import CoordinationError,digest,insert_for
from app.services.remote_tools import request_bytes


def addresses(values):
    return {address.lower() for _,address in getaddresses(values) if '@' in address}


def criteria(spec):
    domain=str(spec.get('domain','')).strip().lower()
    recipient=str(spec.get('recipient','')).strip().lower()
    if domain and not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain):
        raise CoordinationError('mail_domain','Use an exact sender domain without query operators',422)
    if recipient and not re.fullmatch(r'[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}',recipient):
        raise CoordinationError('mail_recipient','Use an exact recipient email address',422)
    if not (domain or recipient): raise CoordinationError('mail_scope','Specify a sender domain or an exact recipient',422)
    return domain,recipient


def text_body(payload,remaining=2000,depth=0):
    if depth>8 or not isinstance(payload,dict): return ''
    if payload.get('mimeType')=='text/plain':
        data=payload.get('body',{}).get('data','')
        if not isinstance(data,str) or len(data)>200000: return ''
        try: return base64.urlsafe_b64decode(data+'===').decode('utf-8','replace')[:remaining]
        except ValueError: return ''
    parts=[]; size=0
    for part in payload.get('parts',[])[:20]:
        text=text_body(part,remaining-size,depth+1)
        if text: parts.append(text); size+=len(text)
        if size>=remaining: break
    return '\n'.join(parts)[:remaining]


class MailWatches:
    def __init__(self,coordinator,oauth,registry):
        self.coordinator,self.sessions,self.oauth,self.registry=coordinator,coordinator.sessions,oauth,registry

    async def binding(self,owner,agent,provider):
        if owner!=1 or provider not in {'google','microsoft'}: raise CoordinationError('mail_owner','This provider is only configured for its native owner',403)
        server='google_gmail' if provider=='google' else 'microsoft_outlook'
        client=self.registry._mcp_clients.get(server)
        names={'gmail_search','gmail_read'} if provider=='google' else {'outlook_search','outlook_read'}
        if not client or not client._connected or not getattr(client,'active',True) or agent not in client.allowed_agents or (client.allowed_tools is not None and not names<=client.allowed_tools):
            raise CoordinationError('mail_disconnected','Connect and grant this agent the mailbox read capabilities first',409)
        async with self.sessions() as db: generation=await db.get(OAuthIdentity,(owner,provider))
        return {'provider':provider,'generation':generation.generation if generation else 'legacy0'}

    async def definition(self,owner,key,revision,provider):
        async with self.sessions() as db:
            row=await db.scalar(select(Automation).where(Automation.user_id==owner,Automation.public_key==key))
        if not row or row.deleted_at or row.content_version!=revision or row.spec['kind']!='mail_watch' or row.spec.get('mail_provider','google')!=provider:
            raise CoordinationError('mail_definition','Mail watch definition is unavailable or obsolete',409)
        if not row.desired_active or not row.active or row.status!='active': raise CoordinationError('mail_inactive','Mail watch is paused or awaiting synchronization',409)
        if row.spec.get('expires_at') and datetime.fromisoformat(row.spec['expires_at'])<=datetime.now(timezone.utc):
            raise CoordinationError('mail_expired','This mailbox watch has expired',409)
        current=await self.binding(owner,row.agent_id,provider)
        if current!=row.connection_binding: raise CoordinationError('mail_account_changed','The mailbox account link changed; review and save this watch before resuming',409)
        return row,current

    async def get(self,provider,path,token,**kwargs):
        base='https://gmail.googleapis.com/gmail/v1/users/me' if provider=='google' else 'https://graph.microsoft.com/v1.0/me'
        status,raw=await request_bytes('GET',base+path,headers={'Authorization':'Bearer '+token,'Prefer':'IdType="ImmutableId", outlook.body-content-type="text"'},
            maximum=512000,timeout=8,**kwargs)
        if status>=300: raise CoordinationError('mail_unavailable','Mailbox provider did not confirm this read',503)
        try: data=json.loads(raw)
        except ValueError: raise CoordinationError('mail_response','Mailbox returned an unreadable response',503)
        if not isinstance(data,dict): raise CoordinationError('mail_response','Mailbox response was not an object',503)
        return data

    async def scan(self,owner,provider,body):
        row,binding=await self.definition(owner,body.automation_key,body.automation_revision,provider)
        domain,recipient=criteria(row.spec)
        # Poll requests cannot widen the criterion stored by the owner.
        if (body.domain and body.domain.lower()!=domain) or (body.recipient and body.recipient.lower()!=recipient):
            raise CoordinationError('mail_scope','Polling criteria differ from the owner definition',403)
        async with self.sessions() as db:
            pending=await db.scalar(select(MailBatch).where(MailBatch.user_id==owner,MailBatch.automation_id==row.id,
                MailBatch.revision==row.content_version,MailBatch.generation==binding['generation'],MailBatch.acknowledged.is_(False)))
            if pending: return pending.payload
            cursor_key=(owner,row.id,row.content_version,binding['generation'])
            saved_cursor=await db.get(MailCursor,cursor_key)
            cursor_version=saved_cursor.version if saved_cursor else 0
            cursor=saved_cursor.cursor if saved_cursor else None
        token=await self.oauth.tokens[provider].get()
        if not token: return {'status':'disconnected','count':0,'messages':[],'message_ids':[]}
        async def read(path,**kwargs):
            if await self.binding(owner,row.agent_id,provider)!=binding:
                raise CoordinationError('mail_account_changed','Mailbox changed before the read; nothing was acknowledged',409)
            return await self.get(provider,path,token,**kwargs)
        found=[]; partial=False; page_complete=True; next_cursor=None
        async with asyncio.timeout(25):
            if provider=='google':
                query='newer_than:'+str(body.newer_than_days)+'d'
                if row.spec.get('label'):
                    query+=' -label:'+json.dumps(str(row.spec['label']),ensure_ascii=False)
                # Provider search is an optimization; exact headers decide eligibility.
                filters=[]
                if domain: filters.append('from:@'+domain)
                if recipient: filters.extend(['to:'+recipient,'deliveredto:'+recipient])
                query+=' {'+' '.join(filters)+'}'
                params={'q':query,'maxResults':25}
                if cursor: params['pageToken']=cursor
                listing=await read('/messages',params=params)
                partial=bool(listing.get('nextPageToken'))
                next_cursor=listing.get('nextPageToken')
                items=listing.get('messages',[])[:25]
                known=await self.known(owner,row,provider,binding,[m.get('id') for m in items if isinstance(m,dict)])
                for index,item in enumerate(items):
                    identity=item.get('id')
                    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',identity): continue
                    if identity in known: continue
                    data=await read('/messages/'+identity,params={'format':'full'})
                    payload=data.get('payload',{}); headers=payload.get('headers',[])
                    header={str(v.get('name','')).lower():str(v.get('value','')) for v in headers if isinstance(v,dict)}
                    senders=addresses([header.get('from','')]); recipients=addresses([header.get(k,'') for k in ('to','cc','delivered-to','x-forwarded-to','x-original-to')])
                    if not ((domain and any(s.rsplit('@',1)[-1]==domain for s in senders)) or (recipient and recipient in recipients)): continue
                    found.append({'id':identity,'thread_id':str(data.get('threadId','')),'from':header.get('from','')[:500],
                        'from_email':next(iter(sorted(senders)),''),'subject':header.get('subject','')[:500],'date':header.get('date','')[:100],
                        'snippet':str(data.get('snippet',''))[:500],'body':text_body(payload,body.body_chars) if body.include_body else ''})
                    if len(found)>=body.max_results:
                        page_complete=index==len(items)-1; partial=partial or not page_complete; break
            else:
                since=(datetime.now(timezone.utc)-timedelta(days=body.newer_than_days)).isoformat()
                fields='id,conversationId,from,toRecipients,ccRecipients,subject,receivedDateTime,bodyPreview'+(',body' if body.include_body else '')
                if cursor:
                    parsed=urlsplit(cursor)
                    if parsed.scheme!='https' or parsed.netloc!='graph.microsoft.com' or parsed.path!='/v1.0/me/messages' or parsed.fragment:
                        raise CoordinationError('mail_cursor','Provider paging URL is outside the mailbox read endpoint',409)
                    data=await read('/messages?'+parsed.query)
                else: data=await read('/messages',params={'$top':25,'$filter':'receivedDateTime ge '+since,'$orderby':'receivedDateTime desc','$select':fields})
                partial=bool(data.get('@odata.nextLink'))
                next_cursor=data.get('@odata.nextLink')
                items=data.get('value',[])[:25]
                known=await self.known(owner,row,provider,binding,[m.get('id') for m in items if isinstance(m,dict)])
                for index,item in enumerate(items):
                    if item.get('id') in known: continue
                    sender=item.get('from',{}).get('emailAddress',{}).get('address','').lower()
                    recipients={r.get('emailAddress',{}).get('address','').lower() for r in item.get('toRecipients',[])+item.get('ccRecipients',[])}
                    if not ((domain and sender.rsplit('@',1)[-1]==domain) or (recipient and recipient in recipients)): continue
                    identity=item.get('id')
                    if not isinstance(identity,str) or not 1<=len(identity)<=512: continue
                    content=item.get('body',{}).get('content','') if body.include_body else ''
                    if item.get('body',{}).get('contentType','').lower()=='html': content=html.unescape(re.sub(r'<[^>]+>',' ',content))
                    found.append({'id':identity,'thread_id':str(item.get('conversationId',''))[:512],'from':sender,'from_email':sender,
                        'subject':str(item.get('subject',''))[:500],'date':str(item.get('receivedDateTime',''))[:100],
                        'snippet':str(item.get('bodyPreview',''))[:500],'body':content[:body.body_chars]})
                    if len(found)>=body.max_results:
                        page_complete=index==len(items)-1; partial=partial or not page_complete; break
        # Recheck account and grants after network reads, before admitting data.
        current=await self.binding(owner,row.agent_id,provider)
        if current!=binding: raise CoordinationError('mail_account_changed','Mailbox changed during scan; nothing was acknowledged',409)
        async with self.sessions() as db,db.begin():
            await db.execute(update(Automation).where(Automation.id==row.id).values(updated_at=Automation.updated_at))
            fresh=await db.get(Automation,row.id)
            if fresh.content_version!=row.content_version or fresh.status!='active': raise CoordinationError('mail_definition','Watch changed during scan',409)
            position=await db.get(MailCursor,cursor_key)
            if (position.version if position else 0)!=cursor_version: raise CoordinationError('mail_scan_raced','Another scan advanced this mailbox page; retry the read',409)
            if not position:
                position=MailCursor(user_id=owner,automation_id=row.id,revision=row.content_version,generation=binding['generation'],version=0)
                db.add(position)
            if next_cursor is not None and (not isinstance(next_cursor,str) or len(next_cursor)>10000):
                raise CoordinationError('mail_cursor','Provider paging cursor exceeds its bound',503)
            position.cursor=next_cursor if page_complete else cursor
            position.version+=1
            seen=set(await db.scalars(select(MailSeen.message_id).where(MailSeen.user_id==owner,MailSeen.automation_id==row.id,
                MailSeen.provider==provider,MailSeen.generation==binding['generation'],MailSeen.message_id.in_([m['id'] for m in found]))))
            messages=[m for m in found if m['id'] not in seen][:body.max_results]
            fingerprint=digest({'watch':row.public_key,'revision':row.content_version,'binding':binding,'ids':sorted(m['id'] for m in messages)})
            payload={'status':'ok','count':len(messages),'message_ids':[m['id'] for m in messages],'messages':messages,
                'fingerprint':fingerprint,'automation_key':row.public_key,'automation_revision':row.content_version,
                'partial':partial or len(found)>body.max_results,'authority':'external_mail_not_owner_statement'}
            if messages:
                await db.execute(insert_for(db,MailBatch).values(user_id=owner,fingerprint=fingerprint,automation_id=row.id,revision=row.content_version,
                    provider=provider,generation=binding['generation'],payload=payload,acknowledged=False).on_conflict_do_nothing())
                original=await db.get(MailBatch,(owner,fingerprint)); payload=original.payload
        return payload

    async def known(self,owner,row,provider,binding,ids):
        async with self.sessions() as db:
            return set(await db.scalars(select(MailSeen.message_id).where(MailSeen.user_id==owner,MailSeen.automation_id==row.id,
                MailSeen.provider==provider,MailSeen.generation==binding['generation'],MailSeen.message_id.in_([i for i in ids if isinstance(i,str)]))))

    async def event(self,owner,definition,data):
        async with self.sessions() as db: batch=await db.get(MailBatch,(owner,data.get('fingerprint','')))
        provider=definition.spec.get('mail_provider','google')
        current=await self.binding(owner,definition.agent_id,provider)
        if not batch or batch.automation_id!=definition.id or batch.revision!=definition.content_version or batch.generation!=current['generation']:
            raise CoordinationError('mail_evidence','The event has no matching observed mailbox batch',409)
        return batch.payload

    async def acknowledge(self,owner,provider,body):
        row,binding=await self.definition(owner,body.automation_key,body.automation_revision,provider)
        async with self.sessions() as db,db.begin():
            await db.execute(update(Automation).where(Automation.id==row.id).values(updated_at=Automation.updated_at))
            batch=await db.get(MailBatch,(owner,body.fingerprint))
            if not batch or batch.automation_id!=row.id or batch.revision!=row.content_version or batch.generation!=binding['generation']:
                raise CoordinationError('mail_evidence','Acknowledgement does not match the observed batch',409)
            if body.message_ids and set(body.message_ids)!=set(batch.payload['message_ids']): raise CoordinationError('mail_evidence','Message ids differ from the admitted batch',409)
            admission=await db.scalar(select(AutomationAdmission).join(CoordinationRun,CoordinationRun.room_id==AutomationAdmission.room_id).where(
                AutomationAdmission.user_id==owner,AutomationAdmission.payload['automation_key'].as_string()==row.public_key,
                AutomationAdmission.payload['event_data']['fingerprint'].as_string()==body.fingerprint))
            if not admission: raise CoordinationError('mail_unadmitted','The batch has no durable admitted run; it remains pending',409)
            for identity in batch.payload['message_ids']:
                await db.execute(insert_for(db,MailSeen).values(user_id=owner,automation_id=row.id,provider=provider,
                    generation=binding['generation'],message_id=identity).on_conflict_do_nothing())
            batch.acknowledged=True
        return {'status':'ok','marked':len(batch.payload['message_ids']),'label':body.label,'scope':'local_watch_receipt'}
