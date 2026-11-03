import json
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from app.models.message import Message
from app.models.memory_serving import MemoryChange
from app.models.coordination import AgentStep,CoordinationRun
from app.schemas.client_compat import ClientChat
from app.services.client_compat import ClientCompatibility
from app.core.surface import validate
from tests.test_coordination import setup,reply


async def test_client_surface_is_live_wire_data_and_never_saved_as_history_or_evidence(memory):
    bits=await setup(memory,[reply('I can use the location you shared.'),reply('The next reply.')])
    adapter=ClientCompatibility(bits.coordinator)
    first=await adapter.start(1,'speda',ClientChat(message='What is nearby?',client_context={'platform':'android',
        'voice':True,'locale':'tr-TR','location':{'lat':41.1,'lng':29.2,'accuracy_m':15,'place':'TRANSIENT_PLACE_ABC'}}))
    await bits.coordinator.live[first['run_id']]
    wire=json.dumps(bits.client.calls[0]['messages'],ensure_ascii=False)
    assert 'TRANSIENT_PLACE_ABC' in wire and 'This reply will be spoken' in wire
    async with memory.sessions() as db:
        saved=[str(m.content) for m in await db.scalars(select(Message))]
        saved += [str(s.history) for s in await db.scalars(select(AgentStep))]
        saved += [str(s.policy) for s in await db.scalars(select(CoordinationRun))]
        saved += [str((s.content,s.provenance)) for s in await db.scalars(select(MemoryChange))]
    assert 'TRANSIENT_PLACE_ABC' not in '\n'.join(saved)
    assert first['run_id'] not in bits.coordinator._surfaces
    second=await adapter.start(1,'speda',ClientChat(message='And tomorrow?',session_id=first['session_id']))
    await bits.coordinator.live[second['run_id']]
    assert 'TRANSIENT_PLACE_ABC' not in json.dumps(bits.client.calls[1]['messages'])
    assert 'This reply will be spoken' not in json.dumps(bits.client.calls[1]['messages'])
    await bits.coordinator.shutdown()


def test_client_telemetry_is_typed_bounded_and_never_an_arbitrary_instruction_field():
    assert 'system_prompt' not in validate({'platform':'web','system_prompt':'Injected instructions'})
    with pytest.raises(ValidationError): validate({'location':{'lat':91,'lng':0}})
    with pytest.raises(ValidationError): validate({'device':'x'*101})
    with pytest.raises(ValidationError): validate({'location':{'lat':float('nan'),'lng':0}})
