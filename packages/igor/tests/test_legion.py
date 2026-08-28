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
    """Isolate worker-model resolution from the deployment it runs on.

    `resolve_worker_model` consults the owner's persisted per-legionnaire pins
    at priority 2 — ahead of the explicit tool param and the effort policy — and
    `get_legion_models()` reads real runtime state off disk. These tests assert
    the POLICY, so they have to start from no pins; otherwise the suite passes
    or fails depending on what the owner happens to have configured, which is
    how it started failing here the moment real pins were set.

    The `pins` fixture below overrides this to test the pin path itself.
    """
    monkeypatch.setattr(settings, "legion_model_override", "")
    monkeypatch.setattr("app.core.runtime_state.get_legion_models", lambda: {})


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


# ── Owner's per-legionnaire pins ─────────────────────────────────────────────

@pytest.fixture
def pins(monkeypatch):
    """Swap the persisted per-worker pins for an in-memory dict."""
    store: dict[str, str] = {}
    monkeypatch.setattr("app.core.runtime_state.get_legion_models", lambda: dict(store))
    return store


def test_pin_beats_effort_policy(pins):
    pins["scout"] = "gemini:gemini-2.5-flash"
    m = resolve_worker_model(LEGION_ROSTER["scout"], None, "zai:glm-4.6", _Profile())
    assert m == "gemini:gemini-2.5-flash"


def test_pin_beats_the_models_explicit_choice(pins):
    # The pin is the owner's cost policy, not a hint — an explicit tool param
    # must not be able to route around it.
    pins["analyst"] = "openai:gpt-5-mini"
    m = resolve_worker_model(LEGION_ROSTER["analyst"], "zai:glm-4.6", "claude-sonnet-4-6", _Profile())
    assert m == "openai:gpt-5-mini"


def test_pin_is_per_worker(pins):
    pins["scout"] = "gemini:gemini-2.5-flash"
    m = resolve_worker_model(LEGION_ROSTER["researcher"], None, "openai:gpt-5.2", _Profile())
    assert m == "openai:gpt-5-mini"  # untouched by scout's pin


def test_deployment_override_beats_pin(pins, monkeypatch):
    monkeypatch.setattr(settings, "legion_model_override", "openai:gpt-5-mini")
    pins["analyst"] = "zai:glm-4.6"
    m = resolve_worker_model(LEGION_ROSTER["analyst"], None, "claude-sonnet-4-6", _Profile())
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


# ── Background workers report in when they finish ────────────────────────────
#
# A background worker used to end in silence: the result landed in a ticket and
# waited for someone to ask legion_status. For a job the owner deliberately sent
# away, silence is the one outcome that makes the feature useless.

from unittest.mock import AsyncMock


def _bg_ctx():
    """A context complete enough for the background path, which builds a real
    detached AgentContext (the shared _ctx above is only shaped for tool
    scoping and has no session identity)."""
    return SimpleNamespace(
        request_id="req", agent_id="speda", model="zai:glm-4.6",
        user_id=1, session_id=42, triggered_by="user",
        timezone="Europe/Istanbul",
        extra={"tool_allowlist": None},
    )


async def test_finished_background_worker_reports_to_the_deploying_agent(monkeypatch):
    import app.legion.runner as runner_mod
    from app.legion.runner import LegionRunner

    reports = []

    class _Client:
        async def create_message(self, **kw):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="the finding")],
                stop_reason="end_turn",
                usage=SimpleNamespace(
                    input_tokens=10, output_tokens=2,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0,
                ),
            )

    r = LegionRunner(_Client(), CapabilityRegistry(), None)
    monkeypatch.setattr(r, "_log_start", AsyncMock(return_value=7))
    monkeypatch.setattr(r, "_log_finish", AsyncMock())

    async def _hook(**kw):
        reports.append(kw)

    r.set_report_hook(_hook)

    ctx = _bg_ctx()
    out = await r.run_worker(
        {"description": "dig into X", "prompt": "go", "run_in_background": True}, ctx
    )
    assert "ticket #7" in out
    # The agent must not promise to chase it itself — the wake-up is automatic.
    assert "report back" in out

    for task in list(r._background):
        await task

    assert len(reports) == 1
    rep = reports[0]
    assert rep["agent_id"] == ctx.agent_id
    assert rep["status"] == "ok"
    assert rep["ticket"] == 7
    assert rep["result"] == "the finding"


