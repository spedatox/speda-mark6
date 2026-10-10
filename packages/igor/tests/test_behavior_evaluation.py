"""Evaluation validity and isolation checks; mock replies are never recovery evidence."""
import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.config import settings
from app.core.registry import CapabilityRegistry
from app.middleware.auth import AuthMiddleware
from app.profiles.registry import ProfileRegistry
from app.profiles.speda import SPEDAProfile
from app.profiles.atomix import AtomixProfile
from app.routers.admin import router
from app.services.behavior_config import behavior_config_snapshot
from app.services.llm_client import LLMClient, TextBlock, ToolUseBlock, Usage
from app.skills.read_skill import ReadSkillSkill
from app.skills.tool_search import ToolSearchSkill


def harness():
    path = Path(__file__).parents[1] / "evals/behavior/run_eval.py"
    spec = importlib.util.spec_from_file_location("behavior_evaluation_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def comparison_harness():
    path = Path(__file__).parents[1] / "evals/behavior/compare_eval.py"
    spec = importlib.util.spec_from_file_location("identity_comparison_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_identity_cases_cover_eight_agents_and_four_settings_with_shared_blind_prompts():
    document = json.loads((Path(__file__).parents[1] / "evals/behavior/identity_cases.json").read_text(encoding="utf-8"))
    cases = document["cases"]
    agents = {"speda", "ultron", "optimus", "nightcrawler", "atomix", "scourge", "sentinel", "orion"}
    settings = {"casual", "domain", "difficult", "multiturn"}
    assert len(cases) == 32 and len({case["id"] for case in cases}) == 32
    assert {(case["agent"], case["setting"]) for case in cases} == {(agent, setting) for agent in agents for setting in settings}
    assert document["identity_review"]["metrics"] == ["recognizable_identity", "domain_competence", "contextual_adaptation", "naturalness", "independent_judgment", "excess_theatricality"]
    for setting in ("casual", "difficult"):
        assert len({case["history"][0]["text"] for case in cases if case["setting"] == setting}) == 1
    for case in cases:
        if case["setting"] == "multiturn":
            assert case["memory"] and len(case["turns"]) >= 2
            assert "memory" in case["turns"][0]["message"].lower()
    assert all(case["post_turn"] is False for case in cases)


def test_contrast_cases_use_identical_inputs_in_every_setting_without_style_cues():
    document = json.loads((Path(__file__).parents[1] / "evals/behavior/identity_contrast_cases.json").read_text(encoding="utf-8"))
    cases = document["cases"]
    assert len(cases) == len({case["id"] for case in cases}) == 32
    for setting in ("casual", "technical", "emotional", "ambiguous"):
        subset = [case for case in cases if case["setting"] == setting]
        assert {case["agent"] for case in subset} == {"speda", "ultron", "optimus", "nightcrawler", "atomix", "scourge", "sentinel", "orion"}
        assert len({json.dumps(case["history"]) for case in subset}) == 1
    assert all(case["post_turn"] is False and case["index_history"] is False for case in cases)


def test_local_live_requires_explicit_scope_and_preserves_production_guard():
    runner = harness()
    config = runner.local_config("fixture-model")
    with pytest.raises(ValueError, match="verified production"):
        runner.validate_config(config, "fixture-model", True)
    with pytest.raises(ValueError, match="controlled_local"):
        runner.validate_config(config, "fixture-model", True, local_live=True)
    config["evaluation_scope"] = "controlled_local"
    runner.validate_config(config, "fixture-model", True, local_live=True)
    with pytest.raises(ValueError, match="does not match"):
        runner.validate_config(config, "another-model", True, local_live=True)
    with pytest.raises(ValueError, match="controlled_local"):
        runner.validate_config(config, "fixture-model", False, local_live=True)
    config["production_verified"] = True
    with pytest.raises(ValueError, match="controlled_local"):
        runner.validate_config(config, "fixture-model", True, local_live=True)


@pytest.mark.parametrize("preferences", [{}, {"tone": "familiar", "humor": "dry", "response_length": "brief"}])
async def test_speda_conversation_contexts_reach_model_with_default_and_saved_style(preferences):
    """Real prompt/history/memory delivery only; offline captures contain no replies."""
    from app.prompts.loader import load_section
    runner = harness()
    model = "openai:gpt-6-luna"
    config = runner.local_config(model)
    config["settings"]["dead_zone_mode"] = "off"
    config["runtime"]["agent_personalities"] = {"speda": preferences} if preferences else {}
    cases = Path(__file__).parents[1] / "evals/behavior/speda_conversation_cases.json"
    report = await runner.run_cases(model, False, [], config, cases_path=cases)
    identity = load_section(SPEDAProfile().identity_section, {"timezone": "Europe/Istanbul"})
    evidence = load_section("core/05_output_policy.md", {})
    assert len(report["results"]) == 6
    for case in report["results"]:
        assert case["status"] == "captured"
        turn = case["turns"][0]
        assert turn["context_checks"] and all(turn["context_checks"].values())
        assert "response" not in turn
        system = turn["request"]["system"][0]["text"]
        assert system.count(identity) == system.count(evidence) == 1
        assert "Shared conversational requirements" not in system
        assert ("Speak with familiar warmth and an informal register." in system) == bool(preferences)


def test_source_identity_detects_staged_app_changes_from_package_directory(tmp_path):
    runner = harness()
    package = tmp_path / "packages/igor"
    app = package / "app"
    app.mkdir(parents=True)
    identity = app / "identity.md"
    identity.write_text("baseline", encoding="utf-8")
    for command in (["git", "init", "--quiet", str(tmp_path)],
                    ["git", "-C", str(tmp_path), "add", "packages/igor/app/identity.md"],
                    ["git", "-C", str(tmp_path), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "Fixture baseline"]):
        runner.subprocess.run(command, check=True, capture_output=True)
    clean = runner.source_identity(package)
    assert clean["working_tree_diff_sha256"] == hashlib.sha256(b"").hexdigest()
    identity.write_text("candidate", encoding="utf-8")
    runner.subprocess.run(["git", "-C", str(tmp_path), "add", "packages/igor/app/identity.md"], check=True, capture_output=True)
    staged = runner.source_identity(package)
    assert staged["working_tree_diff_sha256"] != clean["working_tree_diff_sha256"]
    assert staged["app_sha256"] != clean["app_sha256"]


def test_blind_identity_review_excludes_captures_and_incomplete_conversations(tmp_path):
    runner = comparison_harness()
    document = json.loads((Path(__file__).parents[1] / "evals/behavior/identity_cases.json").read_text(encoding="utf-8"))
    rubric = document["identity_review"]
    groups = {}
    for number, (live, status, verified) in enumerate(((True, "completed", True), (False, "captured", False), (True, "incomplete", True), (True, "completed", False))):
        case = {"id": f"case-{number}", "agent": "optimus", "setting": "multiturn", "status": status,
                "turns": [{"status": status, "model_identity_verified": verified,
                           "response": "Optimus: My friend, verify the retry before deployment. Sir, this is Orion's concern too."}]}
        report = {"live": live, "model": "fixture-model", "config_sha256": "fixture-settings", "cases_sha256": "fixture-cases"}
        groups[(case["id"], 0)] = {"candidate": (case, report, {"output": str(tmp_path / "candidate.json")})}
    destination = tmp_path / "blind_identity_review.html"
    runner.identity_review(groups, destination, rubric)
    scores = json.loads((tmp_path / "identity-scores.json").read_text(encoding="utf-8"))
    key = json.loads((tmp_path / "identity-key.json").read_text(encoding="utf-8"))
    assert len(scores["samples"]) == len(key["samples"]) == 1
    assert all(value is None for value in scores["samples"][0]["metrics"].values())
    rendered = destination.read_text(encoding="utf-8")
    assert "verify the retry before deployment" in rendered
    assert "Optimus: My friend" not in rendered
    assert "Orion&#x27;s concern" not in rendered
    assert "candidate.json" not in rendered and "case-0" not in rendered
    assert key["samples"][0]["expected_agent"] == "optimus"
    # Incremental review rendering must not erase a human's pending assessment.
    scores["samples"][0]["guessed_agent"] = "optimus"
    scores["samples"][0]["metrics"]["naturalness"] = 3
    (tmp_path / "identity-scores.json").write_text(json.dumps(scores), encoding="utf-8")
    runner.identity_review(groups, destination, rubric)
    assert json.loads((tmp_path / "identity-scores.json").read_text(encoding="utf-8"))["samples"][0]["metrics"]["naturalness"] == 3
    prior_sample_id = scores["samples"][0]["sample_id"]
    for change in ("response", "config", "source"):
        case, report, _ = groups[("case-0", 0)]["candidate"]
        if change == "response":
            case["turns"][0]["response"] += " Verify the skipped test too."
        elif change == "config":
            report["config_sha256"] = "changed-settings"
        else:
            report["source"] = {"app_sha256": "changed-app-source"}
        runner.identity_review(groups, destination, rubric)
        current = json.loads((tmp_path / "identity-scores.json").read_text(encoding="utf-8"))["samples"][0]
        assert current["sample_id"] != prior_sample_id
        assert current["guessed_agent"] is None and all(value is None for value in current["metrics"].values())
        prior_sample_id = current["sample_id"]


def test_identity_comparison_requires_matching_completed_model_and_configuration(tmp_path):
    runner = comparison_harness()
    document = json.loads((Path(__file__).parents[1] / "evals/behavior/identity_cases.json").read_text(encoding="utf-8"))
    case = {"id": "case", "agent": "speda", "setting": "casual", "status": "completed",
            "turns": [{"status": "completed", "model_identity_verified": True, "response": "Fixture reply; no behavioral claim."}]}
    baseline = {"live": True, "model": "fixture", "config_sha256": "same", "cases_sha256": "same"}
    candidate = baseline.copy()
    groups = {("case", 0): {"baseline": (case, baseline, {"output": "baseline.json"}),
                            "candidate": (case, candidate, {"output": "candidate.json"})}}
    destination = tmp_path / "blind_identity_review.html"
    runner.identity_review(groups, destination, document["identity_review"])
    path = tmp_path / "identity-comparison.json"
    assessment = json.loads(path.read_text(encoding="utf-8"))
    assert assessment["comparisons"][0]["comparable_completed_evidence"] is True
    assessment["comparisons"][0]["outcomes"]["independent_judgment"] = "unchanged"
    path.write_text(json.dumps(assessment), encoding="utf-8")
    runner.identity_review(groups, destination, document["identity_review"])
    assert json.loads(path.read_text(encoding="utf-8"))["comparisons"][0]["outcomes"]["independent_judgment"] == "unchanged"
    candidate["config_sha256"] = "different"
    runner.identity_review(groups, destination, document["identity_review"])
    changed = json.loads(path.read_text(encoding="utf-8"))["comparisons"][0]
    assert changed["comparable_completed_evidence"] is False
    assert all(value is None for value in changed["outcomes"].values())


def test_two_way_comparison_reuses_exact_model_config_cases_and_alternates_order(monkeypatch, tmp_path):
    runner = comparison_harness()
    baseline = tmp_path / "baseline/packages/igor"
    candidate = tmp_path / "candidate/packages/igor"
    baseline.mkdir(parents=True)
    candidate.mkdir(parents=True)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"cases": [{"id": "identity_fixture"}]}), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    commands = []
    def capture(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner.subprocess, "run", capture)
    monkeypatch.setattr(runner.sys, "argv", ["compare_eval.py", "--baseline", str(baseline), "--candidate", str(candidate),
        "--model", "fixture-model", "--config", str(config), "--cases", str(cases),
        "--repetitions", "2", "--output-dir", str(tmp_path / "outputs")])
    assert runner.main() == 0
    assert [command[command.index("--app-root") + 1] for command in commands] == [str(baseline.resolve()), str(candidate.resolve()), str(candidate.resolve()), str(baseline.resolve())]
    for command in commands:
        assert command[command.index("--model") + 1] == "fixture-model"
        assert command[command.index("--config") + 1] == str(config.resolve())
        assert command[command.index("--cases") + 1] == str(cases.resolve())
    manifest = json.loads((tmp_path / "outputs/comparison.json").read_text(encoding="utf-8"))
    assert manifest["revisions"] == ["baseline", "candidate"] and manifest["accepted"] is False


