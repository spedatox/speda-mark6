from dataclasses import replace
import pytest
from sqlalchemy import select
from app.core.registry import CapabilityRegistry
from app.models.academic import Device
from app.models.model_policy import ToolExecutionReceipt
from app.schemas.llm import Block, ProviderError
from app.skills.base import Skill
from app.skills.notifications import NotificationsSkill


async def test_push_uses_request_database_and_records_delivery_once(memory, monkeypatch):
    async with memory.sessions() as db, db.begin():
        db.add(Device(device_id='fixture-watch', fid='fixture-fid', token='fixture-token', platform='wear'))
    calls = []
    async def deliver(**args):
        calls.append(args)
        return True, 'sent'
    monkeypatch.setattr('app.skills.notifications.fcm.send_data_message', deliver)
    registry = CapabilityRegistry(memory.sessions)
    await registry.register_skill(NotificationsSkill())
    context = memory.context(agent_id='speda')
    context.extra['loaded_tools'] = {'send_push_notification'}
    call = Block(type='tool_use', id='notify', name='send_push_notification', input={'title':'Ready', 'body':'Report ready'})
    assert 'Delivered to 1' in await registry.execute_recorded(call, context)
    assert 'Delivered to 1' in await registry.execute_recorded(call, context)
    assert len(calls) == 1 and calls[0]['fid'] == 'fixture-fid'
    for other in [memory.context(user_id=2), replace(memory.context(), output_mode='silent')]:
        other.extra['loaded_tools'] = {'send_push_notification'}
        with pytest.raises(ProviderError, match='unavailable'):
            await registry.execute_recorded(call, other)
    assert len(calls) == 1


async def test_tool_exception_is_unknown_and_not_a_completed_error_string(memory):
    class Effect(Skill):
        name, description = 'effect', 'Fixture effect'
        input_schema = {'type':'object', 'properties':{}}
        calls = 0
        async def execute(self, args, context):
            self.calls += 1
            raise OSError('connection disappeared after dispatch')
    registry, effect = CapabilityRegistry(memory.sessions), Effect()
    await registry.register_skill(effect)
    context = memory.context()
    call = Block(type='tool_use', id='effect', name='effect', input={})
    with pytest.raises(ProviderError, match='inspection'):
        await registry.execute_recorded(call, context)
    with pytest.raises(ProviderError, match='unknown outcome'):
        await registry.execute_recorded(call, context)
    assert effect.calls == 1
    async with memory.sessions() as db:
        receipt = await db.scalar(select(ToolExecutionReceipt))
        assert receipt.status != 'completed' and receipt.result is None
