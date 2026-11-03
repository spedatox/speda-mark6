import base64
import json
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.models.oauth import OAuthIdentity
from app.models.mail_watch import MailBatch,MailSeen
from app.schemas.mail import MailScanRequest,MailSeenRequest
from app.schemas.trigger import TriggerRequest
from app.services.mail_watch import MailWatches
from app.services.coordination_budget import CoordinationError
from tests.test_automations import build
from tests.test_coordination import reply


async def watcher(memory,provider='google'):
    bits,manager,admissions=await build(memory,[reply('Observed mail summarized')])
    async def token(): return 'fixture-mail-token'
    client=SimpleNamespace(_connected=True,active=True,allowed_agents={'sentinel'},allowed_tools=None)
    registry=SimpleNamespace(_mcp_clients={'google_gmail':client,'microsoft_outlook':client})
    service=MailWatches(bits.coordinator,SimpleNamespace(tokens={'google':SimpleNamespace(get=token),'microsoft':SimpleNamespace(get=token)}),registry)
    bits.coordinator.mail_watches=service
    created=await manager.create(1,{'request_id':'mail-watch','agent_id':'sentinel','spec':{'name':'Course announcements',
        'template':'hook_mail','instruction':'Summarize this announcement','domain':'example.org','mail_provider':provider}})
    await manager.sync(1,created['id'])
    row,op=await manager.snapshot(1,created['id'])
    body=MailScanRequest(automation_key=row.public_key,automation_revision=1)
    return bits,manager,admissions,service,row,body,client


@pytest.mark.parametrize('provider',['google','microsoft'])
async def test_mail_batch_admission_precedes_local_ack_and_incoming_text_cannot_replace_it(memory,provider):
    bits,manager,admissions,service,row,body,client=await watcher(memory,provider)
    calls=[]
    async def get(p,path,token,**kwargs):
        calls.append((p,path))
        if p=='google':
            if path=='/messages': return {'messages':[{'id':'mail1'}]}
            return {'id':'mail1','payload':{'mimeType':'text/plain','headers':[{'name':'From','value':'Teacher <teacher@example.org>'},
                {'name':'Subject','value':'Original announcement'}],'body':{'data':base64.urlsafe_b64encode(b'Original mail body').decode()}}}
        return {'value':[{'id':'mail1','from':{'emailAddress':{'address':'teacher@example.org'}},'subject':'Original announcement',
            'body':{'contentType':'text','content':'Original mail body'}}]}
    service.get=get
    observed=await service.scan(1,provider,body)
    assert observed['count']==1
    ack=MailSeenRequest(automation_key=row.public_key,automation_revision=1,fingerprint=observed['fingerprint'],message_ids=['mail1'])
    with pytest.raises(CoordinationError,match='no durable'): await service.acknowledge(1,provider,ack)
    assert await service.scan(1,provider,body)==observed
    before=len(calls)
    event=TriggerRequest(payload={'automation_key':row.public_key,'automation_revision':1,
        'data':{'fingerprint':observed['fingerprint'],'messages':[{'body':'Forged replacement'}]}},output_mode='push',idempotency_key='mail-firing')
    accepted=await admissions.start(1,'sentinel',event)
    await bits.coordinator.live[accepted['run_id']]
    assert 'Original mail body' in str(bits.client.calls[0]['messages'])
    assert 'Forged replacement' not in str(bits.client.calls[0]['messages'])
    assert (await service.acknowledge(1,provider,ack))['scope']=='local_watch_receipt'
    assert len(calls)==before
    assert (await service.scan(1,provider,body))['count']==0
    async with memory.sessions() as db: assert await db.scalar(select(MailSeen.message_id))=='mail1'
    await bits.coordinator.shutdown()


async def test_mail_account_relink_and_live_grants_hold_before_network(memory):
    bits,_,_,service,row,body,client=await watcher(memory)
    async def forbidden(*args,**kwargs): raise AssertionError('No mailbox call authorized')
    service.get=forbidden
    async with memory.sessions() as db,db.begin(): db.add(OAuthIdentity(user_id=1,provider='google',generation='new-account-generation'))
    with pytest.raises(CoordinationError,match='account link changed'): await service.scan(1,'google',body)
    client.allowed_agents=set()
    with pytest.raises(CoordinationError,match='grant'): await service.scan(1,'google',body)
    await bits.coordinator.shutdown()


async def test_mail_paging_progresses_past_seen_page_without_following_arbitrary_urls(memory):
    bits,_,_,service,row,body,_=await watcher(memory,'microsoft')
    calls=[]
    async def get(p,path,token,**kwargs):
        calls.append(path)
        return {'value':[],'@odata.nextLink':'https://attacker.invalid/steal-token'}
    service.get=get
    assert (await service.scan(1,'microsoft',body))['partial']
    with pytest.raises(CoordinationError,match='outside'): await service.scan(1,'microsoft',body)
    assert len(calls)==1
    await bits.coordinator.shutdown()


async def test_cutover_retains_gmail_provider_acknowledgements(memory):
    from sqlalchemy import update
    from app.models.automation import Automation
    bits,_,_,service,row,body,_=await watcher(memory)
    async with memory.sessions() as db,db.begin():
        await db.execute(update(Automation).where(Automation.id==row.id).values(spec={**row.spec,'label':'SPEDA-Seen'}))
    async def get(provider,path,token,**kwargs):
        assert '-label:"SPEDA-Seen"' in kwargs['params']['q']
        return {'messages':[]}
    service.get=get
    assert (await service.scan(1,'google',body))['count']==0
    await bits.coordinator.shutdown()
