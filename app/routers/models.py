"""Authenticated model discovery and policy controls."""
from fastapi import APIRouter, HTTPException, Request

from app.schemas.llm import PolicyUpdate

router = APIRouter(tags=["models"])


@router.get("/models")
async def models(request: Request):
    from app.services.client_models import catalog
    return await catalog(request.app.state.model_policy, discovery=request.app.state.model_catalog)


@router.get("/model-policy/catalog")
async def native_catalog(request: Request):
    return await request.app.state.model_policy.catalog()


from pydantic import BaseModel, Field


class ClientThinking(BaseModel):
    model: str = Field(min_length=1, max_length=255)
    level: str | None = Field(default=None, max_length=64)


class BudgetMode(BaseModel):
    enabled: bool


@router.get('/budget-mode')
async def budget_mode(request: Request):
    _, policy = await request.app.state.model_policy.snapshot(1)
    return {'budget_mode':policy.budget_mode, 'enabled':policy.budget_mode}


@router.post('/budget-mode')
async def set_budget_mode(request: Request, body: BudgetMode):
    service = request.app.state.model_policy
    revision, policy = await service.snapshot(1)
    policy.budget_mode = body.enabled
    try:
        await service.save(1, revision, policy)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'budget_mode':policy.budget_mode, 'enabled':policy.budget_mode}


@router.post("/models/thinking")
async def thinking(request: Request, body: ClientThinking):
    from app.services.client_models import set_thinking
    try:
        return await set_thinking(request.app.state.model_policy, 1, body.model, body.level)
    except ValueError as exc:
        raise HTTPException(409 if "conflict" in str(exc) else 422, str(exc)) from exc


@router.get("/model-policy")
async def policy(request: Request):
    revision, document = await request.app.state.model_policy.snapshot()
    return {"revision": revision, "policy": document.model_dump(mode="json")}


@router.put("/model-policy")
async def update_policy(request: Request, body: PolicyUpdate):
    try:
        return await request.app.state.model_policy.save(1, body.expected_revision, body.policy)
    except ValueError as exc:
        raise HTTPException(409 if "conflict" in str(exc) else 422, str(exc)) from exc
