from sqlalchemy import select,func
from app.models.coordination import RoomEvent,Room
from app.schemas.coordination import CreateRoom
from app.services.comms import comms
from tests.test_coordination import setup,reply,tool,send


async def test_legacy_comms_cursor_preserves_native_attribution_and_owner_room_scope(memory):
    bits=await setup(memory,[tool({'action':'consult','agent_id':'sentinel','message':'Check the ledger'}),
        reply('Waiting for the consultation.'),reply('The ledger is balanced.'),reply('The consultation is complete.')])
    room=await bits.coordinator.create(1,CreateRoom(lead='speda'))
    await send(bits,room,'Discuss the accounts')
    async with memory.sessions() as db:
        count=await db.scalar(select(func.count()).select_from(RoomEvent))
        session_id=(await db.get(Room,room['id'])).session_id
    rows=await comms(memory.sessions,1,session_id=session_id)
    assert any(r['from_agent']=='sentinel' and r['result']=='The ledger is balanced.' for r in rows)
    assert all(r['room_id']==room['id'] and r['protocol']=='coordinator' for r in rows)
    assert await comms(memory.sessions,2)==[]
    assert await comms(memory.sessions,1,after=max(r['id'] for r in rows))==[]
    assert await comms(memory.sessions,1,session_id=session_id)==rows
    async with memory.sessions() as db: assert await db.scalar(select(func.count()).select_from(RoomEvent))==count
    await bits.coordinator.shutdown()
