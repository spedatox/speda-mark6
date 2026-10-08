"""Capture real orchestrator inputs and live conversations for human comparison.

Live runs require an explicit, verified production configuration. Credentials
come from the application's normal configuration; they never enter artifacts.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import logging
import subprocess
import sys
from contextlib import ExitStack
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


CONFIG_FIELDS = (
    "agent_language", "chat_max_output_tokens", "thinking_visible_enabled",
    "anthropic_thinking_enabled", "thinking_default_effort",
    "anthropic_thinking_budget_low_tokens", "anthropic_thinking_budget_tokens",
    "anthropic_thinking_budget_high_tokens", "prompt_cache_ttl",
    "prompt_cache_conversation_ttl", "ace_enabled", "recall_translate_queries",
    "recall_translation_model", "relevant_recall_enabled", "relevant_recall_limit",
    "relevant_recall_max_chars", "relevant_recall_min_query_chars", "dead_zone_mode",
    "llm_main_model", "llm_background_model", "llm_fallback_chain",
)
RUNTIME_FIELDS = (
    "agent_models", "model_thinking", "budget_mode", "house_party", "agent_sources",
)
APP_ROOT = Path(__file__).resolve().parents[2]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def serializable(value):
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return copy.deepcopy(value)
    return {k: serializable(v) for k, v in vars(value).items() if not k.startswith("_")}


def local_config(model):
    from app.config import settings
    from app.core import runtime_state
    return {
        "model": model,
        "production_verified": False,
        "source": "Local configuration; not verified against production",
        "settings": {key: getattr(settings, key) for key in CONFIG_FIELDS},
        "runtime": {
            "agent_models": runtime_state.get_agent_models(),
            "model_thinking": runtime_state.get_model_thinking(),
            "budget_mode": runtime_state.get_budget_mode(),
            "house_party": runtime_state.get_house_party(),
            "agent_sources": runtime_state.get_agent_sources(),
        },
        # Copy actual model-boundary definitions/catalogs when checking a
        # production tool-enabled request. No external tool handler is loaded.
        "tool_definitions_by_agent": {},
        "tool_index_by_agent": {},
        "api_by_agent": {},
    }


def validate_config(config, model, live):
    if config.get("model") != model:
        raise ValueError("Configuration model does not match --model; no substitution permitted")
    missing = set(CONFIG_FIELDS) - config.get("settings", {}).keys()
    missing |= set(RUNTIME_FIELDS) - config.get("runtime", {}).keys()
    if missing:
        raise ValueError(f"Configuration is incomplete: {', '.join(sorted(missing))}")
    unknown = config["settings"].keys() - set(CONFIG_FIELDS)
    unknown |= config["runtime"].keys() - set(RUNTIME_FIELDS)
    if unknown:
        raise ValueError("Use only the non-secret fields in the configuration template")
    if live and (config.get("production_verified") is not True or not config.get("source")):
        raise ValueError("Live comparison requires verified production settings and their source")


def request_route(request):
    from app.services.llm_client import parse_model_ref, _use_responses_api
    provider, model = parse_model_ref(request["model"])
    api = "anthropic_messages" if provider == "anthropic" else (
        "responses" if _use_responses_api(provider, model, request) else "chat_completions")
    return {"provider": provider, "model": model, "api": api}


def credential_present(model):
    from app.config import settings
    from app.services.llm_client import parse_model_ref
    provider, _ = parse_model_ref(model)
    key = getattr(settings, f"{provider}_api_key", "")
    return bool(key and key != "not-set")


def source_identity(app_root):
    files = {str(p.relative_to(app_root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((app_root / "app").rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    try:
        revision = subprocess.check_output(["git", "-C", str(app_root), "rev-parse", "HEAD"], text=True).strip()
        diff = subprocess.check_output(["git", "-C", str(app_root), "diff", "--", "packages/igor/app"])
    except subprocess.CalledProcessError:
        revision, diff = None, b""
    return {"app_root": str(app_root), "revision": revision, "app_files": files,
            "app_sha256": digest(files), "working_tree_diff_sha256": hashlib.sha256(diff).hexdigest()}


async def run_cases(model: str, live: bool, case_ids: list[str], config=None, output=None) -> dict:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    import app.models  # noqa: F401
    from app.config import settings
    from app.core import runtime_state
    from app.core.context import AgentContext
    from app.core.orchestrator import AgentOrchestrator
    from app.core.registry import CapabilityRegistry
    from app.core.session_manager import SessionManager
    from app.database import Base
    from app.models.memory_file import MemoryFile
    from app.models.message import Message
    from app.models.session import Session
    from app.models.user import User
    from app.profiles.optimus import OptimusProfile
    from app.profiles.registry import ProfileRegistry
    from app.profiles.speda import SPEDAProfile
    from app.profiles.ultron import UltronProfile
    from app.services import language
    from app.services.llm_client import LLMClient, blocks_to_dicts, parse_model_ref
    from app.skills.memory import MemoryRecallCache

    class Captured(Exception):
        pass

    class UnavailableFixtureTool(Exception):
        pass

    class FixtureRegistry(CapabilityRegistry):
        def __init__(self, agent):
            super().__init__()
            self.agent = agent

        def list_tools(self, *args, **kwargs):
            return copy.deepcopy(config.get("tool_definitions_by_agent", {}).get(self.agent, []))

        def tool_index(self, **kwargs):
            return config.get("tool_index_by_agent", {}).get(self.agent, "")

        async def execute(self, name, args, context, **kwargs):
            # Interrupt the evaluation rather than supply a fabricated result
            # or allow the model to act on a real account/host.
            raise UnavailableFixtureTool(name)

    class ObservedRawStream:
        def __init__(self, raw, trace):
            self.raw, self.trace = raw, trace

        async def __aiter__(self):
            async for item in self.raw:
                final = getattr(item, "response", None)
                returned = getattr(final or item, "model", None)
                if returned and returned not in self.trace["returned_models"]:
                    self.trace["returned_models"].append(returned)
                yield item

        async def close(self):
            await self.raw.close()

    class ObservedStream:
        def __init__(self, cm, trace):
            self.cm, self.trace = cm, trace

        async def __aenter__(self):
            self.stream = await self.cm.__aenter__()
            if hasattr(self.stream, "_params"):
                self.trace["wire_request"] = serializable(self.stream._params)
            if hasattr(self.stream, "_raw") and self.trace["route"]["provider"] != "anthropic":
                self.stream._raw = ObservedRawStream(self.stream._raw, self.trace)
            return self

        @property
        def text_stream(self):
            return self.stream.text_stream

        def event_stream(self):
            # The orchestrator tests presence of this method. Use its original
            # tagged iterator when available, otherwise tag visible text only.
            async def tagged():
                if hasattr(self.stream, "event_stream"):
                    async for item in self.stream.event_stream():
                        yield item
                else:
                    async for delta in self.stream.text_stream:
                        yield "text", delta
            return tagged()

        async def get_final_message(self):
            final = await self.stream.get_final_message()
            self.trace["content"] = blocks_to_dicts(final.content)
            self.trace["stop_reason"] = final.stop_reason
            self.trace["usage"] = serializable(final.usage)
            returned = getattr(final, "model", None)
            if returned and returned not in self.trace["returned_models"]:
                self.trace["returned_models"].append(returned)
            return final

        async def __aexit__(self, *args):
            return await self.cm.__aexit__(*args)

    class RecordedClient(LLMClient):
        def __init__(self, result):
            super().__init__()
            self.result = result

        def _chain(self, ref):
            if ref != model:
                raise ValueError("Orchestrator changed the evaluation model")
            # Authentication/rate-limit errors stay errors. Never try fallback.
            return [parse_model_ref(ref)]

        def stream_message(self, **kwargs):
            request = copy.deepcopy(kwargs)
            self.result.setdefault("request", request)
            trace = {"request": request, "route": request_route(request), "returned_models": []}
            self.result["model_calls"].append(trace)
            if not live:
                raise Captured()
            if request["model"] != model:
                raise ValueError("Model changed; comparison stopped")
            expected_api = config.get("api_by_agent", {}).get(self.result["agent"])
            if expected_api != trace["route"]["api"]:
                raise ValueError("Production API route is missing or differs; supply actual tool definitions")
            return ObservedStream(super().stream_message(**kwargs), trace)

    config = copy.deepcopy(config or local_config(model))
    validate_config(config, model, live)
    cases_path = Path(__file__).with_name("cases.json")
    cases = json.loads(cases_path.read_text(encoding="utf-8"))["cases"]
    if set(case_ids) - {c["id"] for c in cases}:
        raise ValueError("Unknown case id")
    profiles = ProfileRegistry()
    for profile in (SPEDAProfile(), OptimusProfile(), UltronProfile()):
        profiles.register(profile)
    report = {"source": source_identity(APP_ROOT), "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(), "config": config,
              "config_sha256": digest(config), "model": model, "live": live,
              "note": "Only actual completed replies are behavioral evidence. Context/language checks are diagnostics.",
              "limitations": ["Synthetic memory and earlier execution fixtures; no real external actions.",
                              "A live tool request stops the case as incomplete rather than inventing a result.",
                              "No post-turn extraction, compaction or background scheduling is evaluated."],
              "results": []}

    def checkpoint():
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with ExitStack() as scope:
        for key, value in config["settings"].items():
            scope.enter_context(patch.object(settings, key, value))
        scope.enter_context(patch.object(runtime_state, "_cache", copy.deepcopy(config["runtime"])))
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        auth_failed = False
        try:
            for case in cases:
                if case_ids and case["id"] not in case_ids:
                    continue
                result = {"id": case["id"], "agent": case["agent"], "rubric": case["rubric"],
                          "fixture": case, "model_calls": [], "events": [], "status": "captured"}
                report["results"].append(result)
                if live and (auth_failed or not credential_present(model)):
                    result.update(status="blocked", error="Matching provider credential unavailable or rejected")
                    checkpoint()
                    continue
                allocated = profiles.get(case["agent"]).allocate_model("user")
                result["production_allocated_model"] = allocated
                if live and allocated != model:
                    result.update(status="blocked", error="Production profile allocation differs from requested model")
                    checkpoint()
                    continue
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.drop_all)
                    await conn.run_sync(Base.metadata.create_all)
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    db.add(User(id=1, name="Fixture owner", timezone="Europe/Istanbul"))
                    session = Session(user_id=1, agent_id=case["agent"], triggered_by="user", model_used=model)
                    db.add(session)
                    await db.flush()
                    for path, content in case.get("memory", {}).items():
                        db.add(MemoryFile(user_id=1, path=path, content=content))
                    for i, item in enumerate(case["history"]):
                        content = [{"type": "text", "text": item["text"]}]
                        if item.get("tools"):
                            content.append({"type": "_speda_meta", "tools": item["tools"]})
                        db.add(Message(session_id=session.id, role=item["role"], content=content,
                                       created_at=datetime(2026, 10, 8, 9, i, tzinfo=timezone.utc)))
                    await db.commit()
                    history = await SessionManager().load_history(db, session.id)
                    context = AgentContext(agent_id=case["agent"], user_id=1, session_id=session.id,
                        request_id=case["id"], triggered_by="user", trigger_payload={}, output_mode="respond",
                        model=model, system_prompt="", conversation_history=history, db=db, timezone="Europe/Istanbul")
                    result["language"] = {"configured_default": settings.agent_language,
                        "explicit_request": case.get("requested_language"),
                        "scope": case.get("language_scope", "reply"),
                        "expected": case.get("requested_language", settings.agent_language),
                        "instruction": case.get("language_instruction")}
                    client = RecordedClient(result)
                    orchestrator = AgentOrchestrator(FixtureRegistry(case["agent"]), client, profiles, MemoryRecallCache())
                    try:
                        async for event in orchestrator.run(context):
                            result["events"].append(json.loads(event.to_json()))
                        result["response"] = "".join(e["data"] for e in result["events"] if e["type"] == "chunk")
                        result["status"] = "completed" if any(e["type"] == "done" for e in result["events"]) else "incomplete"
                        returned = [m for call in result["model_calls"] for m in call["returned_models"]]
                        result["model_identity_verified"] = bool(returned) and all(m == parse_model_ref(model)[1] for m in returned)
                        if not result["model_identity_verified"]:
                            result.update(status="incomplete", error="Provider model identity missing or different; manual verification required")
                        result["language"]["diagnostic_fragments"] = language.detect_leak(
                            result["response"], target=result["language"]["expected"])
                        result["language"]["review_note"] = "Lexical hints only; inspect requested artifact separately from its surrounding prose."
                    except Captured:
                        pass
                    except UnavailableFixtureTool as exc:
                        result.update(status="incomplete", blocked_tool=str(exc))
                    except Exception as exc:
                        result.update(status="failed", error=type(exc).__name__, http_status=getattr(exc, "status_code", None))
                        auth_failed = result["http_status"] in (401, 403)
                    if "request" in result:
                        serialized = json.dumps(result["request"], ensure_ascii=False, sort_keys=True)
                        result.update(input_sha256=digest(result["request"]),
                            stored_fixture_paths=list(case.get("memory", {})),
                            context_checks={text: text in serialized for text in case.get("required_context", [])})
                    # Never discard partial streamed text on a failure.
                    if live:
                        result.setdefault("response", "".join(e["data"] for e in result["events"] if e["type"] == "chunk"))
                    checkpoint()
                    logging.info("%s: %s", case["id"], result["status"])
                    for compat in client._compat_clients.values():
                        await compat.close()
                    await client._anthropic.client.close()
        finally:
            await engine.dispose()
    checkpoint()
    return report


def main():
    global APP_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", type=Path, help="Verified, non-secret production settings snapshot")
    parser.add_argument("--write-local-config", type=Path, help="Save a template without claiming production equivalence")
    parser.add_argument("--app-root", type=Path, default=APP_ROOT, help="Igor package directory from the revision being evaluated")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    APP_ROOT = args.app_root.resolve()
    sys.path.insert(0, str(APP_ROOT))
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.write_local_config:
        args.write_local_config.parent.mkdir(parents=True, exist_ok=True)
        args.write_local_config.write_text(json.dumps(local_config(args.model), ensure_ascii=False, indent=2), encoding="utf-8")
        return
    if not args.output or (args.live and not args.config):
        parser.error("--output is required; --live also requires --config")
    config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
    asyncio.run(run_cases(args.model, args.live, args.case, config, args.output))


if __name__ == "__main__":
    main()
