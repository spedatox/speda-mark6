"""Owner-initiated OAuth: durable one-use state, browser binding and PKCE.

No callback accepts an API key in a URL. Its authority is a pending, authenticated
owner initiation plus the browser cookie minted by its one-use start ticket.
"""
import asyncio
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlsplit, urlunsplit
import httpx
from sqlalchemy import delete, select, update
from app.config import settings
from app.models.oauth import OAuthAttempt
from app.services.google_auth import GoogleToken
from app.services.notion_auth import NotionToken
from app.mcp.microsoft_rest import _Token as MicrosoftToken, MS_SCOPES


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class OAuthService:
    def __init__(self, sessions, connections, tokens=None):
        self.sessions, self.connections = sessions, connections
        self.tokens = tokens or {'google': GoogleToken(), 'microsoft': MicrosoftToken(), 'notion': NotionToken()}
        self.locks = {provider: asyncio.Lock() for provider in self.tokens}

    async def rotate_identity(self,provider):
        from uuid import uuid4
        from app.models.oauth import OAuthIdentity
        async with self.sessions() as db,db.begin():
            row=await db.get(OAuthIdentity,(1,provider))
            if row: row.generation=uuid4().hex
            else: db.add(OAuthIdentity(user_id=1,provider=provider,generation=uuid4().hex))

    def configuration(self, provider):
        if provider not in self.tokens:
            raise ValueError('Unknown OAuth provider')
        client_id = getattr(settings, provider + '_client_id')
        secret = getattr(settings, provider + '_client_secret')
        redirect = getattr(settings, provider + '_oauth_redirect')
        parsed = urlsplit(redirect)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path != f'/oauth/{provider}/callback'
                or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}))):
            raise ValueError('OAuth redirect must use HTTPS (HTTP allowed only on loopback) and the exact callback path')
        tenant = settings.microsoft_tenant or 'common'
        if not all(c.isalnum() or c in '-.' for c in tenant):
            raise ValueError('Invalid Microsoft tenant')
        if not client_id or not secret:
            raise ValueError('OAuth client is not configured')
        return client_id, secret, redirect, tenant

    async def login(self, provider):
        if not self.connections.external_enabled:
            raise ValueError('External integrations are disabled')
        config = self.configuration(provider)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        async with self.sessions() as db, db.begin():
            await db.execute(delete(OAuthAttempt).where((OAuthAttempt.expires_at < time.time()) |
                (OAuthAttempt.provider == provider)))
            db.add(OAuthAttempt(state_hash=digest(state), provider=provider, owner_id=1,
                expires_at=time.time() + 600, config_hash=digest(repr(config)), verifier=verifier, browser_hash=''))
        parsed = urlsplit(config[2])
        start = urlunsplit((parsed.scheme, parsed.netloc, f'/oauth/{provider}/start', urlencode({'ticket':state}), ''))
        return {'auth_url':start}

    async def start(self, provider, ticket):
        config = self.configuration(provider)
        if not self.connections.external_enabled or len(ticket) > 128:
            raise ValueError('Authorization request is unavailable')
        browser = secrets.token_urlsafe(32)
        async with self.sessions() as db, db.begin():
            result = await db.execute(update(OAuthAttempt).where(OAuthAttempt.state_hash == digest(ticket),
                OAuthAttempt.provider == provider, OAuthAttempt.owner_id == 1,
                OAuthAttempt.expires_at > time.time(), OAuthAttempt.browser_hash == '',
                OAuthAttempt.config_hash == digest(repr(config))).values(browser_hash=digest(browser)).returning(OAuthAttempt.verifier))
            verifier = result.scalar_one_or_none()
        if not verifier:
            raise ValueError('Authorization request expired or already started')
        params = {'client_id':config[0], 'redirect_uri':config[2], 'response_type':'code', 'state':ticket}
        if provider in {'google','microsoft'}:
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            params.update(code_challenge=challenge, code_challenge_method='S256', prompt='consent')
        if provider == 'google':
            params.update(access_type='offline', scope=' '.join([
                'https://www.googleapis.com/auth/gmail.modify', 'https://www.googleapis.com/auth/calendar',
                'https://www.googleapis.com/auth/tasks', 'https://www.googleapis.com/auth/drive',
                'https://www.googleapis.com/auth/contacts.readonly']))
            endpoint = 'https://accounts.google.com/o/oauth2/v2/auth'
        elif provider == 'microsoft':
            params.update(scope=' '.join(MS_SCOPES), response_mode='query')
            endpoint = f'https://login.microsoftonline.com/{config[3]}/oauth2/v2.0/authorize'
        else:
            params['owner'] = 'user'
            endpoint = 'https://api.notion.com/v1/oauth/authorize'
        return endpoint + '?' + urlencode(params), browser, config[2].startswith('https:')

    async def callback(self, provider, state, browser, code, error):
        if provider not in self.locks:
            raise ValueError('Unknown OAuth provider')
        async with self.locks[provider]:
            config = self.configuration(provider)
            if not self.connections.external_enabled or not state or not browser or max(len(state),len(browser)) > 128:
                raise ValueError('Authorization request is unavailable')
            # Commit consumption before an exchange. A timeout never repeats a code.
            async with self.sessions() as db, db.begin():
                result = await db.execute(delete(OAuthAttempt).where(OAuthAttempt.state_hash == digest(state),
                    OAuthAttempt.provider == provider, OAuthAttempt.owner_id == 1,
                    OAuthAttempt.expires_at > time.time(), OAuthAttempt.browser_hash == digest(browser),
                    OAuthAttempt.config_hash == digest(repr(config))).returning(OAuthAttempt.verifier))
                verifier = result.scalar_one_or_none()
            if not verifier:
                raise ValueError('Authorization request expired, changed or already used')
            if error or not code or len(code) > 8192:
                raise ValueError('Authorization was denied or returned no usable code')
            data = {'grant_type':'authorization_code', 'code':code, 'redirect_uri':config[2]}
            try:
                async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                    if provider == 'notion':
                        response = await client.post('https://api.notion.com/v1/oauth/token',
                            auth=(config[0], config[1]), json=data)
                    else:
                        data.update(client_id=config[0], client_secret=config[1], code_verifier=verifier)
                        endpoint = 'https://oauth2.googleapis.com/token' if provider == 'google' else f'https://login.microsoftonline.com/{config[3]}/oauth2/v2.0/token'
                        response = await client.post(endpoint, data=data)
                    response.raise_for_status()
                    token = response.json()
                if not isinstance(token.get('access_token'), str) or not token['access_token']:
                    raise ValueError('Missing access token')
                if provider != 'notion' and (not isinstance(token.get('refresh_token'), str) or not token['refresh_token']):
                    raise ValueError('Missing refresh token')
                expires = int(token.get('expires_in', 3600))
                if expires <= 0:
                    raise ValueError('Invalid token expiry')
            except Exception:
                raise ValueError('Token exchange failed; start sign-in again') from None
            if config != self.configuration(provider):
                raise ValueError('OAuth configuration changed; start sign-in again')
            from app.core import runtime_state
            await self.rotate_identity(provider)
            cached = self.tokens[provider]
            # A concurrent token refresh cannot overwrite a new login/disconnect.
            async with cached._lock:
                if provider == 'notion':
                    runtime_state.set_notion_credentials({**token, 'expires_at':time.time() + expires if token.get('expires_in') else None})
                else:
                    getattr(runtime_state, 'set_' + provider + '_refresh_token')(token['refresh_token'])
                cached.clear()
                if provider != 'notion':
                    cached._access, cached._exp = token['access_token'], time.time() + expires
                cached._credential_hash = cached._fingerprint()
                cached._failed_until = 0
            connected = await self.connect_vendor(provider)
            return f'{provider.title()} signed in; {connected} enabled service(s) connected.'

    async def connect_vendor(self, provider):
        from app.services.connections import clone_connection
        from app.mcp.google_rest import build_google_clients
        from app.mcp.microsoft_rest import build_microsoft_clients
        from app.mcp.servers import build_notion_client
        cached = self.tokens[provider]
        clients = (build_google_clients(token_provider=cached) if provider == 'google' else
                   build_microsoft_clients(token_provider=cached) if provider == 'microsoft' else
                   [build_notion_client(cached)])
        enabled = set(settings.mcp_enabled.split(','))
        count = 0
        async with self.connections.lock:
            preferences = self.connections.store.read().get('active', {})
            for client in clients:
                name = client.server_name
                client.allowed_agents = frozenset({'speda','ultron','optimus','friday','atomix','scourge','sentinel','orion'})
                client.allowed_users = frozenset({1})
                self.connections.builtin_factories[name] = lambda c=client: clone_connection(c)
                if ('all' in enabled or name in enabled) and preferences.get(name, True):
                    count += bool(await self.connections.activate(name, client))
        return count

    async def disconnect(self, provider):
        if provider not in self.locks:
            raise ValueError('Unknown OAuth provider')
        async with self.locks[provider]:
            await self.rotate_identity(provider)
            async with self.sessions() as db, db.begin():
                await db.execute(delete(OAuthAttempt).where(OAuthAttempt.provider == provider))
            from app.core import runtime_state
            cached = self.tokens[provider]
            async with cached._lock:
                if provider == 'notion':
                    runtime_state.set_notion_credentials({})
                else:
                    getattr(runtime_state, 'set_' + provider + '_refresh_token')('')
                cached.clear()
                cached._failed_until = 0
            async with self.connections.lock:
                names = [name for name in self.connections.registry._mcp_clients
                    if name.startswith('google_') and provider == 'google' or
                    name == 'microsoft_outlook' and provider == 'microsoft' or name == 'notion' and provider == 'notion']
                for name in names:
                    await self.connections.registry.drop_mcp_server(name)
            return {'connected':False}

    def status(self, provider):
        from app.core import runtime_state
        if provider not in self.tokens:
            raise ValueError('Unknown OAuth provider')
        saved = bool(runtime_state.get_notion_credentials().get('access_token')) if provider == 'notion' else bool(getattr(runtime_state, 'get_' + provider + '_refresh_token')())
        names = ('notion',) if provider == 'notion' else ('microsoft_outlook',) if provider == 'microsoft' else ('google_gmail','google_calendar','google_tasks','google_drive','google_people')
        live = [self.connections.registry._mcp_clients.get(name) for name in names]
        return {'connected':saved, 'credential_present':saved, 'services_live':sum(bool(c and c._connected and getattr(c,'active',True)) for c in live)}