async def test_inline_workers_do_not_report(monkeypatch):
    """Their result returns into the parent turn, where the agent is already
    holding it and already replying — reporting would double-send."""
    from app.legion.runner import LegionRunner

    reports = []

    class _Client:
        async def create_message(self, **kw):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="inline finding")],
                stop_reason="end_turn", usage=None,
            )

    r = LegionRunner(_Client(), CapabilityRegistry(), None)

    async def _hook(**kw):
        reports.append(kw)

    r.set_report_hook(_hook)
    out = await r.run_worker({"description": "d", "prompt": "p"}, _ctx())
    assert out == "inline finding"
    assert reports == []


async def test_a_failed_background_worker_still_reports():
    """Silence on failure is the worst outcome: the owner waits forever for a
    job that died. The report says it failed rather than not arriving."""
    from app.legion.runner import LegionRunner

    reports = []

    class _Boom:
        async def create_message(self, **kw):
            raise RuntimeError("provider exploded")

    r = LegionRunner(_Boom(), CapabilityRegistry(), None)
    r._log_start = AsyncMock(return_value=9)
    r._log_finish = AsyncMock()

    async def _hook(**kw):
        reports.append(kw)

    r.set_report_hook(_hook)
    await r.run_worker(
        {"description": "d", "prompt": "p", "run_in_background": True}, _bg_ctx()
    )
    for task in list(r._background):
        await task

    assert len(reports) == 1
    assert reports[0]["status"] == "error"
    assert "provider exploded" in reports[0]["result"]


def test_report_seed_forbids_redoing_the_work():
    """The normal trigger seed pushes hard on 'ACT, call real tools'. Applied to
    a finished job that makes the agent re-run research it already paid for."""
    from app.core.trigger_runner import build_seed, trigger_meta

    payload = {
        "type": "legion_report", "worker": "researcher", "task": "dig into X",
        "result": "found Y", "status": "ok", "ticket": 3,
    }
    seed = build_seed(payload, "push")
    assert "ALREADY DONE" in seed
    assert "found Y" in seed
    assert "researcher" in seed
    assert "AUTOMATED TRIGGER" not in seed      # not the execute-a-workflow seed
    assert "use_toolset" not in seed
    # Provenance: the agent's own worker woke it, not the automation channel.
    assert trigger_meta(payload, "push")["source"] == "legion"


# ── Live progress events (the Legion panel) ──────────────────────────────────
#
# _loop must emit started → tool* → tool_result* → finished, all keyed by the
# same run id, so the frontend's SUBAGENT reducer (or a background ticket's
# LegionRunRegistry) can fold them into one live SubagentRun.

async def test_inline_worker_emits_progress_events(registry, monkeypatch):
    from app.core import runtime_state
    monkeypatch.setattr(runtime_state, "get_budget_mode", lambda: False)

    calls = {"n": 0}

    class _Client:
        async def create_message(self, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return SimpleNamespace(
                    content=[SimpleNamespace(type="tool_use", id="tu1", name="search_thing", input={"q": "x"})],
                    stop_reason="tool_use", usage=None,
                )
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="done")],
                stop_reason="end_turn", usage=None,
            )

    runner = LegionRunner(_Client(), registry, None)
    events: list[dict] = []
    out = await runner.run_worker(
        {"description": "d", "prompt": "p"}, _ctx(),
        tool_call_id="blockA", emit=events.append,
    )

    assert out == "done"
    phases = [e["phase"] for e in events]
    assert phases == ["started", "tool", "tool_result", "finished"]
    assert all(e["id"] == "blockA" for e in events)
    assert all(e["source"] == "legion" for e in events)
    assert events[0]["prompt"] == "p"
    assert events[1]["tool"] == "search_thing"
    assert events[-1]["ok"] is True
    assert events[-1]["report"] == "done"


async def test_emit_failure_never_breaks_the_worker(registry, monkeypatch):
    """A UI-progress callback is best-effort telemetry — a bug in it must not
    take down the worker it's merely watching."""
    from app.core import runtime_state
    monkeypatch.setattr(runtime_state, "get_budget_mode", lambda: False)

    class _Client:
        async def create_message(self, **kw):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="fine")],
                stop_reason="end_turn", usage=None,
            )

    runner = LegionRunner(_Client(), registry, None)

    def _boom(_event):
        raise RuntimeError("frontend callback bug")

    out = await runner.run_worker({"description": "d", "prompt": "p"}, _ctx(), emit=_boom)
    assert out == "fine"


def test_failed_report_seed_forbids_inventing_a_result():
    from app.core.trigger_runner import build_seed

    seed = build_seed(
        {"type": "legion_report", "worker": "scout", "task": "t",
         "result": "Background worker failed: boom", "status": "error"},
        "push",
    )
    assert "FAILED" in seed
    assert "Do NOT invent" in seed
