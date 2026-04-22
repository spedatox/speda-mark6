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
