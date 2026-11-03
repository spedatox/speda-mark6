"""Legacy comms presentation over attributed native events, never peer dispatch."""
from sqlalchemy import select,and_
from app.models.comms import CommsCursor
from app.models.coordination import Room,RoomEvent,AgentStep
from app.services.coordination_budget import insert_for

KINDS=('contribution_queued','consult_result','message','legion_admitted','legion_result','legion_message')


async def comms(sessions,owner,limit=100,after=0,session_id=None):
    async with sessions() as db:
        query=select(RoomEvent).join(Room,Room.id==RoomEvent.room_id).where(Room.user_id==owner,RoomEvent.kind.in_(KINDS))
        if session_id is not None: query=query.where(Room.session_id==session_id)
        recent=list(await db.scalars(query.order_by(RoomEvent.created_at.desc(),RoomEvent.room_id,RoomEvent.sequence.desc()).limit(300)))
    if recent:
        async with sessions() as db,db.begin():
            for event in reversed(recent):
                await db.execute(insert_for(db,CommsCursor).values(room_id=event.room_id,sequence=event.sequence).on_conflict_do_nothing(index_elements=['room_id','sequence']))
    async with sessions() as db:
        query=select(CommsCursor,RoomEvent,Room).join(RoomEvent,and_(RoomEvent.room_id==CommsCursor.room_id,
            RoomEvent.sequence==CommsCursor.sequence)).join(Room,Room.id==RoomEvent.room_id).where(Room.user_id==owner,CommsCursor.id>after)
        if session_id is not None: query=query.where(Room.session_id==session_id)
        rows=(await db.execute(query.order_by(CommsCursor.id.desc()).limit(max(1,min(limit,300))))).all()
        result=[]
        for cursor,event,room in rows:
            step=await db.get(AgentStep,event.step_id) if event.step_id else None
            if event.kind=='message' and (not step or step.operation_id=='owner'): continue
            parent=await db.get(AgentStep,step.parent_id) if step and step.parent_id else None
            payload=event.payload
            result.append({'id':cursor.id,'request_id':event.run_id or '', 'from_agent':event.speaker,
                'to_agent':payload.get('agent_id') or (parent.agent_id if parent else room.lead),
                'kind':event.kind,'protocol':'coordinator','task':str(payload.get('prompt') or payload.get('text') or '')[:4000],
                'result':str(payload.get('text',''))[:6000] if event.kind in {'message','consult_result','legion_result'} else None,
                'status':step.status if step else payload.get('status','recorded'),'duration_ms':None,
                'session_id':room.session_id,'origin_session_id':room.session_id,'created_at':event.created_at.isoformat()+'Z',
                'room_id':room.id,'sequence':event.sequence,'step_id':event.step_id})
        return result
