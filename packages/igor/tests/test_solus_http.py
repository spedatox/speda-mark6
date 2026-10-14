# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
import httpx
from fastapi import FastAPI
from app.config import settings
from app.routers.solus import router
from app.services.solus.http import SolusHTTP
from app.middleware.auth import AuthMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from test_solus_core import ledger


@pytest.fixture
async def client(ledger, monkeypatch):
    _, factory, _, _ = ledger
    monkeypatch.setattr(settings, 'speda_api_key', 'solus-test-owner-key')
    app = FastAPI()
    app.state.solus_http = SolusHTTP(factory)
    app.include_router(router)
    @app.get('/private')
    async def private():
        return {'private': True}
    app.add_middleware(AuthMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://solus.test') as value:
        yield value


async def configure(client):
    response = await client.post('/solus/api/v1/setup', headers={'X-API-Key':'solus-test-owner-key'}, json={'new_password':'A sufficiently long test passphrase'})
    assert response.status_code == 200, response.text
    login = await client.post('/solus/api/v1/login', json={'password':'A sufficiently long test passphrase'})
    assert login.status_code == 200, login.text
    assert 'HttpOnly' in login.headers['set-cookie'] and 'Secure' in login.headers['set-cookie']
    return login.json()['csrf']


async def test_password_cookie_scope_csrf_and_change(client, ledger):
    assert (await client.get('/solus/api/v1/books')).status_code == 401
    assert (await client.get('/solus')).status_code == 200
    csrf = await configure(client)
    assert (await client.get('/solus/api/v1/books')).status_code == 200
    assert (await client.get('/private')).status_code == 401
    payload={'operation':'book_create','command_key':'browser-command-1','name':'Business','kind':'business'}
    assert (await client.post('/solus/api/v1/commands',json=payload)).status_code == 401
    assert (await client.post('/solus/api/v1/commands',headers={'X-Solus-CSRF':csrf,'Origin':'https://attacker.test'},json=payload)).status_code == 401
    valid = await client.post('/solus/api/v1/commands',headers={'X-Solus-CSRF':csrf,'Origin':'https://solus.test'},json=payload)
    assert valid.status_code == 200,valid.text
    second = httpx.AsyncClient(transport=client._transport,base_url='https://solus.test')
    other = await second.post('/solus/api/v1/login',json={'password':'A sufficiently long test passphrase'})
    assert other.status_code == 200
    changed = await client.post('/solus/api/v1/password',headers={'X-Solus-CSRF':csrf},json={
        'password':'A sufficiently long test passphrase','new_password':'An entirely different test passphrase'})
    assert changed.status_code == 200,changed.text
    assert (await client.get('/solus/api/v1/books')).status_code == 401
    assert (await second.get('/solus/api/v1/books')).status_code == 401
    assert (await client.post('/solus/api/v1/login',json={'password':'A sufficiently long test passphrase'})).status_code == 401
    assert (await client.post('/solus/api/v1/login',json={'password':'An entirely different test passphrase'})).status_code == 200
    assert (await client.get('/solus/api/v1/books')).headers['cache-control']=='no-store'
    await second.aclose()


async def test_throttle_and_secrets_not_echoed(client):
    await configure(client)
    invalid = await client.post('/solus/api/v1/login',json={'password':'z'*257})
    assert invalid.status_code == 422 and 'z'*100 not in invalid.text
    for _ in range(5):
        assert (await client.post('/solus/api/v1/login',json={'password':'wrong'})).status_code == 401
    blocked = await client.post('/solus/api/v1/login',json={'password':'wrong'})
    assert blocked.status_code == 429 and blocked.headers['retry-after']=='900'


async def test_setup_cannot_overwrite_password_or_leak_data(client):
    await configure(client)
    overwrite = await client.post('/solus/api/v1/setup',headers={'X-API-Key':'solus-test-owner-key'},json={'new_password':'Attacker overwrite attempt'})
    assert overwrite.status_code == 409
    assert (await client.get('/solus/api/v1/auth-status')).json()=={'configured':True}


async def test_private_setup_token_is_single_use(client, ledger):
    from hashlib import sha256
    from datetime import datetime, timedelta, timezone
    from app.models.solus_ledger import SolusSetupToken
    _,factory,_,_=ledger
    token='random-private-fixture-token-only'
    async with factory() as db:
        db.add(SolusSetupToken(token_hash=sha256(token.encode()).hexdigest(),expires_at=datetime.now(timezone.utc)+timedelta(minutes=5)))
        await db.commit()
    body={'new_password':'Private setup fixture password','token':token}
    assert (await client.post('/solus/api/v1/setup',json=body)).status_code==200
    assert (await client.post('/solus/api/v1/setup',json=body)).status_code==401
    assert (await client.post('/solus/api/v1/login',json={'password':body['new_password']})).status_code==200


async def test_compiled_assets_are_public_and_confined(client):
    import re
    index=await client.get('/solus')
    assets=re.findall(r'(?:src|href)="(/solus/assets/[^\"]+)"',index.text)
    assert len(assets)>=2
    for path in assets:
        response=await client.get(path)
        assert response.status_code==200
        assert 'immutable' in response.headers['cache-control']
        assert 'unsafe-eval' not in response.headers['content-security-policy']
        assert (await client.head(path)).status_code==200
    assert (await client.get('/solus/assets/%2e%2e/%2e%2e/config.py')).status_code==404


async def test_receipt_resolution_requires_csrf_and_blocks_late_save(client):
    csrf=await configure(client)
    key='uncertain-browser-command'
    path='/solus/api/v1/receipts/'+key+'/resolve'
    assert (await client.post(path)).status_code==401
    resolved=await client.post(path,headers={'X-Solus-CSRF':csrf})
    assert resolved.status_code==200 and resolved.json()['status']=='cancelled'
    late=await client.post('/solus/api/v1/commands',headers={'X-Solus-CSRF':csrf},json={
        'operation':'book_create','command_key':key,'name':'Must not post','kind':'personal'})
    assert late.status_code==409
