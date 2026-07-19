"""The Legion — unit tests: provider-agnostic model resolution, tool scoping,
unknown legionnaire handling, and the config alias."""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.registry import CapabilityRegistry
from app.legion.roster import (
    LEGION_ROSTER,
    TASK_TOOL_DEFINITION,
    WORKER_EXCLUDED_TOOLS,
    resolve_worker_model,
)
from app.legion.runner import LegionRunner
from app.skills.base import Skill


class _Profile:
    """Stand-in mirroring AgentProfile.background_model semantics."""

    background_models = {"zai": "zai:glm-4.5-air", "openai": "openai:gpt-5-mini"}
    haiku_model = "claude-haiku-4-5-20251001"

    def background_model(self, active_model_ref: str) -> str:
        provider, sep, _ = active_model_ref.partition(":")
        if not sep or provider not in ("openai", "gemini", "zai", "deepseek", "nvidia", "ollama"):
            return self.haiku_model
        if provider == "ollama":
            return active_model_ref
        return self.background_models.get(provider, active_model_ref)


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.setattr(settings, "legion_model_override", "")


def test_low_effort_zai_parent_gets_cheap_zai(monkeypatch):
    m = resolve_worker_model(LEGION_ROSTER["scout"], None, "zai:glm-4.6", _Profile())
    assert m == "zai:glm-4.5-air"
    assert "claude" not in m


def test_medium_effort_openai_parent_gets_cheap_openai():
    m = resolve_worker_model(LEGION_ROSTER["researcher"], None, "openai:gpt-5.2", _Profile())
    assert m == "openai:gpt-5-mini"


def test_high_effort_inherits_parent():
    m = resolve_worker_model(LEGION_ROSTER["analyst"], None, "zai:glm-4.6", _Profile())
    assert m == "zai:glm-4.6"


def test_inherit_effort_inherits_parent():
    m = resolve_worker_model(LEGION_ROSTER["general"], None, "openai:gpt-5.2", _Profile())
    assert m == "openai:gpt-5.2"


def test_low_effort_anthropic_parent_gets_haiku():
    m = resolve_worker_model(LEGION_ROSTER["judge"], None, "claude-sonnet-4-6", _Profile())
    assert m == "claude-haiku-4-5-20251001"


def test_explicit_param_beats_effort():
    m = resolve_worker_model(LEGION_ROSTER["scout"], "gemini:gemini-2.5-pro", "zai:glm-4.6", _Profile())
    assert m == "gemini:gemini-2.5-pro"


def test_override_beats_everything(monkeypatch):
    monkeypatch.setattr(settings, "legion_model_override", "openai:gpt-5-mini")
    m = resolve_worker_model(LEGION_ROSTER["analyst"], "zai:glm-4.6", "claude-sonnet-4-6", _Profile())
    assert m == "openai:gpt-5-mini"


def test_default_override_is_empty():
    # THE core fix: no deployment pin by default → provider-agnostic.
    # (Guard against the old claude-haiku hardcode sneaking back.)
    assert settings.legion_model_override == ""


# ── Tool scoping ──────────────────────────────────────────────────────────────

class _ReadOnlySkill(Skill):
    name = "search_thing"
    description = "x" * 10
    read_only = True
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args, context):  # pragma: no cover
        return "ok"


class _WriteSkill(_ReadOnlySkill):
    name = "write_thing"
    read_only = False


class _DispatchLike(_ReadOnlySkill):
    name = "dispatch_agent"
    read_only = False


@pytest.fixture
async def registry():
    r = CapabilityRegistry()
    r.register_legion()
    await r.register_skill(_ReadOnlySkill())
    await r.register_skill(_WriteSkill())
    await r.register_skill(_DispatchLike())
    return r


def _ctx():
    return SimpleNamespace(
        request_id="req", agent_id="speda", model="zai:glm-4.6",
        extra={"tool_allowlist": None},
    )


async def test_worker_never_sees_excluded_tools(registry, monkeypatch):
    from app.core import runtime_state
    monkeypatch.setattr(runtime_state, "get_budget_mode", lambda: False)
    runner = LegionRunner(None, registry, None)
    tools = runner._worker_tools(LEGION_ROSTER["general"], _ctx())
    names = {t["name"] for t in tools}
    assert names.isdisjoint(WORKER_EXCLUDED_TOOLS)
    assert "write_thing" in names  # general keeps write tools


async def test_read_only_worker_keeps_only_read_only_skills(registry, monkeypatch):
    from app.core import runtime_state
    monkeypatch.setattr(runtime_state, "get_budget_mode", lambda: False)
    runner = LegionRunner(None, registry, None)
    tools = runner._worker_tools(LEGION_ROSTER["researcher"], _ctx())
    names = {t["name"] for t in tools}
    assert "search_thing" in names
    assert "write_thing" not in names
    assert names.isdisjoint(WORKER_EXCLUDED_TOOLS)


async def test_unknown_legionnaire_is_corrective(registry):
    runner = LegionRunner(object(), registry, None)
    result = await runner.run_worker(
        {"description": "d", "prompt": "p", "legionnaire": "centurion"}, _ctx()
    )
    assert "unknown legionnaire" in result
    assert "researcher" in result  # tells the model what IS valid


# ── Tool definition shape ─────────────────────────────────────────────────────

def test_tool_definition_wire_name_and_schema():
    assert TASK_TOOL_DEFINITION["name"] == "Task"
    props = TASK_TOOL_DEFINITION["input_schema"]["properties"]
    assert {"description", "prompt", "legionnaire", "model", "run_in_background"} <= set(props)
    assert set(props["legionnaire"]["enum"]) == set(LEGION_ROSTER)
    # Rule 11: real description, not a one-liner.
    assert len(TASK_TOOL_DEFINITION["description"]) > 400


def test_legacy_env_alias(monkeypatch):
    # SUB_AGENT_MODEL in the environment still pins workers (back-compat).
    monkeypatch.setenv("SUB_AGENT_MODEL", "claude-haiku-4-5-20251001")
    from app.config import Settings
    s = Settings()
    assert s.legion_model_override == "claude-haiku-4-5-20251001"
    monkeypatch.delenv("SUB_AGENT_MODEL")
    monkeypatch.setenv("LEGION_MODEL_OVERRIDE", "openai:gpt-5-mini")
    s = Settings()
    assert s.legion_model_override == "openai:gpt-5-mini"
