import httpx
import pytest
from app.config import Settings, settings
from app.schemas.llm import ProviderError
from app.skills.hisar import HisarSkill
from app.skills.transit import BusArrivalsSkill
from app.services import transit


async def test_hisar_deposit_confines_writes_and_holds_unknown_delivery(memory, monkeypatch):
    monkeypatch.setattr(settings, 'hisar_machine_token', 'fixture-only')
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            calls.append(kwargs)
            raise httpx.ReadTimeout('after dispatch')
    monkeypatch.setattr('app.skills.hisar.httpx.AsyncClient', Client)
    skill = HisarSkill()
    for folder in ['/Documents', '/Speda/../../etc', '/SpedaElsewhere', '/Forge/../Documents']:
        result = await skill.execute({'action':'deposit', 'path':folder, 'filename':'report.md', 'content':'text'}, memory.context())
        assert result.startswith('Refused:')
    assert not calls
    with pytest.raises(ProviderError, match='unknown'):
        await skill.execute({'action':'deposit', 'path':'/Speda', 'filename':'report.md', 'content':'text'}, memory.context())
    assert len(calls) == 1


async def test_transit_uses_existing_browser_and_preserves_live_board(memory, monkeypatch):
    called = []
    monkeypatch.setattr(transit.browser_service, 'configured', lambda: True)
    async def act(actions):
        called.append(actions)
        return {'text':'Otobüs Nerede?\n413\nANKARA ROUTE\n06 ABC 123, [413], Hız: 30 km, Engelli\n3 dk\n4/10\n1.2.3.4'}
    monkeypatch.setattr(transit.browser_service, 'act', act)
    result = await BusArrivalsSkill().execute({'stop_number':'12345'}, memory.context())
    assert '413' in result and '3 dk' in result and len(called) == 1
    assert await transit.stop_status('../escape') is None and len(called) == 1


def test_legacy_workspace_environment_maps_to_native_configuration(monkeypatch):
    monkeypatch.delenv('PROJECT_WORKSPACE_ROOT', raising=False)
    monkeypatch.setenv('FORGE_WORKSPACE_ROOT', '/opt/hisar/vault/Forge/workspaces')
    assert Settings(_env_file=None).project_workspace_root == '/opt/hisar/vault/Forge/workspaces'
    assert Settings(_env_file=None, project_workspace_root='explicit').project_workspace_root == 'explicit'
