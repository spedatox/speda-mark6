import pytest
from httpx import AsyncClient, ASGITransport
from pydantic import ValidationError
from app.config import Settings, settings
from app.main import create_app
from app.profiles.speda import SPEDAProfile
from app.profiles.workers import WORKER_SPECS, worker_spec, canonical_worker_id
from app.schemas.llm import PolicyDocument, ThinkingRequest
from app.services.model_policy import ModelPolicy


def policy_service(memory):
    return ModelPolicy(memory.sessions, SPEDAProfile(), Settings(_env_file=None))


def test_legion_roles_preserve_identity_aliases_and_native_execution_boundaries():
    assert len(WORKER_SPECS) == 9
    assert canonical_worker_id('forge_coder') == 'autobot'
    assert canonical_worker_id('forge_pentester') == 'decepticon'
    assert canonical_worker_id('reviewer') == 'forge_reviewer'
    assert canonical_worker_id('general-purpose') == 'general'
    assert all(spec.read_only for spec in WORKER_SPECS.values() if spec.backend == 'legion')
    assert worker_spec('archivist').tool_scope == frozenset({'memory_lookup'})
    assert 'parent turn' in worker_spec('archivist').instructions
    assert worker_spec('autobot').team_lead == 'optimus'
    assert worker_spec('autobot').runtime_role == 'coder'
    assert worker_spec('decepticon').team_lead == 'scourge'
    assert worker_spec('decepticon').runtime_role == 'pentester'
    assert worker_spec('reviewer').read_only and worker_spec('reviewer').backend == 'forge'


async def test_per_worker_model_and_thinking_are_pinned_to_parent_snapshot(memory):
    policy = policy_service(memory)
    document = PolicyDocument(main_model='kimi:kimi-k3',
        worker_models={'researcher':'deepseek:deepseek-flash'},
        workers={'researcher':ThinkingRequest(mode='effort', value='low')})
    await policy.save(1,0,document)
    parent = await policy.resolve(request=ThinkingRequest(mode='effort', value='max'))
    worker = await policy.resolve_worker_policy('researcher',parent=parent,model=parent.spec.ref)
    assert worker.spec.ref == 'deepseek:deepseek-flash'
    assert worker.requested.value == 'low' and worker.source == 'worker'
    assert worker.revision == parent.revision and worker.deadline == parent.deadline
    assert not worker.fallbacks
    document.worker_models['researcher'] = 'kimi:kimi-k3'
    document.workers['researcher'] = ThinkingRequest(mode='effort', value='high')
    await policy.save(1,1,document)
    pinned = await policy.resolve_worker_policy('researcher', parent=parent)
    assert pinned.spec.ref == worker.spec.ref and pinned.requested == worker.requested
    fresh = await policy.resolve_worker_policy('researcher')
    assert fresh.spec.ref == 'kimi:kimi-k3' and fresh.requested.value == 'high'


async def test_worker_route_change_uses_target_native_setting_not_parent_effort(memory):
    policy = policy_service(memory)
    await policy.save(1,0,PolicyDocument(main_model='kimi:kimi-k3',
        models={'kimi:kimi-k2.6':ThinkingRequest(mode='enabled')},
        worker_models={'scout':'kimi:kimi-k2.6'}, roles={'worker':ThinkingRequest(mode='inherit')}))
    parent = await policy.resolve(request=ThinkingRequest(mode='effort', value='max'))
    worker = await policy.resolve_worker_policy('scout',parent=parent)
    assert worker.spec.ref == 'kimi:kimi-k2.6'
    assert worker.wire == {'thinking':{'type':'enabled'}} and worker.source == 'model'
    # Projects can supply only its admitted snapshot plus the root route fallback.
    project_worker = await policy.resolve_worker_policy('scout',model=parent.spec.ref,
        snapshot=(parent.revision,parent.snapshot), deadline=parent.deadline)
    assert project_worker.wire == worker.wire
    with pytest.raises(ValueError,match='Unsupported native'):
        await policy.resolve_worker_policy('scout',parent=parent,request=ThinkingRequest(mode='inherit'))


async def test_unpinned_worker_preserves_admitted_turn_thinking(memory):
    policy = policy_service(memory)
    await policy.save(1,0,PolicyDocument(main_model='kimi:kimi-k3',
        models={'kimi:kimi-k3':ThinkingRequest(mode='effort',value='low')}))
    parent = await policy.resolve(request=ThinkingRequest(mode='effort',value='max'))
    worker = await policy.resolve_worker_policy('general-purpose',parent=parent)
    assert worker.requested == parent.requested and worker.source == 'parent'


async def test_unknown_roles_conflicting_aliases_and_incompatible_native_controls_refuse_save(memory):
    policy = policy_service(memory)
    with pytest.raises(ValidationError): PolicyDocument(worker_models={'unbounded_executor':'kimi:kimi-k3'})
    with pytest.raises(ValidationError):
        PolicyDocument(worker_models={'forge_coder':'kimi:kimi-k3','autobot':'kimi:kimi-k2.6'})
    normalized = PolicyDocument(worker_models={'forge_coder':'kimi:kimi-k2.6'})
    assert normalized.worker_models == {'autobot':'kimi:kimi-k2.6'}
    bad = PolicyDocument(worker_models={'autobot':'kimi:kimi-k2.6'},
        workers={'autobot':ThinkingRequest(mode='effort',value='high')})
    with pytest.raises(ValueError,match='Unsupported native'):
        await policy.save(1,0,bad)
    assert (await policy.snapshot())[0] == 0
    with pytest.raises(ValueError):
        await policy.resolve_worker_policy('unknown')


async def test_legion_model_client_contract_uses_owner_policy_cas_and_aliases(memory):
    service = policy_service(memory)
    app = create_app()
    app.state.model_policy = service
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture') as client:
        assert (await client.get('/agents/legion-models')).status_code == 401
        client.headers['X-API-Key'] = settings.speda_api_key
        rows = (await client.get('/agents/legion-models')).json()
        assert len(rows) == 9 and {row['worker_id'] for row in rows} == set(WORKER_SPECS)
        assert all(row['deployment_pin'] is None for row in rows)
        changed = await client.post('/agents/legion-models',json={'worker_id':'forge_coder',
            'model':'deepseek:deepseek-flash','thinking':{'mode':'effort','value':'high'}, 'expected_revision':0})
        assert changed.status_code == 200,changed.text
        autobot = next(row for row in changed.json() if row['worker_id'] == 'autobot')
        assert autobot['override'] == 'deepseek:deepseek-flash' and autobot['policy_revision'] == 1
        assert autobot['thinking'] == {'mode':'effort','value':'high'}
        stale = await client.post('/agents/legion-models',json={'worker_id':'autobot','model':None,'expected_revision':0})
        assert stale.status_code == 409
        invalid = await client.post('/agents/legion-models',json={'worker_id':'autobot','model':'kimi:kimi-k2.6'})
        assert invalid.status_code == 422
        cleared = await client.post('/agents/legion-models',json={'worker_id':'autobot','model':None,'thinking':None,'expected_revision':1})
        assert cleared.status_code == 200
        assert (await client.post('/agents/legion-models',json={'worker_id':'unbounded','model':None})).status_code == 404
        assert (await service.snapshot())[1].worker_models == {}
