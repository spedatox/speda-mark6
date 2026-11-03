from dataclasses import replace

import pytest

from app.config import Settings
from app.profiles.speda import SPEDAProfile
from app.schemas.llm import PolicyDocument, ThinkingRequest
from app.services.model_policy import ModelPolicy, parse_model_ref, thinking_wire


def service(memory):
    return ModelPolicy(memory.sessions, SPEDAProfile(), Settings(_env_file=None))


async def test_save_revision_conflict_and_reload(memory):
    policy = service(memory)
    doc = PolicyDocument(main_model="kimi:kimi-k3", models={"kimi:kimi-k3": ThinkingRequest(mode="effort", value="low")})
    assert (await policy.save(1, 0, doc))["revision"] == 1
    with pytest.raises(ValueError, match="conflict"):
        await policy.save(1, 0, doc)
    fresh = service(memory)
    applied = await fresh.resolve()
    assert applied.spec.ref == "kimi:kimi-k3"
    assert applied.wire == {"reasoning_effort": "low"}
    assert applied.revision == 1
    assert (await policy.snapshot(2))[0] == 0


async def test_display_never_changes_compute_and_turn_snapshot_is_stable(memory):
    policy = service(memory)
    doc = PolicyDocument(models={"kimi:kimi-k2.6": ThinkingRequest(mode="enabled")})
    await policy.save(1, 0, doc)
    first = await policy.resolve(model="kimi:kimi-k2.6")
    await policy.save(1, 1, doc.model_copy(update={"show_thinking": True}))
    second = await policy.resolve(model="kimi:kimi-k2.6")
    assert first.wire == second.wire == {"thinking": {"type": "enabled"}}
    assert not first.show_thinking and second.show_thinking
    old = await policy.resolve(model=first.spec.ref, snapshot=(first.revision, first.snapshot))
    assert old.revision == 1 and not old.show_thinking


async def test_native_controls_and_unknown_models_are_honest(memory):
    policy = service(memory)
    with pytest.raises(ValueError, match="cannot disable"):
        await policy.resolve(model="kimi:kimi-k3", request=ThinkingRequest(mode="off"))
    with pytest.raises(ValueError, match="Unsupported native"):
        await policy.resolve(model="kimi:kimi-k2.6", request=ThinkingRequest(mode="effort", value="high"))
    unknown = await policy.resolve(model="ollama:local:8b")
    assert unknown.spec.control == "none" and unknown.wire == {}
    with pytest.raises(ValueError, match="no declared"):
        await policy.resolve(model="ollama:local:8b", request=ThinkingRequest(mode="enabled"))
    assert parse_model_ref("ollama:local:8b") == ("ollama", "local:8b")
    with pytest.raises(ValueError):
        parse_model_ref("typo:model")


async def test_precedence_inheritance_background_stays_on_owner_provider(memory):
    policy = service(memory)
    await policy.save(1, 0, PolicyDocument(main_model="kimi:kimi-k3",
        models={"kimi:kimi-k3": ThinkingRequest(mode="effort", value="max")},
        roles={"interactive": ThinkingRequest(mode="effort", value="high")},
        agents={"speda": ThinkingRequest(mode="effort", value="low")}))
    agent = await policy.resolve()
    assert agent.wire["reasoning_effort"] == "low" and agent.source == "agent"
    turn = await policy.resolve(request=ThinkingRequest(mode="effort", value="max"))
    assert turn.wire["reasoning_effort"] == "max" and turn.source == "turn"
    worker = await policy.resolve(model=turn.spec.ref, request=ThinkingRequest(mode="inherit"), parent=turn)
    assert worker.wire == turn.wire and worker.source == "parent"
    assert (await policy.resolve(role="background")).spec.ref == "kimi:kimi-k3"


async def test_every_advertised_native_effort_has_an_exact_mapping(memory):
    policy = service(memory)
    for spec in policy.specs().values():
        for value in spec.efforts:
            wire = thinking_wire(spec, ThinkingRequest(mode="effort", value=value))
            assert value in str(wire)
        if spec.can_disable:
            assert thinking_wire(spec, ThinkingRequest(mode="off"))


async def test_bad_persistent_policy_is_not_acknowledged(memory):
    policy = service(memory)
    with pytest.raises(ValueError):
        await policy.save(1, 0, PolicyDocument(main_model="kimi:kimi-k3", roles={"interactive": ThinkingRequest(mode="off")}))
    assert (await policy.snapshot())[0] == 0


async def test_anthropic_budget_must_be_native_and_bounded(memory):
    policy = service(memory)
    spec = policy.descriptor("claude-haiku-4-5-20251001")
    with pytest.raises(ValueError):
        thinking_wire(spec, ThinkingRequest(mode="budget", value=32))
    applied = await policy.resolve(model=spec.ref, request=ThinkingRequest(mode="budget", value=2048))
    assert applied.wire == {"thinking": {"type": "enabled", "budget_tokens": 2048}}


async def test_explicit_openai_gateway_protocol_and_native_off(memory):
    policy = service(memory)
    from app.schemas.llm import ModelSpec
    spec = ModelSpec(ref="openai:gateway-model", protocol="chat", control="effort",
        efforts=("low", "high"), can_disable=True)
    document = PolicyDocument(descriptors=[spec], models={spec.ref: ThinkingRequest(mode="off")})
    await policy.save(1, 0, document)
    applied = await policy.resolve(model=spec.ref)
    assert applied.spec.protocol == "chat" and applied.wire == {"reasoning_effort": "none"}
