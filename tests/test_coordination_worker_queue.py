import asyncio
from dataclasses import replace
import time
import pytest
from sqlalchemy import select,update,func
from app.models.coordination import CoordinationRun,ModelCallReceipt
from app.services.coordination_budget import CoordinationError
from tests.test_worker_control import environment,parent_context


async def queued_parent(memory):
    bits,_=await environment(memory)
    parent=await parent_context(memory,bits)
    async with memory.sessions() as db,db.begin():
        run=await db.get(CoordinationRun,parent.run_id)
        limits=dict(run.limits)
        limits['run']={**limits['run'],'concurrency':1}
        run.limits=limits
    policy=replace(parent.effective_policy,deadline=time.monotonic()+5)
    return bits,parent,policy


async def test_known_child_slot_waits_before_admission_without_another_send(memory,monkeypatch):
    bits,parent,policy=await queued_parent(memory)
    first,_=await bits.budget.reserve(parent,'first',policy,{},100)
    waiting=asyncio.Event()
    original=bits.budget.reserve
    async def reserve(*args):
        try:return await original(*args)
        except CoordinationError as error:
            if error.code=='concurrency':waiting.set()
            raise
    monkeypatch.setattr(bits.budget,'reserve',reserve)
    second=asyncio.create_task(bits.budget.reserve_when_available(parent,'second',policy,{},100))
    await asyncio.wait_for(waiting.wait(),3)
    assert not second.done() and not bits.client.calls
    async with memory.sessions() as db,db.begin():
        await db.execute(update(ModelCallReceipt).where(ModelCallReceipt.id==first).values(status='complete',response={}))
    second_id,saved=await asyncio.wait_for(second,3)
    assert second_id!=first and saved is None
    async with memory.sessions() as db:
        run=await db.get(CoordinationRun,parent.run_id)
        assert run.calls==2
        assert await db.scalar(select(func.count()).select_from(ModelCallReceipt))==2
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_unknown_capacity_is_held_without_waiting_or_provider_retry(memory):
    bits,parent,policy=await queued_parent(memory)
    first,_=await bits.budget.reserve(parent,'first',policy,{},100)
    async with memory.sessions() as db,db.begin():
        await db.execute(update(ModelCallReceipt).where(ModelCallReceipt.id==first).values(status='unknown'))
    with pytest.raises(CoordinationError,match='uncertain'):
        await asyncio.wait_for(bits.budget.reserve_when_available(parent,'second',policy,{},100),2)
    async with memory.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ModelCallReceipt))==1
    assert not bits.client.calls
    await bits.coordinator.shutdown()


async def test_capacity_wait_uses_original_deadline_without_charging_a_reservation(memory):
    bits,parent,policy=await queued_parent(memory)
    await bits.budget.reserve(parent,'first',policy,{},100)
    short=replace(policy,deadline=time.monotonic()+.05)
    with pytest.raises(CoordinationError,match='deadline'):
        await asyncio.wait_for(bits.budget.reserve_when_available(parent,'second',short,{},100),2)
    async with memory.sessions() as db:
        assert (await db.get(CoordinationRun,parent.run_id)).calls==1
    assert not bits.client.calls
    await bits.coordinator.shutdown()
