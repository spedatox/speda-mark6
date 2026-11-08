"""Canvas is a per-turn presentation contract, never an authority or memory write."""
import json
import pytest
from pydantic import ValidationError
from app.config import Settings, settings
from app.core.surface import validate
from app.profiles.registry import ProfileRegistry
from app.schemas.client_compat import ClientChat
from app.services.client_compat import ClientCompatibility
from app.services.voice import Voice
from tests.conftest import context
from tests.test_coordination import setup, reply


def test_all_agents_get_visual_first_canvas_only_when_selected():
    profiles = ProfileRegistry()
    variables = {'current_datetime': '2026-10-27', 'timezone': 'Europe/Istanbul'}
    for aid, profile in profiles.profiles.items():
        normal = profiles.build(profile.snapshot(), variables)
        canvas = profiles.build(profile.snapshot(), {**variables, 'canvas': True, 'canvas_max_panels': 24})
        assert 'CANVAS MODE IS ACTIVE' not in normal
        assert 'CANVAS MODE IS ACTIVE' in canvas
        assert 'at most 24 authored panels' in canvas
        assert 'maximum useful visualization' in canvas
        assert ('### Ultron: visual tutoring' in canvas) == (aid == 'ultron')
    tutoring = profiles.build(profiles.require('ultron').snapshot(), {**variables, 'canvas': True})
    for requirement in ('6–10 purposeful panels', 'worked', 'sliders', 'misconception', 'wait; adapt'):
        assert requirement in tutoring
    assert "Spader's cadence" in tutoring
    assert 'CANVAS MODE IS ACTIVE' not in profiles.build_worker({**variables, 'canvas': True})


def test_canvas_status_and_panel_capacity_are_configurable_without_enabling_paid_speech():
    config = Settings(_env_file=None, azure_speech_key='', openai_api_key='', elevenlabs_api_key='', voice_canvas_max_panels=18)
    status = Voice(None, None, None, config).status()
    assert status['canvas'] == {'enabled': True, 'max_panels': 18}
    assert status['configured'] is False and status['budget_required'] is True
    config.voice_canvas_enabled = False
    assert Voice(None, None, None, config).status()['canvas']['enabled'] is False
    for limit in (0, 33):
        with pytest.raises(ValidationError): validate({'platform': 'web', 'canvas': True, 'canvas_max_panels': limit})
    assert 'canvas_instructions' not in validate({'canvas': True, 'canvas_instructions': 'Untrusted override'})


async def test_orchestrator_applies_server_cap_and_does_not_brief_workers_or_unsupported_clients(memory, monkeypatch):
    bits = await setup(memory, [])
    monkeypatch.setattr(settings, 'voice_canvas_enabled', True)
    monkeypatch.setattr(settings, 'voice_canvas_max_panels', 12)
    ctx = context(agent_id='ultron')
    ctx.extra['client_surface'] = validate({'platform': 'desktop', 'canvas': True, 'canvas_max_panels': 24})
    assert 'at most 12 authored panels' in bits.engine.build_system_prompt(ctx)
    assert 'Native Mark VI artifacts' not in bits.engine.build_system_prompt(ctx)
    ctx.extra['client_surface']['native_artifacts'] = True
    assert 'Native Mark VI artifacts' in bits.engine.build_system_prompt(ctx)
    ctx.extra['client_surface']['canvas_max_panels'] = 4
    assert 'at most 4 authored panels' in bits.engine.build_system_prompt(ctx)
    ctx.task_role = 'worker'
    assert 'CANVAS MODE IS ACTIVE' not in bits.engine.build_system_prompt(ctx)
    assert 'Native Mark VI artifacts' not in bits.engine.build_system_prompt(ctx)
    ctx.task_role = 'interactive'
    ctx.extra['client_surface']['platform'] = 'android'
    assert 'CANVAS MODE IS ACTIVE' not in bits.engine.build_system_prompt(ctx)
    assert 'Native Mark VI artifacts' in bits.engine.build_system_prompt(ctx)
    ctx.extra['client_surface']['platform'] = 'desktop'
    monkeypatch.setattr(settings, 'voice_canvas_enabled', False)
    assert 'CANVAS MODE IS ACTIVE' not in bits.engine.build_system_prompt(ctx)
    await bits.coordinator.shutdown()


async def test_real_turn_gets_ultron_canvas_brief_and_next_chat_turn_does_not(memory, monkeypatch):
    monkeypatch.setattr(settings, 'voice_canvas_enabled', True)
    monkeypatch.setattr(settings, 'voice_canvas_max_panels', 24)
    bits = await setup(memory, [reply('A visual explanation.'), reply('A short chat reply.')])
    adapter = ClientCompatibility(bits.coordinator)
    first = await adapter.start(1, 'ultron', ClientChat(message='Teach me Bayes theorem', client_context={
        'platform': 'desktop', 'voice': True, 'canvas': True, 'canvas_max_panels': 24, 'native_artifacts': True}))
    await bits.coordinator.live[first['run_id']]
    call = bits.client.calls[0]
    assert 'CANVAS MODE IS ACTIVE' in call['system']
    assert '### Ultron: visual tutoring' in call['system']
    assert 'Native Mark VI artifacts' in call['system']
    assert 'This reply will be spoken' in json.dumps(call['messages'])
    assert 'canvas presentation is deferred' not in json.dumps(call['messages'])
    assert first['run_id'] not in bits.coordinator._surfaces
    second = await adapter.start(1, 'ultron', ClientChat(message='Thanks', session_id=first['session_id']))
    await bits.coordinator.live[second['run_id']]
    assert 'CANVAS MODE IS ACTIVE' not in bits.client.calls[1]['system']
    assert 'Native Mark VI artifacts' not in bits.client.calls[1]['system']
    assert 'client telemetry' not in json.dumps(bits.client.calls[1]['messages'])
    await bits.coordinator.shutdown()