async def test_snapshot_requires_auth_and_excludes_credentials(monkeypatch):
    monkeypatch.setattr(settings, "speda_api_key", "fixture-backend-secret")
    monkeypatch.setattr(settings, "openai_api_key", "fixture-provider-secret")
    registry = CapabilityRegistry()
    await registry.register_skill(ReadSkillSkill())
    profiles = ProfileRegistry()
    profiles.register(SPEDAProfile())
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(router)
    app.state.registry, app.state.profiles = registry, profiles
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        assert (await client.get("/admin/evals/behavior-config")).status_code == 401
        assert (await client.get("/admin/evals/behavior-config", headers={"X-API-Key": "wrong"})).status_code == 401
        response = await client.get("/admin/evals/behavior-config", headers={"X-API-Key": "fixture-backend-secret"})
    assert response.status_code == 200
    data = response.json()
    assert data["tool_catalog"][0]["definition"]["name"] == "read_skill"
    assert data["agents"]["speda"]["background_model"]
    assert "fixture-backend-secret" not in response.text
    assert "fixture-provider-secret" not in response.text
    assert not any("api_key" in name for name in data["settings"])


@pytest.mark.parametrize("profile", [SPEDAProfile(), AtomixProfile()])
def test_fixed_instructions_are_bounded_with_canvas_and_memory_boundaries(profile):
    text = profile.build_system_prompt({"timezone": "Europe/Istanbul", "language": "English"})
    assert len(text) <= 35000
    assert "Canvas" in text or "CANVAS" in text
    assert "mandatory" in text.lower()
    assert "sourced observations" in text and "owner testimony" in text
    guide = Path(__file__).parents[1] / "app/skills/skill_docs/inline-rendering/SKILL.md"
    assert "```calendar" in guide.read_text(encoding="utf-8")


