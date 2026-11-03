import json

import httpx
from fastapi import FastAPI

from app.middleware.auth import APIKeyMiddleware
from app.routers.admin import router
from app.schemas.llm import PolicyDocument
from app.services.behavior_config import BehaviorConfiguration
from app.services.configuration import PolicyConfiguration
from app.services.global_budget import GlobalBudget
from app.services.iacon_budget import MaintenanceBudget
from tests.test_configuration import configuration
from tests.test_coordination import setup


async def build(memory,tmp_path,monkeypatch):
    bits=await setup(memory,[])
    config=configuration(tmp_path,openai_api_key='NEVER-EXPORT-OWNER-KEY',
        n8n_secret='NEVER-EXPORT-SCHEDULER-KEY')
    policies=PolicyConfiguration(bits.policy,bits.budget,GlobalBudget(memory.sessions),
        MaintenanceBudget(memory.sessions,bits.client))
    service=BehaviorConfiguration(bits.coordinator,bits.engine._registry,policies,config)
    # File digests are covered by a separate test. Runtime evidence should not
    # depend on the speed of the developer's synchronized source folder.
    monkeypatch.setattr(service,'source_hashes',lambda:{'core/orchestrator.py':'fixture-digest'})
    return bits,config,service


async def test_authenticated_runtime_snapshot_is_native_and_never_claims_parity(memory,tmp_path,monkeypatch):
    bits,config,service=await build(memory,tmp_path,monkeypatch)
    app=FastAPI(); app.state.behavior_configuration=service
    app.include_router(router);app.add_middleware(APIKeyMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
        assert (await client.get('/admin/evals/behavior-config')).status_code==401
        response=await client.get('/admin/evals/behavior-config',headers={'X-API-Key':'test-only-key'})
        assert response.status_code==200,response.text
        value=response.json()
    assert not value['production_verified'] and not value['acceptance']['complete']
    assert value['verification']=='runtime_snapshot_only' and value['consistent_model_revision']
    assert value['aliases']['nightcrawler']=='friday' and value['agents']['friday']['stable_id']=='nightcrawler'
    assert {p['id'] for p in value['policies']}=={'models','coordination','global','iacon'}
    roles={r['id']:r for r in value['worker_roles']}
    assert roles['autobot']['team']=='autobots' and roles['autobot']['backend']=='forge'
    assert roles['decepticon']['team']=='decepticons' and roles['decepticon']['backend']=='forge'
    assert roles['researcher']['read_only'] and roles['researcher']['backend']=='legion'
    assert 'NEVER-EXPORT' not in response.text and not bits.client.calls
    assert value['authorities']['memory']=='Iacon'
    assert 'collaborate' in {t['name'] for t in value['tool_definitions_by_agent']['speda']}
    assert 'Unbound interactive' in value['tool_scope']
    assert value['acceptance']['deferred_for_owner_review']==['projects','canvas']
    await bits.coordinator.shutdown()


async def test_snapshot_respects_native_budget_mode_and_running_saved_distinction(memory,tmp_path,monkeypatch):
    bits,config,service=await build(memory,tmp_path,monkeypatch)
    before=await service.snapshot(1)
    assert 'Task' in {t['name'] for t in before['tool_definitions_by_agent']['speda']}
    revision,policy=await bits.policy.snapshot(1)
    policy.budget_mode=True
    await bits.policy.save(1,revision,policy)
    await config.save({'agent_language':'tr'},0,'pending-language')
    after=await service.snapshot(1)
    assert after['model_policy_revision']==revision+1 and after['consistent_model_revision']
    assert 'Task' not in {t['name'] for t in after['tool_definitions_by_agent']['speda']}
    assert after['saved_configuration_revision']==1
    assert after['settings']['agent_language']==''  # saved is not running
    assert config.snapshot()['restart_required']==['agent_language']
    assert not bits.client.calls
    await bits.coordinator.shutdown()


def test_source_digests_export_only_code_paths_and_hashes():
    digests=BehaviorConfiguration.source_hashes()
    assert 'core/orchestrator.py' in digests
    assert all(not path.startswith(('/', 'C:')) and len(value)==64 for path,value in digests.items())
    assert not any('.env' in path or 'configuration-history' in path for path in digests)
