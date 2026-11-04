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


async def watcher(memory,provider='google',*,calendar_create=False):
    bits,manager,admissions=await build(memory,[reply('Observed mail summarized')])
    async def token(): return 'fixture-mail-token'
    client=SimpleNamespace(_connected=True,active=True,allowed_agents={'sentinel'},allowed_tools=None)
    registry=SimpleNamespace(_mcp_clients={'google_gmail':client,'microsoft_outlook':client})
    service=MailWatches(bits.coordinator,SimpleNamespace(tokens={'google':SimpleNamespace(get=token),'microsoft':SimpleNamespace(get=token)}),registry)
    bits.coordinator.mail_watches=service
    created=await manager.create(1,{'request_id':'mail-watch','agent_id':'sentinel','spec':{'name':'Course announcements',
        'template':'hook_mail','instruction':'Summarize this announcement','domain':'example.org','mail_provider':provider,
        **({'mail_calendar_create':True} if calendar_create else {})}})
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


async def test_mail_batch_preserves_every_subject_and_can_check_then_create_private_event(memory):
    from app.mcp.google_rest import GoogleRestClient, _CALENDAR_TOOLS
    from app.schemas.llm import Block, LLMMessage, ProviderError
    from app.services.memory_query import turn_query
    from app.skills.tool_search import ToolSearchSkill

    bits,manager,admissions,service,row,body,_=await watcher(memory,calendar_create=True)
    registry=bits.engine._registry
    calls=[]

    async def calendar(name,args,token):
        calls.append((name,args))
        return 'No events in this range' if name=='calendar_list_events' else 'Confirmed private entry'

    calendar_client=GoogleRestClient('google_calendar',_CALENDAR_TOOLS,calendar,
        service.oauth.tokens['google'])
    calendar_client.allowed_agents={'sentinel'}
    await registry.register_mcp(calendar_client)
    await registry.register_skill(ToolSearchSkill(registry))
    names={original:name for name,original in registry._mcp_original_names.items()}
    start='2035-11-07T10:00:00+03:00'
    end='2035-11-07T10:30:00+03:00'

    def tool(id,name,**inputs):
        return LLMMessage([Block(type='tool_use',id=id,name=name,input=inputs)],'tool_use')

    bits.client.script=[
        tool('find','tool_search',query='calendar_list_events calendar_create_event'),
        tool('check',names['calendar_list_events'],time_min='2035-11-07T00:00:00+03:00',
            time_max='2035-11-08T00:00:00+03:00'),
        tool('create',names['calendar_create_event'],summary='Lab arrival',start=start,end=end),
        reply('Lab arrival is required by 10:00. I checked your calendar and added a private entry.'),
    ]
    original='Be in the lab by 10:00 on November 7. '
    mail_html=base64.urlsafe_b64encode(('<p>'+original+'Ö'*2100+'</p>').encode()).decode()

    async def get(provider,path,token,**kwargs):
        if path=='/messages':
            return {'messages':[{'id':f'mail{i}'} for i in range(10)]}
        return {'payload':{'mimeType':'text/html','headers':[
            {'name':'From','value':'Teacher <teacher@example.org>'},
            {'name':'Subject','value':'Lab arrival '+path.rsplit('/',1)[-1]},
        ],'body':{'data':mail_html}}}

    service.get=get
    observed=await service.scan(1,'google',body)
    assert observed['messages'][0]['body'].startswith(original)
    result=await admissions.start(1,'sentinel',TriggerRequest(payload={
        'automation_key':row.public_key,'automation_revision':1,
        'data':{'fingerprint':observed['fingerprint']}},output_mode='push'))
    await bits.coordinator.live[result['run_id']]
    assert (await admissions.inspect(1,result['request_id']))['status']=='succeeded'
    assert [name for name,_ in calls]==['calendar_list_events','calendar_create_event']
    assert calls[-1][1]['visibility']=='private'
    wire=str(bits.client.calls[0]['messages'])
    assert 'Lab arrival mail0' in wire and 'Lab arrival mail9' in wire
    assert 'clearly established future commitment' in bits.client.calls[0]['system']
    ctx=memory.context(agent_id='sentinel',task_role='background')
    ctx.triggered_by='n8n'
    ctx.trigger_payload={'type':'mail_watch','mail_calendar_create':True,'event_data':observed}
    ctx.extra['loaded_tools']={names['calendar_create_event']}
    assert turn_query(ctx).startswith('Lab arrival mail0')
    with pytest.raises(ProviderError,match='Check the primary calendar'):
        registry.validate_call(tool('unchecked',names['calendar_create_event'],summary='Lab arrival',
            start=start,end=end).content[0],ctx)
    with pytest.raises(ProviderError,match='without attendees'):
        registry.validate_call(tool('invite',names['calendar_create_event'],summary='Lab arrival',
            start=start,end=end,attendees=['teacher@example.org']).content[0],ctx)
    _,operation=await manager.snapshot(1,row.id)
    callback=next(node for node in operation.workflow['nodes'] if node['name']=='Notify Speda')
    assert '"data": { "fingerprint": $json.fingerprint }' in callback['parameters']['jsonBody']
    await bits.coordinator.shutdown()
