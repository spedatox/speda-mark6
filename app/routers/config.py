"""Authenticated owner configuration. No tool or agent restart capability."""
from fastapi import APIRouter,Request,BackgroundTasks
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from app.services.config_store import ConfigUnavailable
from app.services.coordination_budget import CoordinationError

class PrivateConfigRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def safe(request):
            try: return await original(request)
            except RequestValidationError:
                return JSONResponse(status_code=422,content={'code':'config_request','detail':'Invalid configuration request fields; no submitted values are echoed.'})
        return safe


router=APIRouter(tags=['configuration'],route_class=PrivateConfigRoute)


class ConfigSave(BaseModel):
    model_config=ConfigDict(extra='forbid')
    values:dict=Field(default_factory=dict,max_length=1000)
    reset:list[str]=Field(default_factory=list,max_length=1000)
    expected_revision:int=Field(ge=0)
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')


class RestartRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0)
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')


class PolicySave(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0)
    value:dict


@router.get('/config')
async def snapshot(request:Request):
    try: result=request.app.state.configuration.snapshot()
    except ConfigUnavailable as exc: raise CoordinationError('config_unavailable',str(exc),503) from None
    return result | {'restart':request.app.state.backend_restart.status()}


@router.put('/config')
async def save(request:Request,body:ConfigSave):
    return await request.app.state.configuration.save(body.values,body.expected_revision,body.request_id,body.reset)


@router.post('/config/restart',status_code=202)
async def restart(request:Request,body:RestartRequest,background:BackgroundTasks):
    result=await request.app.state.backend_restart.request(body.expected_revision,body.request_id)
    if result['accepted']: background.add_task(request.app.state.backend_restart.signal)
    return result


@router.get('/config/policies')
async def policies(request:Request): return await request.app.state.policy_configuration.snapshot(1)


@router.put('/config/policies/{identity}')
async def policy(identity:str,request:Request,body:PolicySave):
    return await request.app.state.policy_configuration.save(1,identity,body.expected_revision,body.value)
