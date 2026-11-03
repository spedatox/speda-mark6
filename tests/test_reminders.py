from datetime import datetime,timezone
import pytest
from app.services.reminders import Reminders
from app.services.coordination_budget import CoordinationError
from app.schemas.llm import ProviderError
from tests.test_delivery import channel,pair,event


async def test_tick_owner_clock_buttons_and_bound_answer_stop_pending_reasks(memory):
    bits,gateway,delivery,calls=await channel(memory)
    reminders=Reminders(bits.coordinator,delivery); bits.coordinator.reminders=reminders
    await pair(gateway)
    await reminders.save(1,'evening',{'text':'Did you take the supplement?','agent':'sentinel','at':'21:00','options':['taken','skipped'],'max_asks':2,'every_minutes':5})
    early=datetime(2026,10,26,17,59,tzinfo=timezone.utc).timestamp()
    due=early+60
    assert (await reminders.tick(1,'sentinel',now=early))['skipped']==['evening']
    assert len((await reminders.tick(1,'sentinel',now=due))['queued'])==1
    assert not (await reminders.tick(1,'sentinel',now=due+600))['queued']
    await delivery.drain(1)
    sent=[c for c in calls if c[1]=='sendMessage'][-1]
    button=sent[2]['reply_markup']['inline_keyboard'][0][0]['callback_data']
    row=(await reminders.list(1))[0]
    assert row['asks']==1
    assert len((await reminders.tick(1,'sentinel',now=due+600))['queued'])==1
    remote=(await delivery.list(1))[-1]['remote_id']
    callback={'update_id':15,'callback_query':{'from':{'id':12345678901},'data':button,
        'message':{'message_id':int(remote),'chat':{'id':12345678901,'type':'private'}}}}
    result=await gateway.receive(1,'sentinel',callback)
    assert result['status']=='answered' and result['answer']=='taken'
    await delivery.drain(1)
    assert len([c for c in calls if c[1]=='sendMessage'])==1
    assert (await gateway.receive(1,'sentinel',callback))['answer']=='taken'
    assert not (await reminders.tick(1,'sentinel',now=due+1200))['queued']
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_unknown_reminder_send_holds_across_next_day_and_other_owner(memory):
    bits,gateway,delivery,calls=await channel(memory)
    reminders=Reminders(bits.coordinator,delivery); bits.coordinator.reminders=reminders
    await pair(gateway)
    now=datetime(2026,10,26,18,tzinfo=timezone.utc).timestamp()
    first=await reminders.ask(1,'sentinel','question',{'text':'Ready?','max_asks':3},now=now)
    sends=0
    async def lost(*args):
        nonlocal sends
        sends+=1
        raise ProviderError('unknown','Lost acknowledgement')
    delivery.telegram=lost
    await delivery.drain(1)
    assert (await reminders.ask(1,'sentinel','question',{'text':'Different wording'},now=now+86400))['status']=='already_open'
    assert not (await reminders.tick(1,'sentinel',now=now+86400))['queued']
    assert len(await reminders.list(1))==1 and sends==1
    with pytest.raises(CoordinationError): await reminders.answer(2,first['cycle_id'],'done','owner_api')
    await reminders.answer(1,first['cycle_id'],'done','owner_api',expected_version=1)
    assert (await reminders.list(1,history=True))[0]['via']=='owner_api'
    await bits.coordinator.shutdown()


async def test_ambiguous_answer_and_superseded_definition_never_close_wrong_cycle(memory):
    bits,gateway,delivery,calls=await channel(memory)
    reminders=Reminders(bits.coordinator,delivery)
    await pair(gateway)
    for identity in ('one','two'):
        await reminders.save(1,identity,{'text':identity,'agent':'sentinel'})
    await reminders.tick(1,'sentinel')
    with pytest.raises(CoordinationError,match='Select one'): await reminders.answer(1,0,'done','owner_api')
    await reminders.save(1,'one',{'text':'Revised one','agent':'sentinel','expected_version':1})
    await delivery.drain(1)
    assert len([c for c in calls if c[1]=='sendMessage'])==1
    assert {r['status'] for r in await delivery.list(1)}=={'accepted','cancelled'}
    await bits.coordinator.shutdown()
