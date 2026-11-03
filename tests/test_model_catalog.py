from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.profiles.speda import SPEDAProfile
from app.routers.models import router
from app.schemas.llm import PolicyDocument, ThinkingRequest
from app.services.client_models import catalog
from app.services.llm_client import LLMClient
from app.services.model_catalog import ModelCatalog
from app.services.model_policy import ModelPolicy


async def test_models_route_discovers_new_ids_and_retains_native_controls(memory):
    settings = Settings(_env_file=None, openai_api_key='test-key',
        openai_base_url='https://gateway.test/v1')
    policy = ModelPolicy(memory.sessions, SPEDAProfile(), settings)
    await policy.save(1, 0, PolicyDocument(models={
        'openai:gpt-6-sol': ThinkingRequest(mode='effort', value='high')}))
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == 'localhost':
            raise httpx.ConnectError('offline')
        assert request.url == 'https://gateway.test/v1/models'
        assert request.headers['Authorization'] == 'Bearer test-key'
        return httpx.Response(200, json={'data': [{'id': 'gpt-brand-new'},
            {'id': 'gpt-6-sol'}, {'id': 'text-embedding-new'}, {'id': 'gpt-audio-new'},
            {'id': 'sora-2'}, {'id': 'babbage-002'}, {'id': 'davinci-002'},
            {'id': 'gpt-completion-instruct'}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(policy, settings, http_client=http)
        discovery = ModelCatalog(client)
        app = FastAPI()
        app.state.model_policy, app.state.model_catalog = policy, discovery
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as api:
            response = await api.get('/models')
            assert response.status_code == 200
            rows = {row['id']: row for row in response.json()}
            assert set(rows) == {'openai:gpt-brand-new', 'openai:gpt-6-sol'}
            assert rows['openai:gpt-brand-new']['thinking_options'] == ['default']
            assert not rows['openai:gpt-brand-new']['thinking_supported']
            assert rows['openai:gpt-6-sol']['thinking'] == 'high'
            assert rows['openai:gpt-6-sol']['thinking_pinned']
            assert rows['openai:gpt-6-sol']['policy_revision'] == 1
            assert (await api.get('/models')).json() == response.json()
        assert len(calls) == 2  # OpenAI and local Ollama, cached across UI requests.


async def test_native_google_pagination_vertex_auth_and_compat_routes(memory):
    settings = Settings(_env_file=None, gemini_api_key='google-key', vertex_project_id='project',
        moonshot_api_key='moon-key', nvidia_api_key='nim-key', deepseek_api_key='ds-key',
        zai_api_key='z-key')
    policy = ModelPolicy(memory.sessions, SPEDAProfile(), settings)
    seen = []

    def respond(request):
        seen.append(str(request.url))
        host = request.url.host
        if host == 'generativelanguage.googleapis.com':
            assert request.headers['x-goog-api-key'] == 'google-key'
            if 'pageToken' not in request.url.params:
                return httpx.Response(200, json={'models': [{'name': 'models/gemini-new',
                    'supportedGenerationMethods': ['generateContent']}], 'nextPageToken': 'second'})
            return httpx.Response(200, json={'models': [
                {'name': 'models/embed-only', 'supportedGenerationMethods': ['embedContent']},
                {'name': 'models/gemini-another', 'displayName': 'Another Gemini'}]})
        if host == 'aiplatform.googleapis.com':
            assert request.url.path == '/v1beta1/publishers/google/models'
            assert request.headers['Authorization'] == 'Bearer vertex-token'
            return httpx.Response(200, json={'publisherModels': [
                {'name': 'publishers/google/models/gemini-vertex-new'},
                {'name': 'publishers/google/models/imagen-9'}]})
        assert request.url.path.endswith('/models')
        if host == 'api.moonshot.ai':
            assert request.headers['Authorization'] == 'Bearer moon-key'
        mid = 'vendor/new-chat-instruct' if host == 'integrate.api.nvidia.com' else 'new-chat'
        return httpx.Response(200, json={'data': [{'id': mid}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(policy, settings, http_client=http)
        client.vertex_token = AsyncMock(return_value='vertex-token')
        rows = {row['id']: row for row in await catalog(policy, discovery=ModelCatalog(client))}
    assert set(rows) == {'gemini:gemini-new', 'gemini:gemini-another',
        'vertex:gemini-vertex-new', 'kimi:new-chat', 'nvidia:vendor/new-chat-instruct',
        'deepseek:new-chat', 'zai:new-chat', 'ollama:new-chat'}
    assert rows['gemini:gemini-another']['name'] == 'Another Gemini'
    assert len(seen) == 8


async def test_anthropic_sdk_listing_and_provider_failures_are_isolated(memory):
    settings = Settings(_env_file=None, anthropic_api_key='test', openai_api_key='test',
        kimi_api_key='test', nvidia_api_key='test')
    policy = ModelPolicy(memory.sessions, SPEDAProfile(), settings)

    class Models:
        async def list(self, **kwargs):
            yield SimpleNamespace(id='claude-brand-new', display_name='New Claude')

    def respond(request):
        if request.url.host == 'api.openai.com':
            return httpx.Response(503)
        if request.url.host == 'api.moonshot.ai':
            return httpx.Response(200, json={'data': []})
        if request.url.host == 'integrate.api.nvidia.com':
            return httpx.Response(200, json={'unexpected': []})
        raise httpx.ConnectError('offline')

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(policy, settings, http_client=http)
        client._anthropic = SimpleNamespace(client=SimpleNamespace(models=Models()))
        rows = {row['id']: row for row in await catalog(policy, discovery=ModelCatalog(client))}
    assert rows['claude-brand-new']['name'] == 'New Claude'
    assert 'claude-sonnet-4-6' not in rows  # A successful listing replaces static inventory.
    assert 'openai:gpt-6-sol' in rows and 'kimi:kimi-k2.6' in rows
    assert all(row['provider'] in {'anthropic', 'openai', 'kimi'} for row in rows.values())
