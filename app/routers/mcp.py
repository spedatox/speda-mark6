"""Authenticated MCP diagnostics; no direct tool-execution bypass."""
from fastapi import APIRouter, Request, HTTPException

router = APIRouter(prefix='/mcp', tags=['mcp'])


@router.get('/servers')
async def servers(request: Request):
    return [client.status() for client in request.app.state.registry._mcp_clients.values()]


@router.post('/servers/{server}/refresh')
async def refresh(request: Request, server: str):
    client = request.app.state.registry._mcp_clients.get(server)
    if client is None:
        raise HTTPException(404, 'MCP connection unavailable')
    await client.refresh_tools()
    return client.status()


@router.post('/servers/{server}/reconnect')
async def reconnect(request: Request, server: str):
    try:
        return await request.app.state.connections.reconnect(server)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
