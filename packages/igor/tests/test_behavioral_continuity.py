"""Pipeline regression evidence; live personality is reviewed separately."""
import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.session_manager import SessionManager
from app.database import Base
from app.models.memory_file import MemoryFile
from app.models.message import Message
from app.models.session import Session
from app.services.chat_history import execution_receipts
from app.services.compaction import _extract_text, _run_compaction
from app.services.relevant_recall import initial_recall_query, salient_evidence
from app.skills.memory import MemoryRecallCache, recall_for_context, relevant_files_for_message


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def meta(*tools):
    return [{"type": "_speda_meta", "tools": list(tools), "thinking": "PRIVATE_REASONING"}]


async def test_visible_owner_turns_have_exact_citable_sources_without_history_search(db):
    from app.services.memory_admission import resolve_evidence

    session = Session(user_id=1, agent_id="atomix", triggered_by="user", model_used="test")
    db.add(session)
    await db.flush()
    owner = Message(session_id=session.id, role="user", content=[
        {"type": "text", "text": "I am waiting for the application result."},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "test"}},
    ], created_at=datetime(2026, 10, 9, 13, 38))
    assistant = Message(session_id=session.id, role="assistant", content="You may hear back tomorrow.",
                        created_at=datetime(2026, 10, 9, 13, 39))
    db.add_all([owner, assistant])
    await db.commit()
    manager = SessionManager()
    loaded = await manager.load_history(db, session.id)
    label = loaded[0]["content"][0]["text"]
    assert f"owner source: message:{owner.id}" in label
    assert loaded[0]["content"][1:] == owner.content
    assert loaded[1]["content"] == assistant.content
    assert await manager.load_history(db, session.id) == loaded
    assert initial_recall_query([loaded[0]]) == "I am waiting for the application result."
    evidence = await resolve_evidence(db, 1, [{
        "ref": f"message:{owner.id}", "quote": owner.content[0]["text"],
    }], session_id=session.id)
    assert evidence[0]["ref"] == f"message:{owner.id}"
    assert evidence[0]["quote"] == owner.content[0]["text"]
    with pytest.raises(ValueError, match="owner"):
        await resolve_evidence(db, 1, [{
            "ref": f"message:{assistant.id}", "quote": assistant.content,
        }], session_id=session.id)
    # The transport annotation never becomes part of the stored owner quotation.
    assert "owner source" not in str(owner.content)


@pytest.mark.parametrize("triggered_by", ["n8n", "agent"])
async def test_automated_history_has_no_owner_citation_labels(db, triggered_by):
    session = Session(user_id=1, agent_id="atomix", triggered_by=triggered_by, model_used="test")
    db.add(session)
    await db.flush()
    db.add(Message(session_id=session.id, role="user", content="Check pending work."))
    await db.commit()
    assert "owner source" not in str(await SessionManager().load_history(db, session.id))


async def test_next_turn_has_execution_outcome_even_if_prose_denies_it(db):
    session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
    db.add(session)
    await db.flush()
    content = [{"type": "text", "text": "I didn't call Atomix."}, *meta(
        {"name": "dispatch_agent", "input": {"agent": "atomix", "secret": "PRIVATE_INPUT"},
         "result": "Returned clinic guidance."},
        {"name": "send_telegram_file", "result": "Error: delivery failed"},
        {"name": "run_command"},
    )]
    db.add(Message(session_id=session.id, role="assistant", content=content))
    await db.commit()
    loaded = await SessionManager().load_history(db, session.id)
    text = str(loaded)
    assert "target: atomix" in text and "Returned clinic guidance" in text
    assert "delivery failed" in text and "completion unknown" in text
    assert "PRIVATE_REASONING" not in text and "PRIVATE_INPUT" not in text
    assert "_speda_meta" not in text
    # Neither the stored prose nor the display metadata is rewritten.
    assert content[0]["text"] == "I didn't call Atomix."


