"""Existing agent settings panels over renovated policy and persona preferences."""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal
from app.schemas.coordination import PersonaStyle, PersonaUpdate
from app.schemas.llm import ThinkingRequest

router = APIRouter(prefix='/agents', tags=['agent-settings'])


class GroupMode(BaseModel):
    model_config = ConfigDict(extra='forbid')
    engaged: bool
    passphrase: str | None = Field(default=None, max_length=1024)
    platform: str | None = Field(default=None, max_length=32)


@router.get('/house-party')
async def group_mode():
    from app.core.runtime_state import get_house_party
    return {'engaged': get_house_party()}


@router.post('/house-party')
async def set_group_mode(body: GroupMode):
    import hmac
    from app.config import settings
    from app.core.runtime_state import set_house_party
    if body.engaged:
        if (body.platform or '').lower().strip() not in {'desktop', 'web'}:
            raise HTTPException(409, 'House Party can be engaged from the desktop app or web app.')
        expected, supplied = settings.house_party_passphrase.strip(), (body.passphrase or '').strip()
        if not supplied or not expected or not hmac.compare_digest(supplied, expected):
            raise HTTPException(403, 'Invalid authorization passphrase.')
    return {'engaged': set_house_party(body.engaged)}


class AgentModel(BaseModel):
    agent_id: str = Field(max_length=64)
    model: str | None = Field(default=None, max_length=255)


@router.get('/models')
async def models(request: Request):
    service = request.app.state.model_policy
    _, policy = await service.snapshot(1)
    return [{'agent_id':profile['agent_id'], 'name':profile['name'], 'domain':profile['domain'],
        'override':policy.agent_models.get(profile['agent_id']), 'telegram_override':policy.telegram_models.get(profile['agent_id']),
        'default_main':policy.main_model or service.settings.llm_main_model or service.profile.allocate_model('user'),
        'default_background':policy.background_model or service.settings.llm_background_model or service.profile.allocate_model('n8n'),
    } for profile in request.app.state.profiles.roster()]


@router.post('/models')
async def set_model(request: Request, body: AgentModel):
    aid = request.app.state.profiles.canonical(body.agent_id)
    service = request.app.state.model_policy
    revision, policy = await service.snapshot(1)
    if body.model and body.model.strip():
        policy.agent_models[aid] = body.model.strip()
    else:
        policy.agent_models.pop(aid, None)
    try:
        await service.save(1, revision, policy)
    except ValueError as exc:
        raise HTTPException(409 if 'conflict' in str(exc) else 422, str(exc)) from exc
    return await models(request)


@router.post('/telegram-models')
async def set_telegram_model(request:Request,body:AgentModel):
    aid=request.app.state.profiles.canonical(body.agent_id)
    service=request.app.state.model_policy
    revision,policy=await service.snapshot(1)
    if body.model and body.model.strip(): policy.telegram_models[aid]=body.model.strip()
    else: policy.telegram_models.pop(aid,None)
    try: await service.save(1,revision,policy)
    except ValueError as exc: raise HTTPException(409 if 'conflict' in str(exc) else 422,str(exc)) from None
    return await models(request)


class LegionModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    worker_id: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=255)
    thinking: ThinkingRequest | None = None
    expected_revision: int | None = Field(default=None, ge=0)


@router.get('/legion-models')
async def legion_models(request: Request):
    """Original worker settings shape over the revisioned native owner policy."""
    from app.profiles.workers import WORKER_SPECS
    from app.services.client_models import choices
    service = request.app.state.model_policy
    revision, policy = await service.snapshot(1)
    rows = []
    for spec in WORKER_SPECS.values():
        route = policy.worker_models.get(spec.worker_id)
        descriptor = service.descriptor(route, policy) if route else None
        rows.append({'worker_id':spec.worker_id, 'when_to_use':spec.when_to_use,
            'effort':spec.effort, 'derived_from':'Admitted parent/run model and native policy unless owner-pinned',
            'override':route, 'deployment_pin':None, 'policy_revision':revision,
            'thinking':policy.workers[spec.worker_id].model_dump() if spec.worker_id in policy.workers else None,
            'thinking_options':choices(descriptor) if descriptor else ['inherit'],
            'backend':spec.backend, 'read_only':spec.read_only, 'team':spec.team,
            'team_lead':spec.team_lead, 'runtime_role':spec.runtime_role})
    return rows


@router.post('/legion-models')
async def set_legion_model(request: Request, body: LegionModel):
    from app.profiles.workers import canonical_worker_id
    try:
        worker = canonical_worker_id(body.worker_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    service = request.app.state.model_policy
    revision, policy = await service.snapshot(1)
    if body.expected_revision is not None and body.expected_revision != revision:
        raise HTTPException(409, 'Policy revision conflict')
    if 'model' in body.model_fields_set:
        if body.model and body.model.strip():
            policy.worker_models[worker] = body.model.strip()
        else:
            policy.worker_models.pop(worker, None)
    if 'thinking' in body.model_fields_set:
        if body.thinking is not None:
            policy.workers[worker] = body.thinking
        else:
            policy.workers.pop(worker, None)
    try:
        await service.save(1, revision, policy)
    except ValueError as exc:
        raise HTTPException(409 if 'conflict' in str(exc) else 422, str(exc)) from None
    return await legion_models(request)


class PersonalitySettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    instructions: str = Field(default='', max_length=6000)
    tone: Literal['default','familiar','professional'] = 'default'
    humor: Literal['default','off','dry','playful','restrained','expressive'] = 'default'
    directness: Literal['default','gentle','direct'] = 'default'
    response_length: Literal['default','brief','detailed'] = 'default'


class Personality(BaseModel):
    agent_id: str = Field(max_length=64)
    settings: PersonalitySettings


@router.get('/personalities')
async def personalities(request: Request):
    rows = []
    for profile in request.app.state.profiles.roster():
        saved = await request.app.state.coordinator.preference(1, profile['agent_id'])
        style = saved['style']
        rows.append({'agent_id':profile['agent_id'], 'name':profile['name'], 'domain':profile['domain'],
            'settings':{'instructions':style['instructions'], 'tone':style['tone'], 'humor':style['humor'],
                'directness':style['directness'], 'response_length':style['length']}})
    return rows


@router.post('/personalities')
async def personality(request: Request, body: Personality):
    coordinator = request.app.state.coordinator
    saved = await coordinator.preference(1, body.agent_id)
    values = body.settings.model_dump()
    values['length'] = values.pop('response_length')
    await coordinator.preference(1, body.agent_id, PersonaUpdate(expected_version=saved['preference_version'],
        settings=PersonaStyle.model_validate(values)))
    return await personalities(request)
