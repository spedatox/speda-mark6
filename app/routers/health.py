from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    registry = getattr(request.app.state, "registry", None)
    tools = registry.list_tools() if registry else []
    return JSONResponse(
        {
            "status": "ok",
            "tools_registered": len(tools),
        }
    )


@router.get('/health/live')
async def live():
    return {'status':'ok'}


@router.get('/health/ready')
async def ready(request:Request):
    import asyncio
    from sqlalchemy import text
    from app.database import AsyncSessionLocal
    try:
        async with asyncio.timeout(3), AsyncSessionLocal() as db:
            await db.execute(text('SELECT 1'))
        return JSONResponse({'status':'ready','instance_id':getattr(request.app.state,'backend_instance_id',None)},headers={'Cache-Control':'no-store'})
    except Exception:
        return JSONResponse({'status':'unavailable'}, status_code=503,headers={'Cache-Control':'no-store'})
