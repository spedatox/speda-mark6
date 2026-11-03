"""Original client contracts over native connection and OAuth services."""
from html import escape
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, StrictBool

router = APIRouter(tags=['connections'])


class Toggle(BaseModel):
    model_config = ConfigDict(extra='forbid')
    server: str
    active: StrictBool


@router.get('/connections')
async def connections(request: Request):
    return request.app.state.connections.summary()


@router.post('/connections')
async def toggle(request: Request, body: Toggle):
    try:
        return await request.app.state.connections.toggle(body.server, body.active)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/connections/mcp')
async def custom(request: Request):
    return request.app.state.connections.custom()


@router.post('/connections/mcp')
async def save_custom(request: Request, body: dict):
    try:
        return await request.app.state.connections.save(body)
    except ValueError:
        # Pydantic errors can contain the submitted credential values.
        return {'error':'Invalid connection. Use a unique lowercase name, explicit grants, a pinned stdio command or HTTPS URL with headers.'}


@router.delete('/connections/mcp/{name}')
async def remove_custom(request: Request, name: str):
    try:
        return await request.app.state.connections.remove(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/connections/{provider}/login')
async def login(request: Request, provider: str):
    try:
        return await request.app.state.oauth.login(provider)
    except ValueError as exc:
        return {'error':str(exc)}


@router.get('/connections/{provider}/status')
async def status(request: Request, provider: str):
    try:
        return request.app.state.oauth.status(provider)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.post('/connections/{provider}/disconnect')
async def disconnect(request: Request, provider: str):
    try:
        return await request.app.state.oauth.disconnect(provider)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get('/oauth/{provider}/start')
async def start(request: Request, provider: str, ticket: str = Query(default='', max_length=128)):
    try:
        url, browser, secure = await request.app.state.oauth.start(provider, ticket)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    response = RedirectResponse(url, status_code=303, headers={'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer'})
    response.set_cookie('speda_oauth_' + provider, browser, max_age=600, httponly=True,
        secure=secure, samesite='lax', path=f'/oauth/{provider}/callback')
    return response


@router.get('/oauth/{provider}/callback')
async def callback(request: Request, provider: str, state: str = Query(default='', max_length=128),
                   code: str = Query(default='', max_length=8192), error: str = Query(default='', max_length=256)):
    try:
        message = await request.app.state.oauth.callback(provider, state,
            request.cookies.get('speda_oauth_' + provider, ''), code, error)
        status_code = 200
    except ValueError as exc:
        message, status_code = str(exc), 400
    response = HTMLResponse('<!doctype html><html><body><h2>' + escape(message) +
        '</h2><p>You can close this tab and return to Speda.</p></body></html>', status_code=status_code,
        headers={'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer',
                 'Content-Security-Policy':"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"})
    if provider in {'google','microsoft','notion'}:
        response.delete_cookie('speda_oauth_' + provider, path=f'/oauth/{provider}/callback')
    return response
