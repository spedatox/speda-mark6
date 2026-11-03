import pytest


@pytest.fixture(autouse=True)
def isolated_domain_settings(monkeypatch, tmp_path):
    from app.core import runtime_state
    monkeypatch.setattr(runtime_state, '_STATE_FILE', tmp_path / 'runtime_state.json')
    monkeypatch.setattr(runtime_state, '_cache', None)