async def test_receipts_are_bounded_across_history_and_ordinary_messages_unchanged(db):
    session = Session(user_id=1, agent_id="optimus", triggered_by="user", model_used="test")
    db.add(session)
    await db.flush()
    for i in range(12):
        db.add(Message(session_id=session.id, role="assistant", content=meta(
            {"name": f"tool_{i}", "result": "x" * 3000}), created_at=datetime(2026, 10, 8, 10, i)))
    db.add(Message(session_id=session.id, role="assistant", content="Ordinary reply.",
                   created_at=datetime(2026, 10, 8, 11)))
    await db.commit()
    loaded = await SessionManager().load_history(db, session.id)
    receipts = [b["text"] for m in loaded if isinstance(m["content"], list) for b in m["content"]
                if b["type"] == "text" and b["text"].startswith("[RECORDED TOOL")]
    assert sum(map(len, receipts)) <= 14400
    assert "tool_11" in " ".join(receipts)
    assert loaded[-1]["content"] == "Ordinary reply."


async def test_calendar_name_survives_followup_without_retrieving_memory(db):
    session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="test")
    db.add(session)
    await db.flush()
    # The useful class is in the middle of the list, beyond an old short
    # excerpt's prefix; a later workshop lookup must not displace that list.
    calendar = (
        "4 event(s):\n" + "- Workshop registration and location details.\n" * 10
        + "- 2026-10-14T09:00:00+03:00 — MIS 215 - Statistics I @ Lab 221\n"
        + "- Other scheduled classes and locations.\n" * 10
    )
    tools = meta(
        {"name": "use_toolset", "result": "Loaded google_calendar."},
        {"name": "calendar_list_events", "result": calendar},
        {"name": "calendar_get_event", "result": "Odoo workshop details. " * 24},
    )
    db.add(Message(session_id=session.id, role="assistant", content=[
        {"type": "text", "text": "There is a clash with MIS 215."}, *tools,
    ], created_at=datetime(2026, 10, 10, 13, 52)))
    db.add(Message(session_id=session.id, role="assistant", content="Yes, that is the clash.",
                   created_at=datetime(2026, 10, 10, 13, 53)))
    db.add(Message(session_id=session.id, role="user", content="Which class is MIS 215?",
                   created_at=datetime(2026, 10, 10, 13, 54)))
    await db.commit()
    manager = SessionManager()
    loaded = await manager.load_history(db, session.id)
    receipt = loaded[0]["content"][-1]["text"]
    assert calendar in receipt
    assert "MIS 215 - Statistics I @ Lab 221" in receipt
    assert "Odoo workshop details." in receipt
    assert "result omitted" not in receipt
    assert loaded[1]["content"] == "Yes, that is the clash."
    assert await manager.load_history(db, session.id) == loaded


def test_recent_receipts_still_bound_large_results_and_exclude_private_inputs():
    content = meta(*[
        {"name": f"tool_{i}", "input": {"secret": "PRIVATE_INPUT"},
         "result": f"Result {i}\n" + "x" * 20000 + f"\nError {i}: partial failure"}
        for i in range(8)
    ])
    receipt = execution_receipts(content, budget=12000, result_limit=3000)
    assert len(receipt) <= 12000
    assert "Error 7: partial failure" in receipt
    assert "PRIVATE_INPUT" not in receipt and "PRIVATE_REASONING" not in receipt
    assert len(execution_receipts(content)) <= 1200


async def test_compaction_receives_actions_and_unknowns_instead_of_only_prose(db, monkeypatch):
    session = Session(user_id=1, agent_id="optimus", triggered_by="user", model_used="test")
    db.add(session)
    await db.flush()
    for i in range(10):
        db.add(Message(session_id=session.id, role="assistant", content=[
            {"type": "text", "text": "Earlier conversation."}, *meta(
                {"name": "run_command", "result": "exit_code: 0; keypair created"},
                {"name": "dispatch_agent"})], created_at=datetime(2026, 10, 8, 10, i)))
    await db.commit()
    from app.config import settings
    monkeypatch.setattr(settings, "compaction_keep_tokens", 0)
    create = AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text="Preserved summary")]))
    monkeypatch.setattr("app.services.llm_client.LLMClient.create_message", create)
    assert await _run_compaction(db, session, "test", "test", force=True)
    prompt = create.call_args.kwargs["messages"][0]["content"]
    assert "keypair created" in prompt and "completion unknown" in prompt
    assert "PRIVATE_REASONING" not in prompt
    assert "keypair created" in _extract_text(meta({"name": "run_command", "result": "keypair created"}))


