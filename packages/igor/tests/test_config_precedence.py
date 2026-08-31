# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Where a setting's value comes from when several sources disagree.

The bug these guard against was silent and production-only: the managed override
file (Settings → Configuration in Heartbreaker) was listed in `env_file=`, which
ranks it below `os.environ` — and docker-compose's `env_file:` hands the
deployment .env to the container as real environment variables, not as a file.
So every key present in both was decided by the deployment, the Configuration tab
did nothing, and it all looked correct under bare uvicorn where the deployment
.env really is just a file.

The invariant: the owner's explicit choice outranks whatever the deployment
supplies, except for the handful of keys the deployment must own outright.
"""

import pytest

import app.config as cfg


@pytest.fixture
def settings_cls(tmp_path, monkeypatch):
    """Point the managed-env path at a temp file and hand back a factory for a
    fresh Settings instance.

    Deliberately does NOT reload app.config: several modules import `settings`
    lazily inside functions (app/legion/roster.py:160 among them), so rebinding
    the module mid-session hands them a different object than the one other tests
    have monkeypatched. Patching _MANAGED_ENV is enough — read_managed_env()
    resolves it at call time.
    """

    def _build(managed: str | None):
        path = tmp_path / "managed.env"
        if managed is not None:
            path.write_text(managed, encoding="utf-8")
        monkeypatch.setattr(cfg, "_MANAGED_ENV", path)
        # Never let a real .env next to the test runner leak in via env_file=".env".
        monkeypatch.chdir(tmp_path)
        return cfg

    return _build


def test_managed_env_outranks_real_environment_variables(settings_cls, monkeypatch):
    """The compose case: deployment value arrives via os.environ, owner set a
    different one in the UI. The owner wins."""
    monkeypatch.setenv("TAVILY_API_KEY", "from-deployment")
    cfg = settings_cls('TAVILY_API_KEY="from-the-config-tab"\n')

    assert cfg.Settings().tavily_api_key == "from-the-config-tab"


def test_environment_still_supplies_keys_the_owner_never_touched(settings_cls, monkeypatch):
    """The managed file only holds keys the owner has edited. Everything else
    must still fall through to the deployment."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-deployment")
    cfg = settings_cls('TAVILY_API_KEY="from-the-config-tab"\n')

    assert cfg.Settings().anthropic_api_key == "from-deployment"


def test_deployment_owned_keys_are_never_overridable(settings_cls, monkeypatch):
    """docker-compose.yml pins DATABASE_URL through `environment:` precisely so it
    outranks the deployment .env. The managed file must not be able to repoint the
    database — it lives in a bind-mounted directory that system_ops can write."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:////root/.speda/speda.db")
    cfg = settings_cls('DATABASE_URL="postgresql://elsewhere/steal"\n')

    assert cfg.Settings().database_url == "sqlite+aiosqlite:////root/.speda/speda.db"
    assert "DATABASE_URL" in cfg._DEPLOYMENT_OWNED


def test_managed_values_are_coerced_to_their_declared_type(settings_cls):
    """The file is all strings; bools and ints must still land as bools and ints."""
    cfg = settings_cls('BUDGET_MODE="false"\nSYSTEM_OPS_TIMEOUT="90"\n')
    s = cfg.Settings()

    assert s.budget_mode is False
    assert s.system_ops_timeout == 90


def test_absent_managed_file_is_not_an_error(settings_cls, monkeypatch):
    """Dev boxes and fresh deployments have no managed file at all."""
    monkeypatch.setenv("TAVILY_API_KEY", "from-deployment")
    cfg = settings_cls(None)

    assert cfg.Settings().tavily_api_key == "from-deployment"


def test_quoted_values_round_trip_through_the_writer(settings_cls):
    """write_managed_env quotes and escapes; the source must unquote identically,
    or a secret containing a quote silently becomes a different secret."""
    cfg = settings_cls("")
    awkward = 'a "quoted" \\ value'
    cfg.write_managed_env({"TAVILY_API_KEY": awkward})

    assert cfg.read_managed_env()["TAVILY_API_KEY"] == awkward
    assert cfg.Settings().tavily_api_key == awkward
