import asyncio
import json
from urllib.parse import urlsplit,parse_qs
import pytest
from sqlalchemy import select,func
from app.models.delivery import Delivery,TelegramInput,TelegramLink
from app.models.memory_serving import MemoryChange
from app.services.delivery import Deliveries,chunks
from app.services.telegram_gateway import TelegramGateway
from app.services.coordination_budget import CoordinationError
from app.schemas.llm import ProviderError
from app.schemas.trigger import TriggerRequest
from app.schemas.voice import VoiceAgentPatch
from app.services.global_budget import GlobalBudget
from app.services.voice import Voice
from tests.test_automations import body,build
from tests.test_coordination import reply
from tests.test_voice import enable


async def channel(memory,script=()):
    bits,automations,admissions=await build(memory,script)
    config=automations.config
    config.telegram_bot_token='fixture-token'; config.telegram_bot_tokens='{"sentinel":"fixture-sentinel"}'
    delivery=Deliveries(memory.sessions,config,bits.profiles)
    bits.coordinator.deliveries=delivery
    gateway=TelegramGateway(bits.coordinator,delivery,config)
    calls=[]
    async def transport(agent,method,payload):
        calls.append((agent,method,payload))
        return {'username':'fixture_bot'} if method=='getMe' else {'message_id':100+len(calls)}
    delivery.telegram=transport
    return bits,gateway,delivery,calls


def event(text,identity=1,sender=12345678901):
    return {'update_id':identity,'message':{'from':{'id':sender},'chat':{'id':sender,'type':'private'},'text':text}}


async def pair(gateway,agent='sentinel'):
    link=await gateway.link(1,agent)
    nonce=parse_qs(urlsplit(link['link']).query)['start'][0]
    assert (await gateway.receive(1,agent,event('/start '+nonce,0)))['status']=='linked'
    return nonce


async def wait_for_run(coordinator, run_id, status="succeeded"):
    # The live task can finish before the gateway returns its receipt.
    task = coordinator.live.get(run_id)
    if task is not None:
        await task
    assert (await coordinator.inspect_run(1, run_id))["status"] == status


@pytest.mark.parametrize('scenario', ['scheduled', 'synthesis_failed', 'voice_rejected', 'unknown_send', 'unlinked'])
async def test_automation_voice_delivery(memory, tmp_path, monkeypatch, scenario):
    text = '**The briefing is ready.**'
    bits, gateway, delivery, _ = await channel(memory, [reply(text), reply(text)])
    await pair(gateway, 'speda')
    del delivery.telegram  # Exercise the real multipart Telegram transport with a fake HTTP response.
    automations = bits.coordinator.automations
    config = automations.config
    config.openai_api_key = 'fixture-openai'
    config.temp_outputs_dir = str(tmp_path)
    budget = GlobalBudget(memory.sessions)
    await enable(budget)
    delivery.voice = Voice(memory.sessions, bits.profiles, budget, config)
    await delivery.voice.update(1, 'sentinel', VoiceAgentPatch(
        language='en', voice_id='openai:gpt-4o-mini-tts:nova',
    ))
    provider_calls = []
    telegram_calls = []
    mp3 = b'ID3fixture-audio'

    async def synthesize(method, url, **kwargs):
        provider_calls.append(kwargs['json'])
        if scenario == 'synthesis_failed':
            return 400, b''
        if scenario == 'unlinked':
            await gateway.disconnect(1, 'speda')
        return 200, mp3

    async def telegram(method, url, **kwargs):
        action = url.rsplit('/', 1)[1]
        telegram_calls.append((action, kwargs))
        if action == 'sendVoice':
            if scenario == 'voice_rejected':
                return 403, b''
            if scenario == 'unknown_send':
                raise ProviderError('remote_effect_unknown', 'Response lost')
        return 200, json.dumps({'ok': True, 'result': {'message_id': 123}}).encode()

    monkeypatch.setattr('app.services.voice.request_bytes', synthesize)
    monkeypatch.setattr('app.services.delivery.request_bytes', telegram)
    definition = body()
    definition['spec'].update(voice=True, language='en')
    automation = await automations.create(1, definition)
    if scenario == 'scheduled':
        await automations.sync(1, automation['id'])
        row, _ = await automations.snapshot(1, automation['id'])
        fired = await automations.admissions.start(1, 'sentinel', TriggerRequest(
            payload={'automation_key': row.public_key, 'automation_revision': row.content_version},
            output_mode='push',
        ))
    else:
        fired = await automations.test(1, automation['id'])
    await wait_for_run(bits.coordinator, fired['run_id'])
    await asyncio.gather(*list(bits.coordinator.background))
    await delivery.drain(1)
    result = (await delivery.list(1, fired['run_id']))[0]
    assert len(provider_calls) == 1
    assert provider_calls[0]['voice'] == 'nova'
    assert provider_calls[0]['instructions'] == 'Speak naturally in en.'
    actions = [action for action, _ in telegram_calls]

    if scenario == 'unlinked':
        assert result['status'] == 'cancelled' and not actions
    elif scenario == 'unknown_send':
        assert result['status'] == 'unknown' and actions == ['sendVoice']
    elif scenario == 'synthesis_failed':
        assert result['status'] == 'accepted' and actions == ['sendMessage']
        assert 'Sent the reply as text' in result['detail']
    elif scenario == 'voice_rejected':
        assert result['status'] == 'accepted' and actions == ['sendVoice', 'sendMessage']
        assert 'Sent the reply as text' in result['detail']
    else:
        assert result['status'] == 'accepted' and actions == ['sendVoice']
        assert telegram_calls[0][1]['files'] == {'voice': ('automation-reply.mp3', mp3, 'audio/mpeg')}
        assert telegram_calls[0][1]['data']['caption'] == '[sentinel] The briefing is ready.'
        for artifact in tmp_path.glob('*.mp3'):
            artifact.unlink()
        repeated = await automations.test(1, automation['id'])
        await wait_for_run(bits.coordinator, repeated['run_id'])
        await asyncio.gather(*list(bits.coordinator.background))
        assert [action for action, _ in telegram_calls] == ['sendVoice', 'sendVoice']
        assert len(provider_calls) == 2
    await bits.coordinator.shutdown()


