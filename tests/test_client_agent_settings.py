import pytest
from httpx import ASGITransport, AsyncClient
from app.config import settings
from app.main import create_app
from tests.test_coordination import setup, reply


async def test_existing_agent_settings_and_group_authorization(memory, monkeypatch, tmp_path):
    bits = await setup(memory, [reply('Configured')])
    app = create_app()
    app.state.model_policy = bits.policy
    app.state.coordinator = bits.coordinator
    app.state.profiles = bits.profiles
    from app.core import runtime_state
    monkeypatch.setattr(runtime_state, '_STATE_FILE', tmp_path / 'ui-state.json')
    monkeypatch.setattr(runtime_state, '_cache', None)
    monkeypatch.setattr(settings, 'house_party_passphrase', 'fixture-secret')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test',
                           headers={'X-API-Key':settings.speda_api_key}) as http:
        roster = (await http.get('/agents')).json()
        assert len(roster) == 8 and all(p['status'] == 'online' for p in roster)
        changed = await http.post('/agents/models', json={'agent_id':'nightcrawler', 'model':'kimi:kimi-k2.6'})
        assert changed.status_code == 200, changed.text
        assert next(p for p in changed.json() if p['agent_id'] == 'friday')['override'] == 'kimi:kimi-k2.6'
        style = {'instructions':'Use short sentences.', 'tone':'professional', 'humor':'dry',
                 'directness':'direct', 'response_length':'brief'}
        changed = await http.post('/agents/personalities', json={'agent_id':'friday', 'settings':style})
        assert changed.status_code == 200, changed.text
        assert next(p for p in changed.json() if p['agent_id'] == 'friday')['settings'] == style
        assert (await http.post('/agents/personalities', json={'agent_id':'friday',
            'settings':{**style, 'authority':'all_tools'}})).status_code == 422
        assert (await http.get('/agents/house-party')).json() == {'engaged':False}
        assert (await http.post('/agents/house-party', json={'engaged':True, 'platform':'android',
            'passphrase':'fixture-secret'})).status_code == 409
        assert (await http.post('/agents/house-party', json={'engaged':True, 'platform':'desktop',
            'passphrase':'wrong'})).status_code == 403
        assert (await http.post('/agents/house-party', json={'engaged':True, 'platform':'desktop',
            'passphrase':'fixture-secret'})).json() == {'engaged':True}
        monkeypatch.setattr(runtime_state, '_cache', None)
        assert (await http.get('/agents/house-party')).json()['engaged']
        assert (await http.post('/agents/house-party', json={'engaged':False, 'platform':'android'})).json() == {'engaged':False}
    await bits.coordinator.shutdown()
