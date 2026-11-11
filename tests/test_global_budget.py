import asyncio
import httpx
import pytest
from app.config import Settings
from app.profiles.speda import SPEDAProfile
from app.schemas.llm import PolicyDocument, ThinkingRequest
from app.services.coordination_budget import CoordinationError
from app.services.global_budget import GlobalBudget, GlobalPolicy, GlobalCall, GlobalDay
from app.services.llm_client import LLMClient
from app.services.model_policy import ModelPolicy
from tests.test_llm_streams import chat_events
from sqlalchemy import select, func


async def configured(memory, **limits):
    policy = ModelPolicy(memory.sessions, SPEDAProfile(), Settings(_env_file=None))
    await policy.save(1, 0, PolicyDocument(main_model='kimi:kimi-k2.6',
        models={'kimi:kimi-k2.6':ThinkingRequest(mode='off')}))
    effective = await policy.resolve(user_id=1)
    budget = GlobalBudget(memory.sessions)
    await budget.save(1, 0, GlobalPolicy(paused=False, daily_calls=100, daily_tokens=10000000,
        daily_microdollars=10000000, prices={'kimi:kimi-k2.6':{'microdollars_per_token':1}}, **limits))
    return budget, effective


@pytest.mark.parametrize('priced', [False, True])
async def test_disabled_spending_limits_allow_calls_past_zero_allowances(memory, priced):
    budget, policy = await configured(memory)
    await budget.save(1, 1, GlobalPolicy(paused=False, enforce_limits=False,
        lifetime_calls=0, lifetime_tokens=0, lifetime_microdollars=0,
        prices={policy.spec.ref: {'microdollars_per_token': 1}} if priced else {}))
    await budget.preflight(1, policy, images=True)
    key = await budget.reserve(1, policy, {'input': 'test'}, 16, 'unrestricted')
    await budget.finish(key, 'complete')
    status = await budget.status(1)
    assert status['lifetime_reserved']['calls'] == 1
    assert status['lifetime_reserved']['tokens'] > 0
    assert (status['lifetime_reserved']['microdollars'] > 0) == priced
    # Active calls still limit concurrency, and closed unknown calls keep their receipts.
    await budget.save(1, 2, GlobalPolicy(paused=False, enforce_limits=False, concurrent_calls=1))
    key = await budget.reserve(1, policy, {}, 16, 'uncertain')
    with pytest.raises(CoordinationError, match='slots'):
        await budget.reserve(1, policy, {}, 16, 'next')
    await budget.finish(key, 'unknown')
    restarted = GlobalBudget(memory.sessions)
    next_key = await restarted.reserve(1, policy, {}, 16, 'next')
    with pytest.raises(CoordinationError, match='slots'):
        await restarted.reserve_external(1, 'speech:fixture', 100, 'speech')
    await restarted.finish(next_key, 'complete')
    speech = await restarted.reserve_external(1, 'speech:fixture', 100, 'speech')
    await restarted.finish(speech, 'complete')
    async with memory.sessions() as db:
        assert (await db.get(GlobalCall, key)).status == 'unknown'
    assert (await restarted.status(1))['lifetime_reserved']['calls'] == 4


async def test_disabled_spending_limits_allow_unpriced_external_calls(memory):
    budget = GlobalBudget(memory.sessions)
    await budget.save(1, 0, GlobalPolicy(paused=False, enforce_limits=False,
        lifetime_calls=0, lifetime_microdollars=0))
    key = await budget.reserve_external(1, 'speech:fixture', 100, 'speech')
    await budget.finish(key, 'complete')
    assert (await budget.status(1))['lifetime_reserved']['calls'] == 1


async def test_unrestricted_client_chat_keeps_receipts_and_retry_identity(memory):
    from uuid import uuid4
    from app.schemas.client_compat import ClientChat
    from app.schemas.coordination import SpendingPolicy
    from app.services.client_compat import ClientCompatibility
    from tests.test_coordination import setup, reply
    bits = await setup(memory, [reply('No artificial spending gate')])
    await bits.budget.save(1, 1, SpendingPolicy(paused=False, enforce_limits=False,
        run={'tokens': 2000000}))
    bits.client.spending = GlobalBudget(memory.sessions)
    bits.client.configured = lambda _: True
    await bits.client.spending.save(1, 0, GlobalPolicy(paused=False, enforce_limits=False))
    adapter = ClientCompatibility(bits.coordinator)
    body = ClientChat(message='Hello', request_id=str(uuid4()))
    try:
        first = await adapter.start(1, 'speda', body)
        task = bits.coordinator.live.get(first['run_id'])
        if task:
            await task
        run = await bits.coordinator.inspect_run(1, first['run_id'])
        assert run['status'] == 'succeeded', run
        assert run['calls'][0]['status'] == 'complete'
        assert (await adapter.start(1, 'speda', body))['run_id'] == first['run_id']
        assert len(bits.client.calls) == 1
    finally:
        await bits.coordinator.shutdown()


async def test_lifetime_budget_counts_prior_days_and_unknown_outcomes(memory):
    budget, policy = await configured(memory, lifetime_microdollars=5000000)
    async with memory.sessions() as db, db.begin():
        db.add(GlobalDay(user_id=1, day='2025-01-01', calls=1, tokens=100, microdollars=4999999))
        db.add(GlobalCall(id='a'*32, user_id=1, day='2025-01-01', route=policy.spec.ref,
            purpose='prior_test', status='unknown', tokens=100, microdollars=4999999))
    with pytest.raises(CoordinationError, match='total spending'):
        await budget.reserve(1, policy, {'input':'test'}, 16, 'today')
    status = await budget.status(1)
    assert status['lifetime_reserved']['microdollars'] == 4999999
    assert status['lifetime_reserved']['calls'] == 1


