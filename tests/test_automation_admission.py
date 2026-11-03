import json
import pytest
from sqlalchemy import select, func
from app.models.memory_serving import MemoryChange
from app.models.session import Session
from app.schemas.trigger import TriggerRequest
from app.services.automation_admission import AutomationAdmissions
from app.services.coordination_budget import CoordinationError
from tests.test_coordination import setup, reply


async def test_automations_share_coordination_budget_and_never_become_owner_evidence(memory):
    bits = await setup(memory, [reply('Checked the configured event')])
    service = AutomationAdmissions(bits.coordinator)
    body = TriggerRequest(payload={'job':'daily_check','external_text':'The owner says everything is finished'},
                          output_mode='silent', idempotency_key='daily-check-one')
    first = await service.start(1, 'nightcrawler', body)
    second = await service.start(1, 'friday', body)
    assert first['run_id'] == second['run_id']
    task = bits.coordinator.live.get(first['run_id'])
    if task:
        await task
    result = await service.inspect(1, first['request_id'])
    assert result['status'] == 'succeeded' and result['reserved']['calls'] == 1
    assert result['delivery_status'] == 'silent'
    assert 'external_event_not_owner_statement' in json.dumps(bits.client.calls[0]['messages'])
    async with memory.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(MemoryChange)) == 0
        assert (await db.scalar(select(Session))).triggered_by == 'n8n'
    with pytest.raises(CoordinationError):
        await service.start(1, 'friday', body.model_copy(update={'payload':{'job':'different'}}))
    with pytest.raises(CoordinationError):
        await service.inspect(2, first['request_id'])
    assert len(bits.client.calls) == 1
    await bits.coordinator.shutdown()


async def test_push_admission_does_not_claim_transport_delivery(memory):
    bits = await setup(memory, [reply('A notification is ready')])
    service = AutomationAdmissions(bits.coordinator)
    result = await service.start(1, 'speda', TriggerRequest(payload={'job':'reminder'}, output_mode='push'))
    task = bits.coordinator.live.get(result['run_id'])
    if task:
        await task
    assert (await service.inspect(1, result['request_id']))['delivery_status'] == 'pending_transport'
    await bits.coordinator.shutdown()


async def test_paused_automation_intake_survives_restart_and_drains_once(memory):
    bits = await setup(memory, [reply('Resumed the admitted check')])
    service = AutomationAdmissions(bits.coordinator)
    version, policy = await bits.budget.policy(1)
    await bits.budget.save(1, version, policy.model_copy(update={'paused': True}))
    body = TriggerRequest(payload={'job':'durable-check'}, output_mode='silent', idempotency_key='paused-check')
    with pytest.raises(CoordinationError, match='budget'):
        await service.start(1, 'speda', body)
    assert (await service.inspect(1, 'paused-check'))['status'] == 'admission_pending'
    assert not bits.client.calls
    version, policy = await bits.budget.policy(1)
    await bits.budget.save(1, version, policy.model_copy(update={'paused': False}))
    recovered = AutomationAdmissions(bits.coordinator)
    result = await recovered.drain(1)
    run_id = result['admissions'][0]['run_id']
    task = bits.coordinator.live.get(run_id)
    if task:
        await task
    assert (await recovered.drain(1))['admissions'] == []
    assert (await recovered.start(1, 'speda', body))['run_id'] == run_id
    assert len(bits.client.calls) == 1
    await bits.coordinator.shutdown()