async def test_coordinate_pair_recalls_established_place_without_dorm_keyword(db):
    db.add_all([
        MemoryFile(user_id=1, path="/memories/general/10-26/places.md", content=(
            "# Places\n\n## Earlier\n" + "Other place. " * 200 +
            "\n\n## Residence\n39.95048, 32.88605 is the owner's dormitory.\n\n## Later\n" + "Other record. " * 200)),
        MemoryFile(user_id=2, path="/memories/general/10-26/places.md",
                   content="# Places\n39.95048, 32.88605 is another user's private address."),
    ])
    await db.commit()
    query = "Check my calendar and location [39.95048,32.88605]"
    result = await relevant_files_for_message(1, db, query)
    assert "owner's dormitory" in result
    assert "private address" not in result
    assert len(result) <= 2800


async def test_truncated_preference_is_selectively_retrievable(db):
    content = ("# Prohibitions\n\n## First\n" + "Routine reporting preference. " * 100 +
               "\n\n## Conversations\nDo not turn workplace frustration into an unsolicited crisis questionnaire.\n\n"
               "## Last\n" + "Formatting preference. " * 100)
    path = "/memories/dossier/prohibitions.md"
    db.add(MemoryFile(user_id=1, path=path, content=content))
    await db.commit()
    standing = await recall_for_context(1, db, cache=MemoryRecallCache())
    assert "Do not turn workplace frustration" not in standing
    recalled = await relevant_files_for_message(1, db, "workplace frustration crisis questionnaire")
    assert "Do not turn workplace frustration" in recalled
    assert len(recalled) <= 2800


def test_weekday_stamp_and_coordinate_tool_evidence_do_not_obscure_recall():
    query = initial_recall_query([{"role": "user", "content": "[Thu 2026-10-08 12:00 +03] What is here?"}])
    assert query == "What is here?"
    assert "coordinates 39.95048, 32.88605" in salient_evidence({"latitude": 39.95048, "longitude": 32.88605})
    assert len(execution_receipts(meta({"name": "read", "result": "x" * 5000}), budget=200)) <= 200