async def test_lifetime_concurrency_and_pause_block_before_provider_dispatch(memory):
    budget, policy = await configured(memory, lifetime_calls=1)
    responses = await asyncio.gather(*(budget.reserve(1, policy, {'input':'test'}, 16, 'race')
        for _ in range(2)), return_exceptions=True)
    assert sum(isinstance(r, str) for r in responses) == 1
    assert sum(isinstance(r, CoordinationError) for r in responses) == 1
    await budget.finish(next(r for r in responses if isinstance(r, str)), 'unknown')
    # Replacing the process/service does not reset the total.
    with pytest.raises(CoordinationError):
        await GlobalBudget(memory.sessions).reserve(1, policy, {}, 16, 'restart')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={'content-type':'text/event-stream'}, text=chat_events(reasoning=False))
    config = Settings(_env_file=None, kimi_api_key='fixture-only')
    client = LLMClient(ModelPolicy(memory.sessions, SPEDAProfile(), config), config,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), spending=budget)
    try:
        with pytest.raises(CoordinationError):
            await client.create_message(model=policy.spec.ref, system='test',
                messages=[{'role':'user', 'content':'test'}], max_tokens=16)
        assert not calls
    finally:
        await client.close()


async def test_global_capacity_rejection_does_not_wedge_coordination(memory):
    from app.schemas.coordination import CreateRoom
    from tests.test_coordination import send, setup

    bits = await setup(memory, [])
    await bits.budget.save(1, 1, bits.spending.model_copy(update={'concurrent_calls': 1}))
    gate = GlobalBudget(memory.sessions)
    await gate.save(1, 0, GlobalPolicy(paused=False, enforce_limits=False, concurrent_calls=1))
    route = await bits.policy.resolve(user_id=1)
    active = await gate.reserve(1, route, {}, 16, 'existing call')
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=chat_events(reasoning=False))

    config = Settings(_env_file=None, kimi_api_key='fixture-only')
    client = LLMClient(bits.policy, config,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), spending=gate)
    bits.budget.client = client
    try:
        room = await bits.coordinator.create(1, CreateRoom())
        blocked = await send(bits, room)
        assert blocked['status'] == 'failed'
        assert blocked['calls'][0]['status'] == 'rejected'
        assert not sent
        await gate.finish(active, 'unknown')

        new_room = await bits.coordinator.create(1, CreateRoom())
        recovered = await send(bits, new_room, input_id='after-saturation')
        assert recovered['status'] == 'succeeded', recovered
        assert recovered['calls'][0]['status'] == 'complete'
        assert len(sent) == 1
    finally:
        await bits.coordinator.shutdown()
        await client.close()


async def test_shared_pause_rejects_chat_before_admission_and_keeps_automation_intake(memory):
    from app.models.coordination import Room, CoordinationRun
    from app.models.message import Message
    from app.schemas.client_compat import ClientChat
    from app.schemas.trigger import TriggerRequest
    from app.services.client_compat import ClientCompatibility
    from app.services.automation_admission import AutomationAdmissions
    from tests.test_coordination import setup, reply
    bits = await setup(memory, [reply('Unpaused')])
    gate = GlobalBudget(memory.sessions)
    bits.client.spending = gate
    bits.client.configured = lambda _: True
    adapter = ClientCompatibility(bits.coordinator)
    with pytest.raises(CoordinationError, match='Global spending'):
        await adapter.start(1, 'speda', ClientChat(message='Hello'))
    async with memory.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Room)) == 0
        assert await db.scalar(select(func.count()).select_from(CoordinationRun)) == 0
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
    admissions = AutomationAdmissions(bits.coordinator)
    event = TriggerRequest(payload={'job':'paused-global'}, output_mode='silent', idempotency_key='global-paused')
    with pytest.raises(CoordinationError, match='Global spending'):
        await admissions.start(1, 'speda', event)
    assert (await admissions.inspect(1, 'global-paused'))['status'] == 'admission_pending'
    assert not bits.client.calls
    await gate.save(1, 0, GlobalPolicy(paused=False, daily_calls=100, daily_tokens=1000000,
        daily_microdollars=1000000, prices={'kimi:kimi-k2.6':{'microdollars_per_token':1}}))
    result = await admissions.start(1, 'speda', event)
    task = bits.coordinator.live.get(result['run_id'])
    if task:
        await task
    assert len(bits.client.calls) == 1
    await bits.coordinator.shutdown()


async def test_completed_client_retry_survives_later_budget_pause(memory):
    from uuid import uuid4
    from app.schemas.client_compat import ClientChat
    from app.services.client_compat import ClientCompatibility
    from tests.test_coordination import setup, reply
    bits = await setup(memory, [reply('Already answered')])
    adapter = ClientCompatibility(bits.coordinator)
    body = ClientChat(message='Hello', request_id=str(uuid4()))
    first = await adapter.start(1, 'speda', body)
    task = bits.coordinator.live.get(first['run_id'])
    if task:
        await task
    version, policy = await bits.budget.policy(1)
    await bits.budget.save(1, version, policy.model_copy(update={'paused':True}))
    assert (await adapter.start(1, 'speda', body))['run_id'] == first['run_id']
    assert len(bits.client.calls) == 1
    await bits.coordinator.shutdown()