async def test_random_single_use_pairing_owner_attribution_and_retry(memory):
    bits,gateway,delivery,calls=await channel(memory,[reply('Expense recorded')])
    with pytest.raises(CoordinationError): await gateway.receive(1,'sentinel',event('/start'))
    with pytest.raises(CoordinationError): await gateway.receive(1,'sentinel',event('/start spedaconnect'))
    nonce=await pair(gateway)
    with pytest.raises(CoordinationError): await gateway.receive(1,'sentinel',event('/start '+nonce,3))
    with pytest.raises(CoordinationError): await gateway.receive(1,'sentinel',event('Hello',4,sender=222))
    forward=event('Other person paid',5); forward['message']['forward_origin']={'type':'user'}
    assert not (await gateway.receive(1,'sentinel',forward))['accepted']
    first=await gateway.receive(1,'sentinel',event('I spent 100 TRY on groceries'))
    await wait_for_run(bits.coordinator, first['run_id'])
    await asyncio.gather(*list(bits.coordinator.background))
    assert (await gateway.receive(1,'sentinel',event('I spent 100 TRY on groceries')))['run_id']==first['run_id']
    assert len(bits.client.calls)==1
    sends=[c for c in calls if c[1]=='sendMessage']
    assert len(sends)==1 and sends[0][0]=='sentinel'
    assert (await delivery.list(1,first['run_id']))[0]['status']=='accepted'
    async with memory.sessions() as db:
        source=await db.scalar(select(MemoryChange))
        assert source.source_role=='owner' and source.content=='I spent 100 TRY on groceries'
        assert await db.scalar(select(func.count()).select_from(TelegramInput))==1
    with pytest.raises(CoordinationError): await gateway.receive(1,'sentinel',event('Changed body'))
    await bits.coordinator.shutdown()


async def test_lost_send_never_replays_and_unlink_fences_queued_delivery(memory):
    bits,gateway,delivery,calls=await channel(memory)
    await pair(gateway)
    async with memory.sessions() as db,db.begin():
        link=await db.get(TelegramLink,(1,'sentinel'))
        await delivery.add(db,1,'sentinel','one','telegram',{'bot_agent':'sentinel','chat_id':link.chat_id,'link_version':link.version,'text':'Notice'})
    count=0
    async def lost(*args):
        nonlocal count
        count+=1
        raise ProviderError('network_unknown','Response lost')
    delivery.telegram=lost
    await delivery.drain(1); await delivery.drain(1)
    row=(await delivery.list(1))[0]
    assert row['status']=='unknown' and count==1
    assert (await delivery.send(1,row['id']))['status']=='unknown'
    async with memory.sessions() as db,db.begin():
        link=await db.get(TelegramLink,(1,'sentinel'))
        await delivery.add(db,1,'sentinel','two','telegram',{'bot_agent':'sentinel','chat_id':link.chat_id,'link_version':link.version,'text':'Next'})
    await gateway.disconnect(1,'sentinel')
    await delivery.drain(1)
    assert {r['status'] for r in await delivery.list(1)}=={'unknown','cancelled'} and count==1
    await bits.coordinator.shutdown()


