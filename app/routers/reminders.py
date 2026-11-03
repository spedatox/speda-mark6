"""Thin adapters for reminder settings, owner answers and n8n ticks."""
from fastapi import APIRouter,Request,Query
from app.schemas.reminder import ReminderDefinitionIn,ReminderTickRequest,ReminderAnswerRequest
from app.services.n8n import validate_n8n_secret

router=APIRouter(tags=['reminders'])


@router.get('/reminders/definitions')
async def definitions(request:Request,agent:str=''):
    return {'definitions':await request.app.state.reminders.definitions(1,agent)}


@router.put('/reminders/definitions/{identity}')
async def save(identity:str,request:Request,body:ReminderDefinitionIn):
    return await request.app.state.reminders.save(1,identity,body.model_dump())


@router.delete('/reminders/definitions/{identity}')
async def remove(identity:str,request:Request,expected_version:int|None=None):
    return await request.app.state.reminders.save(1,identity,{'expected_version':expected_version},remove=True)


@router.post('/reminders/tick')
async def tick(request:Request,body:ReminderTickRequest):
    validate_n8n_secret(request)
    result=await request.app.state.reminders.tick(1,body.agent,[s.model_dump() for s in body.reminders])
    await request.app.state.deliveries.drain(1)
    return result


@router.get('/reminders/open')
async def opened(request:Request,agent:str=''):
    return {'open':await request.app.state.reminders.list(1,agent)}


@router.get('/reminders/history')
async def history(request:Request,reminder_id:str='',limit:int=Query(30,ge=1,le=100)):
    return {'history':await request.app.state.reminders.list(1,history=True,reminder_id=reminder_id,limit=limit)}


@router.post('/reminders/answer')
async def answer(request:Request,body:ReminderAnswerRequest):
    return await request.app.state.reminders.answer(1,body.cycle_id,body.answer,'owner_api',
        reminder_id=body.reminder_id,expected_version=body.expected_version)
