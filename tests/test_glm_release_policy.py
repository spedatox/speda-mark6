import pytest
from app.schemas.llm import ThinkingRequest
from app.services.llm_protocol import request_body
from scripts.import_model_policy import migrate_legacy
from tests.test_model_policy import service


@pytest.mark.parametrize('model',['zai:glm-5.3','zai:glm-5.3-flash','zai:glm-5.3-flashx'])
async def test_deployed_glm_native_efforts_are_exact_and_never_claim_off(memory,model):
    policy=service(memory)
    for effort in ('low','high','max'):
        effective=await policy.resolve(model=model,request=ThinkingRequest(mode='effort',value=effort))
        body=request_body(effective,'System',[{'role':'user','content':'Test'}],[],1024)
        assert body['thinking']=={'type':'enabled'} and body['reasoning_effort']==effort
    with pytest.raises(ValueError,match='cannot disable'):
        await policy.resolve(model=model,request=ThinkingRequest(mode='off'))
    with pytest.raises(ValueError,match='Unsupported native'):
        await policy.resolve(model=model,request=ThinkingRequest(mode='effort',value='medium'))


def test_production_glm_policy_migration_preserves_pins_and_reports_forced_thinking():
    raw={'agent_models':{'speda':'zai:glm-5.3','nightcrawler':'zai:glm-5.3-flash'},
        'model_thinking':{'zai:glm-5.3':'low','zai:glm-5.3-flash':'none'}}
    document,report=migrate_legacy(raw)
    assert document.agent_models=={'speda':'zai:glm-5.3','friday':'zai:glm-5.3-flash'}
    assert document.models['zai:glm-5.3-flash']==ThinkingRequest(mode='effort',value='low')
    mapped=next(r for r in report if r.get('model')=='zai:glm-5.3-flash' and r['kind']=='thinking')
    assert mapped['legacy']=='none' and mapped['applied']=={'thinking':{'type':'enabled'},'reasoning_effort':'low'}
    assert mapped['note']