async def test_controlled_cases_capture_the_repaired_model_input(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "evals/behavior/run_eval.py"
    spec = importlib.util.spec_from_file_location("behavior_eval", path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    monkeypatch.setattr("app.core.registry.CapabilityRegistry.dead_zone_active", AsyncMock(return_value=False))
    from app.config import settings
    # The harness reads local settings for capture and restores its overrides.
    report = await harness.run_cases("openai:gpt-6-luna", False, [
        "known_location", "atomix_action_awareness", "ssh_action_awareness", "partial_execution",
        "calendar_code_followup"])
    assert len(report["results"]) == 5
    for case in report["results"]:
        assert all(case["context_checks"].values()), case["id"]
        assert "response" not in case  # input evidence does not claim live behavior


def test_behavior_harness_refuses_unverified_configuration():
    path = Path(__file__).resolve().parents[1] / "evals/behavior/run_eval.py"
    spec = importlib.util.spec_from_file_location("behavior_eval", path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    config = harness.local_config("openai:gpt-6-luna")
    with pytest.raises(ValueError, match="verified production"):
        harness.validate_config(config, "openai:gpt-6-luna", True)
    with pytest.raises(ValueError, match="substitution"):
        harness.validate_config(config, "deepseek:another-model", False)


async def test_prompt_comparison_changes_one_group_and_restores_loader(tmp_path, monkeypatch):
    import json
    from app.prompts import loader

    path = Path(__file__).resolve().parents[1] / "evals/behavior/run_eval.py"
    spec = importlib.util.spec_from_file_location("behavior_eval", path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    monkeypatch.setattr("app.core.registry.CapabilityRegistry.dead_zone_active", AsyncMock(return_value=False))
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"cases": [{"id": "atomix_context", "agent": "atomix",
        "history": [{"role": "assistant", "text": "Which detail?", "tools": [
            {"name": "record_observation", "result": "Nothing saved; scope remains unclear."}]},
            {"role": "user", "text": "I already answered that."}], "rubric": "Use the actual answer."}]}))
    replacement = tmp_path / "memory.md"
    replacement.write_text("Controlled memory section for {timezone}.")
    original = loader.load_section
    before = await harness.run_cases("openai:gpt-6-luna", False, [], cases_path=cases)
    after = await harness.run_cases("openai:gpt-6-luna", False, [], cases_path=cases,
                                   section_override=("core/08_memory.md", replacement))
    assert loader.load_section is original
    a, b = before["results"][0]["request"], after["results"][0]["request"]
    assert a["messages"] == b["messages"] and "Nothing saved" in str(b["messages"])
    assert {k: v for k, v in a.items() if k != "system"} == {k: v for k, v in b.items() if k != "system"}
    assert a["system"][1:] == b["system"][1:]
    old = original("core/08_memory.md", {"timezone": "Europe/Istanbul"})
    assert a["system"][0]["text"].replace(old, "Controlled memory section for Europe/Istanbul.") == b["system"][0]["text"]
    assert after["instruction_comparison"]["section"] == "core/08_memory.md"
    with pytest.raises(ValueError, match="existing section"):
        await harness.run_cases("openai:gpt-6-luna", False, [], cases_path=cases,
                               section_override=("../../outside.md", replacement))


async def test_live_harness_preserves_settings_and_explicit_language(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from app.config import settings
    from app.services.llm_client import LLMClient, TextBlock, Usage

    path = Path(__file__).resolve().parents[1] / "evals/behavior/run_eval.py"
    spec = importlib.util.spec_from_file_location("behavior_eval", path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    model = "openai:gpt-6-luna"
    config = harness.local_config(model)
    config.update(production_verified=True, source="Mocked unit-test configuration")
    config["settings"].update(agent_language="en", dead_zone_mode="off",
        thinking_default_effort="high", thinking_visible_enabled=True,
        chat_max_output_tokens=8096, llm_fallback_chain="deepseek:another-model")
    config["runtime"]["agent_models"] = {"speda": model}
    config["runtime"]["model_thinking"] = {model: "high"}
    from app.core.registry import CapabilityRegistry
    from app.skills.read_skill import ReadSkillSkill
    from app.profiles.speda import SPEDAProfile
    registry = CapabilityRegistry()
    await registry.register_skill(ReadSkillSkill())
    config["tool_catalog"] = registry.definition_snapshot()
    config["agents"] = {"speda": {"background_model": SPEDAProfile().background_model(model)}}
    config["api_by_agent"] = {"speda": "responses"}
    monkeypatch.setattr(harness, "credential_present", lambda _: True)
    calls = []

    class MockStream:
        @property
        def text_stream(self):
            async def chunks():
                yield "Öğrenci siber güvenlik girişimimizi tanıtıyoruz."
            return chunks()

        async def get_final_message(self):
            return SimpleNamespace(content=[TextBlock(text="Öğrenci siber güvenlik girişimimizi tanıtıyoruz.")],
                stop_reason="end_turn", usage=Usage(), model="gpt-6-luna")

    class MockHandle:
        async def __aenter__(self):
            return MockStream()

        async def __aexit__(self, *args):
            return False

    def mock_stream(client, **kwargs):
        assert client._chain(model) == [("openai", "gpt-6-luna")]
        calls.append(harness.copy.deepcopy(kwargs))
        return MockHandle()

    monkeypatch.setattr(LLMClient, "stream_message", mock_stream)
    original_language = settings.agent_language
    cases = json.loads(path.with_name("cases.json").read_text(encoding="utf-8"))
    selected = next(case for case in cases["cases"] if case["id"] == "turkish_draft")
    selected.update(post_turn=False, index_history=False)
    fixture = tmp_path / "cases.json"
    fixture.write_text(json.dumps({"cases": [selected]}), encoding="utf-8")
    report = await harness.run_cases(model, True, ["turkish_draft"], config, cases_path=fixture)
    result = report["results"][0]
    assert result["status"] == "completed"
    assert calls[0] == result["request"]
    assert calls[0]["max_tokens"] == 8096
    assert calls[0]["reasoning_effort"] == "high"
    assert result["language"]["expected"] == "tr"
    assert result["language"]["scope"] == "artifact"
    assert not result["language"]["diagnostic_fragments"]
    assert result["model_identity_verified"]
    assert settings.agent_language == original_language