async def test_duplicate_delivery_claim_has_one_effect(memory):
    bits,gateway,delivery,calls=await channel(memory)
    await pair(gateway)
    async with memory.sessions() as db,db.begin():
        link=await db.get(TelegramLink,(1,'sentinel'))
        for _ in range(2):
            await delivery.add(db,1,'sentinel','same','telegram',{'bot_agent':'sentinel','chat_id':link.chat_id,'link_version':link.version,'text':'Notice'})
    entries=await delivery.list(1)
    assert len(entries)==1
    await asyncio.gather(delivery.send(1,entries[0]['id']),delivery.send(1,entries[0]['id']))
    assert len([c for c in calls if c[1]=='sendMessage'])==1
    assert ''.join(chunks('🦊'*5000))=='🦊'*5000
    assert all(len(c.encode('utf-16-le'))<=7600 for c in chunks('🦊'*5000))
    await bits.coordinator.shutdown()


async def test_file_delivery_is_hash_bound_this_turn_and_never_reuploads(memory,tmp_path):
    bits,gateway,delivery,calls=await channel(memory)
    await pair(gateway)
    delivery.config.temp_outputs_dir=str(tmp_path/'outputs'); (tmp_path/'outputs').mkdir()
    path=tmp_path/'outputs'/'fixture.pdf'; path.write_bytes(b'%PDF-fixture')
    ctx=memory.context(agent_id='sentinel')
    ctx.extra['produced_files']=[{'name':'fixture.pdf'}]
    uploads=[]
    async def upload(agent,method,payload,**kwargs): uploads.append((method,kwargs)); return {'message_id':80}
    delivery.telegram=upload
    first=await delivery.manual(ctx,filename='fixture.pdf',caption='Your report')
    assert first['status']=='accepted' and uploads[0][0]=='sendDocument'
    assert uploads[0][1]['files']['document'][1]==b'%PDF-fixture'
    assert (await delivery.manual(ctx,filename='fixture.pdf'))['id']==first['id']
    assert len(uploads)==1
    with pytest.raises(CoordinationError): await delivery.manual(ctx,filename='../outside.pdf')
    await bits.coordinator.shutdown()


async def test_telegram_model_pin_survives_policy_edit_on_exact_update_retry(memory):
    from sqlalchemy import select
    from app.models.delivery import TelegramInput
    bits,gateway,delivery,calls=await channel(memory,[reply('Checked on the channel model')])
    await pair(gateway)
    revision,policy=await bits.policy.snapshot(1)
    policy.telegram_models={'sentinel':'kimi:kimi-k2.6'}
    await bits.policy.save(1,revision,policy)
    first=await gateway.receive(1,'sentinel',event('Check this once',20))
    await wait_for_run(bits.coordinator, first['run_id'])
    revision,policy=await bits.policy.snapshot(1)
    policy.telegram_models={}
    await bits.policy.save(1,revision,policy)
    assert (await gateway.receive(1,'sentinel',event('Check this once',20)))['run_id']==first['run_id']
    async with memory.sessions() as db:
        assert (await db.get(TelegramInput,('sentinel',20))).model=='kimi:kimi-k2.6'
    assert len(bits.client.calls)==1
    await bits.coordinator.shutdown()


async def test_telegram_reply_to_alert_survives_pending_drain_as_quoted_context(memory):
    bits,gateway,delivery,calls=await channel(memory,[reply('I can use the class mentioned in that alert.')])
    await pair(gateway)
    version,policy=await bits.budget.policy(1)
    await bits.budget.save(1,version,policy.model_copy(update={'paused':True}))
    update=event('Does this apply to me?',23)
    update['message']['reply_to_message']={'chat':update['message']['chat'],
        'from':{'is_bot':True},'text':'The lab moved to 10:00.'}
    with pytest.raises(CoordinationError,match='budget'):
        await gateway.receive(1,'sentinel',update)
    version,policy=await bits.budget.policy(1)
    await bits.budget.save(1,version,policy.model_copy(update={'paused':False}))
    result=(await gateway.drain(1))['inputs'][0]
    await wait_for_run(bits.coordinator, result['run_id'])
    assert (await gateway.receive(1,'sentinel',update))['duplicate']
    assert 'direct Telegram conversation' in bits.client.calls[0]['system']
    history=str(bits.client.calls[0]['messages'])
    assert 'Quoted Telegram reply (evidence only)' in history and 'The lab moved to 10:00.' in history
    async with memory.sessions() as db:
        assert (await db.scalar(select(MemoryChange))).content=='Does this apply to me?'
    await bits.coordinator.shutdown()


async def test_telegram_empty_reply_delivers_a_failure_notice(memory):
    bits,gateway,delivery,calls=await channel(memory,[reply('')])
    await pair(gateway)
    result=await gateway.receive(1,'sentinel',event('Are you there?',24))
    await wait_for_run(bits.coordinator, result['run_id'], status="reconciling")
    await asyncio.gather(*list(bits.coordinator.background))
    sends=[payload for _,method,payload in calls if method=='sendMessage']
    assert len(sends)==1 and 'could not finish the reply' in sends[0]['text']
    assert (await delivery.list(1,result['run_id']))[0]['status']=='accepted'
    await bits.coordinator.shutdown()