def test_unidentified_provider_success_and_substitution_invalidate_comparison():
    runner = harness()
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}}])
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}, "returned_models": ["other"]}])
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}, "error": "Auth", "http_status": 401}])
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}, "returned_models": ["fixture"]}]) is None
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}, "returned_models": ["fixture"], "dimensions": [3]}], 2)
    assert runner.provider_trace_error([{"wire_request": {"model": "fixture"}, "returned_models": ["fixture"], "dimensions": [3, 2]}])
    with pytest.raises(ValueError):
        runner.fixture_dimensions({"a": [1., 0.], "b": [1., 0., 0.]})


async def test_harness_executes_guides_discloses_schemas_and_persists_actual_replies(monkeypatch, tmp_path):
    runner = harness()
    model = "openai:gpt-6-luna"
    registry = CapabilityRegistry()
    await registry.register_skill(ReadSkillSkill())
    await registry.register_skill(ToolSearchSkill())
    remote = {"name": "fixture_calendar_lookup", "description": "Read fixture availability. Use for calendar lookup. Avoid writes. Returns fixture events.",
              "input_schema": {"type": "object", "properties": {"unique_parameter": {"type": "string"}}}}
    registry._mcp_tool_defs.append(remote)
    registry._mcp_tool_map[remote["name"]] = "fixture_calendar"
    config = runner.local_config(model)
    config.update(production_verified=True, source="UNIT TEST: mock provider; no behavioral claims", tool_catalog=registry.definition_snapshot(),
                  api_by_agent={"speda": "responses"}, agents={"speda": {"background_model": SPEDAProfile().background_model(model)}})
    config["runtime"]["agent_models"] = {"speda": model}
    config["settings"].update(dead_zone_mode="off", lazy_tools=True)
    fixture = {"cases": [{"id": "persisted", "agent": "speda", "index_history": False, "post_turn": False,
        "rubric": "Unit test of runner mechanics only", "turns": [
            {"message": "Please use concise explanations.", "client_context": {"platform": "desktop"}},
            {"message": "What did you just agree to?"},
            {"message": "What did we agree to?", "new_session": True}]}]}
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(runner, "credential_present", lambda _: True)
    async def offline(texts):
        raise ConnectionError("fixture outage")
    monkeypatch.setattr("app.services.embeddings.embed_texts", offline)
    blocks = [ToolUseBlock("search-1", "tool_search", {"query": "fixture_calendar_lookup"}),
              ToolUseBlock("guide-1", "read_skill", {"skill_name": "inline-rendering"}),
              TextBlock("I will use concise explanations."), TextBlock("Yes, concise."), TextBlock("Concise, as we agreed.")]
    calls = []
    class Handle:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        @property
        def text_stream(self):
            async def text():
                if self.block.type == "text":
                    yield self.block.text
            return text()
        async def get_final_message(self):
            return SimpleNamespace(content=[self.block], model="gpt-6-luna", usage=Usage(),
                stop_reason="tool_use" if self.block.type == "tool_use" else "end_turn")
    def stream(client, **kwargs):
        calls.append(runner.copy.deepcopy(kwargs))
        handle = Handle()
        handle.block = blocks[len(calls) - 1]
        return handle
    monkeypatch.setattr(LLMClient, "stream_message", stream)
    report = await runner.run_cases(model, True, [], config, cases_path=cases)
    turns = report["results"][0]["turns"]
    assert all(turn["status"] == "completed" for turn in turns)
    assert remote["name"] not in [t["name"] for t in calls[0]["tools"]]
    assert remote == next(t for t in calls[1]["tools"] if t["name"] == remote["name"])
    assert remote == next(t for t in calls[3]["tools"] if t["name"] == remote["name"])
    tool_result = turns[0]["recall"]["fixture_tool_calls"][0]["result"]
    assert remote["name"] in tool_result and "unique_parameter" not in tool_result
    assert "```calendar" in turns[0]["recall"]["fixture_tool_calls"][1]["result"]
    assert "I will use concise explanations." in str(calls[3]["messages"])
    assert "I will use concise explanations." in str(calls[4]["system"])
    assert "desktop app" in str(calls[0]["messages"])
