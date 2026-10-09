"""Capture real orchestrator inputs and live conversations for human comparison.

Live runs require an explicit, verified production configuration. Credentials
come from the application's normal configuration; they never enter artifacts.
"""
import argparse
import base64
import asyncio
import copy
import hashlib
import json
import logging
import re
import importlib.util
import tempfile
import subprocess
import sys
import time
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


def evaluation_schema():
    # The runner's schema is fixed even while importing a historical app tree.
    path = Path(__file__).resolve().parents[2] / "app/services/behavior_config.py"
    spec = importlib.util.spec_from_file_location("behavior_snapshot_schema", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    schema = evaluation_schema()
    return {
        "version": 2,
        "model": model,
        "production_verified": False,
        "source": "Local configuration; not verified against production",
        "settings": {key: getattr(settings, key, None) for key in schema.CONFIG_FIELDS},
        "runtime": {
            "agent_models": runtime_state.get_agent_models(),
            "model_thinking": runtime_state.get_model_thinking(),
            "budget_mode": runtime_state.get_budget_mode(),
            "house_party": runtime_state.get_house_party(),
            "agent_sources": runtime_state.get_agent_sources(),
            "disabled_servers": sorted(runtime_state.get_disabled_servers()),
            "agent_personalities": getattr(runtime_state, "get_agent_personalities", lambda: {})(),
        },
        # Copy actual model-boundary definitions/catalogs when checking a
        # production tool-enabled request. No external tool handler is loaded.
        "tool_definitions_by_agent": {},
        "tool_index_by_agent": {},
        "api_by_agent": {},
        "tool_catalog": [],
    }


def validate_config(config, model, live):
    if config.get("model") != model:
        raise ValueError("Configuration model does not match --model; no substitution permitted")
    schema = evaluation_schema()
    fields = schema.CONFIG_FIELDS if config.get("version") == 2 else CONFIG_FIELDS
    runtime_fields = schema.RUNTIME_FIELDS if config.get("version") == 2 else RUNTIME_FIELDS
    missing = set(fields) - config.get("settings", {}).keys()
    missing |= set(runtime_fields) - config.get("runtime", {}).keys()
    if missing:
        raise ValueError(f"Configuration is incomplete: {', '.join(sorted(missing))}")
    unknown = config["settings"].keys() - set(fields)
    unknown |= config["runtime"].keys() - set(runtime_fields)
    if unknown:
        raise ValueError("Use only the non-secret fields in the configuration template")
    if live and (config.get("production_verified") is not True or not config.get("source")):
        raise ValueError("Live comparison requires verified production settings and their source")
    if live and (config.get("version") != 2 or not config.get("tool_catalog")):
        raise ValueError("Live comparison requires a version-2 production registry snapshot")


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


def provider_trace_error(traces, expected_dimensions=None):
    """Fail closed on authentication, substitution and unidentified successes."""
    for call in traces:
        request = call.get("wire_request", call.get("request", {}))
        expected = request.get("model", "").split(":", 1)[-1]
        returned = call.get("returned_models", [])
        if call.get("http_status") in (401, 403):
            return "Provider authentication rejected"
        if returned and any(name != expected for name in returned):
            return "Provider returned a different model"
        if not call.get("error") and not returned:
            return "Provider model identity missing"
        dimensions = call.get("dimensions", [])
        if dimensions and (len(set(dimensions)) != 1 or dimensions[0] <= 0
                or (expected_dimensions is not None and dimensions[0] != expected_dimensions)):
            return "Embedding vector dimensions are incompatible"
    return None


def fixture_dimensions(vectors):
    dimensions = set()
    for value in vectors.values():
        if isinstance(value, str):
            blob = base64.b64decode(value, validate=True)
            if len(blob) % 4:
                raise ValueError("Invalid float32 fixture vector")
            dimensions.add(len(blob) // 4)
        else:
            dimensions.add(len(value))
    if len(dimensions) > 1 or 0 in dimensions:
        raise ValueError("Incompatible fixture vector dimensions")
    return next(iter(dimensions), None)


def source_identity(app_root):
    files = {str(p.relative_to(app_root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((app_root / "app").rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    try:
        revision = subprocess.check_output(["git", "-C", str(app_root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        diff = subprocess.check_output(["git", "-C", str(app_root), "diff", "HEAD", "--", "app"], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        revision, diff = None, b""
    archived_verified = False
    if revision is None:
        for parent in app_root.parents:
            marker = parent / "evaluation-source.json"
            if marker.exists():
                archived = json.loads(marker.read_text(encoding="utf-8"))
                revision = archived["revision"]
                archived_verified = archived["app_sha256"] == digest(files)
                break
    return {"app_root": str(app_root), "revision": revision, "archived_source_verified": archived_verified, "app_files": files,
            "app_sha256": digest(files), "working_tree_diff_sha256": hashlib.sha256(diff).hexdigest()}


async def run_cases(model: str, live: bool, case_ids: list[str], config=None, output=None,
                    cases_path=None, section_override=None, embedding_fixture=None) -> dict:
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
    from app.profiles.atomix import AtomixProfile
    from app.profiles.registry import ProfileRegistry
    from app.profiles.speda import SPEDAProfile
    from app.profiles.ultron import UltronProfile
    from app.profiles.sentinel import SentinelProfile
    from app.profiles.nightcrawler import NightCrawlerProfile
    from app.profiles.scourge import ScourgeProfile
    from app.profiles.orion import OrionProfile
    from app.profiles.warroom import WarRoomProfile
    from app.services import language
    from app.services.llm_client import LLMClient, blocks_to_dicts, parse_model_ref
    from app.skills.memory import MemoryRecallCache
    from app.skills.base import Skill
    from app.skills.read_skill import ReadSkillSkill
    from app.skills.tool_search import ToolSearchSkill
    from app.skills.toolsets import UseToolsetSkill
    from app.skills.semantic_search import SemanticSearchSkill
    from app.skills.search_history import SearchHistorySkill
    from app.skills.memory import MemorySkill
    from app.skills.observations import SearchMemorySkill, RecordObservationSkill
    from app.skills.memory_edit import MemoryEditSkill
    from app.skills.memory_state import MemoryStateSkill
    from app.skills.memory_write import LedgerAppendSkill, RegistryUpsertSkill, NarrativeReviseSkill
    from app.skills.memory_event import MemoryEventSkill
    from app.skills.memory_graph import ExploreMemorySkill
    from app.services import embeddings as embedding_service

    class Captured(Exception):
        pass

    class UnavailableFixtureTool(Exception):
        pass

    class FixtureRegistry(CapabilityRegistry):
        def __init__(self, agent):
            super().__init__()
            self.agent = agent

        async def initialize(self):
            class DefinitionSkill(Skill):
                async def execute(self, args, context):
                    raise UnavailableFixtureTool(self.name)
                def to_tool_definition(self):
                    return copy.deepcopy(self.definition)
            actual = {s.name: s for s in (ReadSkillSkill(), ToolSearchSkill(), UseToolsetSkill(),
                SemanticSearchSkill(), SearchHistorySkill(), MemorySkill(), SearchMemorySkill(),
                RecordObservationSkill(), MemoryEditSkill(), MemoryStateSkill(), MemoryEventSkill(),
                LedgerAppendSkill(), RegistryUpsertSkill(), NarrativeReviseSkill(), ExploreMemorySkill())}
            for entry in config.get("tool_catalog", []):
                definition = entry["definition"]
                tier = entry["tier"]
                if tier == 0:
                    self.register_legion()
                elif tier == 1:
                    skill = actual.get(definition["name"], DefinitionSkill())
                    skill.definition = copy.deepcopy(definition)
                    if definition["name"] not in actual:
                        skill.name = definition["name"]
                        skill.description = definition.get("description", "")
                        skill.input_schema = definition.get("input_schema", {})
                    for attr in ("deferred", "read_only", "requires_network", "search_keywords"):
                        setattr(skill, attr, entry.get(attr, getattr(skill, attr)))
                    restricted = entry.get("restricted_to")
                    skill.restricted_to = frozenset(restricted) if restricted is not None else None
                    await self.register_skill(skill)
                elif tier == 2:
                    self._mcp_tool_defs.append(copy.deepcopy(definition))
                    self._mcp_tool_map[definition["name"]] = entry["server"]
                    if entry.get("read_only"):
                        self._mcp_read_only.add(definition["name"])
                elif tier == 3:
                    adapter = DefinitionSkill()
                    adapter.definition = definition
                    adapter.name = definition["name"]
                    self._adapters[adapter.name] = adapter
            self.allowed_handlers = set(actual) & set(self._skills)

        async def execute(self, name, args, context, **kwargs):
            # Interrupt the evaluation rather than supply a fabricated result
            # or allow the model to act on a real account/host.
            if name not in self.allowed_handlers:
                raise UnavailableFixtureTool(name)
            started = time.monotonic()
            result = await super().execute(name, args, context, **kwargs)
            context.extra.setdefault("fixture_tool_calls", []).append({
                "id": kwargs.get("tool_call_id"), "name": name,
                "input": copy.deepcopy(args), "result": result,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1)})
            return result

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
            self.started = time.monotonic()
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
            self.trace["elapsed_ms"] = round((time.monotonic() - self.started) * 1000, 1)
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
            self.started = time.monotonic()

        def _chain(self, ref):
            if ref != model:
                raise ValueError("Orchestrator changed the evaluation model")
            # Authentication/rate-limit errors stay errors. Never try fallback.
            return [parse_model_ref(ref)]

        def stream_message(self, **kwargs):
            request = copy.deepcopy(kwargs)
            self.result.setdefault("request", request)
            self.result.setdefault("pre_model_ms", round((time.monotonic() - self.started) * 1000, 1))
            self.result.setdefault("assembled_input_chars", len(json.dumps(request, ensure_ascii=False)))
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

    active_result = None
    support_clients = []

    class SDKProxy:
        """Observe the actual provider-returned identity, before normalization."""
        def __init__(self, raw, trace_factory):
            self.raw, self.trace_factory = raw, trace_factory
        def __getattr__(self, name):
            value = getattr(self.raw, name)
            if name in {"responses", "chat", "completions", "messages", "embeddings"}:
                return SDKProxy(value, self.trace_factory)
            if name != "create":
                return value
            async def create(*args, **kwargs):
                trace = self.trace_factory()
                trace["wire_request"] = serializable(kwargs)
                started = time.monotonic()
                if not live:
                    raise RuntimeError("Input capture does not call supporting providers")
                try:
                    response = await value(*args, **kwargs)
                except Exception as exc:
                    trace.update(error=type(exc).__name__, http_status=getattr(exc, "status_code", None))
                    raise
                finally:
                    trace["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
                identity = getattr(response, "model", None)
                if identity:
                    trace.setdefault("returned_models", []).append(identity)
                if hasattr(response, "data") and hasattr(response, "model"):
                    trace["dimensions"] = [len(item.embedding) for item in response.data]
                usage = getattr(response, "usage", None)
                if usage is not None:
                    trace["usage"] = serializable(usage)
                return response
            return create

    class SupportingClient(LLMClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            support_clients.append(self)
            self._anthropic.client = SDKProxy(self._anthropic.client, self.trace)
            self.current_trace = None
        def trace(self):
            return self.current_trace
        def _chain(self, ref):
            configured = set(filter(None, [model, *config["runtime"]["agent_models"].values(),
                *(config["settings"].get(k) for k in ("llm_background_model", "memory_review_model",
                    "memory_review_vision_model", "auto_extract_model", "recall_translation_model")),
                *(entry.get("background_model") for entry in config.get("agents", {}).values())]))
            if ref not in configured:
                raise ValueError("Unverified supporting model requested")
            return [parse_model_ref(ref)]
        def _compat_client(self, provider):
            return SDKProxy(super()._compat_client(provider), self.trace)
        async def create_message(self, **kwargs):
            trace = {"request": serializable(kwargs), "route": request_route(kwargs), "returned_models": []}
            self.current_trace = trace
            active_result.setdefault("supporting_calls", []).append(trace)
            response = await super().create_message(**kwargs)
            trace["content"] = blocks_to_dicts(response.content)
            return response

    config = copy.deepcopy(config or local_config(model))
    validate_config(config, model, live)
    if config["runtime"].get("agent_personalities"):
        from app.profiles.base import AgentProfile
        if not hasattr(AgentProfile, "personalization_prompt"):
            raise ValueError("This revision cannot apply the verified personality configuration; comparison invalid")
    cases_path = Path(cases_path or Path(__file__).with_name("cases.json"))
    case_document = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = case_document["cases"]
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", case["id"]) for case in cases):
        raise ValueError("Case ids must be safe, unique filename labels")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Case ids must be unique")
    if set(case_ids) - {c["id"] for c in cases}:
        raise ValueError("Unknown case id")
    profiles = ProfileRegistry()
    for profile in (SPEDAProfile(), AtomixProfile(), OptimusProfile(), UltronProfile(),
                    SentinelProfile(), NightCrawlerProfile(), ScourgeProfile(), OrionProfile(), WarRoomProfile()):
        profiles.register(profile)
    report = {"source": source_identity(APP_ROOT), "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(), "config": config,
              "config_sha256": digest(config), "model": model, "live": live,
              "note": "Only actual completed replies are behavioral evidence. Context/language checks are diagnostics.",
              "limitations": ["Isolated owner/history fixtures; real external account/host actions remain blocked.",
                              "Only actual completed live replies establish behavior; offline captures are diagnostics."],
              "results": []}
    if case_document.get("identity_review"):
        report["identity_review"] = copy.deepcopy(case_document["identity_review"])

    # Evaluation only: replace exactly one instruction group, retaining all
    # other prompt sections, tools, settings and memory. Never alter disk or
    # production configuration. Missing targets are errors, not empty sections.
    replacement = None
    if section_override:
        from app.prompts import loader
        section, replacement_path = section_override
        target = (loader.PROMPTS_DIR / section).resolve()
        if not target.is_relative_to(loader.PROMPTS_DIR.resolve()) or not target.is_file():
            raise ValueError("Prompt override must name an existing section inside app/prompts")
        replacement = Path(replacement_path).read_text(encoding="utf-8").strip()
        report["instruction_comparison"] = {
            "section": section, "original_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "replacement_sha256": hashlib.sha256(replacement.encode()).hexdigest(),
            "scope": "One section replaced for this isolated evaluation only",
        }

    def checkpoint():
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with ExitStack() as scope:
        if section_override:
            original_load = loader.load_section

            def compared_section(relative_path, context_vars=None):
                if (loader.PROMPTS_DIR / relative_path).resolve() != target:
                    return original_load(relative_path, context_vars)
                text = replacement
                for key, value in (context_vars or {}).items():
                    text = text.replace("{" + key + "}", str(value))
                return text

            scope.enter_context(patch.object(loader, "load_section", compared_section))
        for key, value in config["settings"].items():
            if hasattr(settings, key):
                scope.enter_context(patch.object(settings, key, value))
        scope.enter_context(patch.object(runtime_state, "_cache", copy.deepcopy(config["runtime"])))
        isolated = scope.enter_context(tempfile.TemporaryDirectory(prefix="speda-behavior-"))
        scope.enter_context(patch.object(runtime_state, "_STATE_FILE", Path(isolated) / "runtime_state.json"))
        isolated_url = "sqlite+aiosqlite:///" + (Path(isolated) / "evaluation.db").as_posix()
        scope.enter_context(patch.object(settings, "database_url", isolated_url))
        engine = create_async_engine(isolated_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        # Patch the source app's normal factories, including already-imported
        # references. Future imports inherit the patched database factory.
        for name, module in list(sys.modules.items()):
            if name.startswith("app.") and hasattr(module, "AsyncSessionLocal"):
                scope.enter_context(patch.object(module, "AsyncSessionLocal", factory))
        import app.services.llm_client as llm_module
        for name, module in list(sys.modules.items()):
            if name.startswith("app.") and getattr(module, "LLMClient", None) is LLMClient:
                scope.enter_context(patch.object(module, "LLMClient", SupportingClient))
        original_embedding_client = embedding_service._get_client

        def embedding_trace():
            trace = {"returned_models": []}
            active_result.setdefault("embedding_calls", []).append(trace)
            return trace

        scope.enter_context(patch.object(embedding_service, "_get_client",
            lambda: SDKProxy(original_embedding_client(), embedding_trace)))
        index_path = Path(embedding_fixture) if embedding_fixture else (
            ((output.parent if output else Path(isolated)) / "fixture-embeddings.json") if live else None)
        index_data = json.loads(index_path.read_text(encoding="utf-8")) if index_path and index_path.exists() else {
            "model": settings.embedding_model, "vectors": {}, "provider_verified": False}
        if index_data["model"] != settings.embedding_model:
            raise ValueError("Fixture embedding model differs from production; no substitution allowed")
        if live and index_data["vectors"] and not index_data.get("provider_verified"):
            raise ValueError("Fixture vectors lack verified provider identity")
        index_data["dimensions"] = fixture_dimensions(index_data["vectors"])
        auth_failed = False
        try:
            for case in cases:
                if case_ids and case["id"] not in case_ids:
                    continue
                case_result = {"id": case["id"], "agent": case["agent"], "rubric": case["rubric"],
                               "fixture": case, "turns": [], "status": "captured"}
                if case.get("setting"):
                    case_result["setting"] = case["setting"]
                report["results"].append(case_result)
                if live and (auth_failed or not credential_present(model)):
                    case_result.update(status="blocked", error="Matching provider credential unavailable or rejected")
                    checkpoint()
                    continue
                allocated = profiles.get(case["agent"]).allocate_model("user")
                case_result["production_allocated_model"] = allocated
                if live and allocated != model:
                    case_result.update(status="blocked", error="Production profile allocation differs from requested model")
                    checkpoint()
                    continue
                expected_background = config.get("agents", {}).get(case["agent"], {}).get("background_model")
                if live and profiles.get(case["agent"]).background_model(model) != expected_background:
                    case_result.update(status="blocked", error="Production supporting model configuration is missing or differs")
                    checkpoint()
                    continue
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.drop_all)
                    await conn.run_sync(Base.metadata.create_all)
                    # FTS virtual tables are outside ORM metadata. Case ids are
                    # reused, so stale text would corrupt ranking between cases.
                    from sqlalchemy import text
                    await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
                    await conn.execute(text("DROP TABLE IF EXISTS observations_fts"))
                from app.skills import semantic_search
                from app.services import observations
                for module in (semantic_search, observations):
                    if hasattr(module, "_VECTOR_CACHE"):
                        module._VECTOR_CACHE.clear()
                active_result = case_result
                sessions = SessionManager()
                memory_cache = MemoryRecallCache()
                registry = FixtureRegistry(case["agent"])
                await registry.initialize()
                async with factory() as db:
                    db.add(User(id=1, name="Fixture owner", timezone="Europe/Istanbul"))
                    await db.commit()
                    for memory_path, content in case.get("memory", {}).items():
                        db.add(MemoryFile(user_id=1, path=memory_path, content=content))
                    await db.commit()

                    async def seed_history(session, history):
                        from app.models.message_embedding import MessageEmbedding
                        from app.services.embedding_indexer import _extract_text
                        bodies = [_extract_text(item.get("content") or item.get("text", "")).strip()[:2000]
                                  for item in history]
                        missing = list(dict.fromkeys(body for body in bodies if body and digest(body) not in index_data["vectors"]))
                        if live and case.get("index_history", True):
                            for start in range(0, len(missing), 64):
                                batch = missing[start:start + 64]
                                vectors = await embedding_service.embed_texts(batch)
                                error = provider_trace_error(active_result.get("embedding_calls", []), index_data["dimensions"])
                                if error:
                                    raise ValueError(error)
                                if len(vectors) != len(batch):
                                    raise ValueError("Incomplete fixture embedding batch")
                                dimensions = {len(vector) for vector in vectors}
                                if len(dimensions) != 1 or (index_data["dimensions"] is not None
                                        and dimensions != {index_data["dimensions"]}):
                                    raise ValueError("Incompatible fixture embedding dimensions")
                                index_data["dimensions"] = next(iter(dimensions))
                                index_data["vectors"].update({digest(body): base64.b64encode(vector.tobytes()).decode("ascii")
                                                             for body, vector in zip(batch, vectors)})
                                index_data["storage"] = "float32_base64"
                                index_data["provider_verified"] = True
                            if missing:
                                index_path.parent.mkdir(parents=True, exist_ok=True)
                                index_path.write_text(json.dumps(index_data), encoding="utf-8")
                        for i, item in enumerate(history):
                            content = item.get("content") or [{"type": "text", "text": item.get("text", "")}]
                            if item.get("tools"):
                                content.append({"type": "_speda_meta", "tools": item["tools"]})
                            message = await sessions.save_message(db, session.id, item["role"], content)
                            if item.get("created_at"):
                                message.created_at = datetime.fromisoformat(item["created_at"])
                            else:
                                message.created_at = datetime(2026, 10, 8, 9, i % 60, tzinfo=timezone.utc)
                            body = _extract_text(content).strip()[:2000]
                            key = digest(body)
                            if key in index_data["vectors"] and case.get("index_history", True):
                                import numpy as np
                                value = index_data["vectors"][key]
                                blob = base64.b64decode(value) if isinstance(value, str) else np.asarray(value, dtype=np.float32).tobytes()
                                db.add(MessageEmbedding(message_id=message.id, session_id=session.id,
                                    user_id=1, agent_id=session.agent_id, role=item["role"], text=body,
                                    embedding=blob))
                                # The real embedding indexer also writes the local
                                # keyword projection. Preserve that indexed state
                                # in older revisions instead of handicapping them.
                                from app.services import lexical
                                await lexical.index_message(db, message.id, body)
                            await db.commit()

                    fixture_started = time.perf_counter()
                    try:
                        volume = int(case.get("history_volume", 0))
                        if volume:
                            archive = Session(user_id=1, agent_id=case["agent"], triggered_by="user", model_used=model)
                            db.add(archive)
                            await db.commit()
                            await seed_history(archive, [{"role": "user", "text":
                                f"Fixture archive row {i}: warehouse inventory slot {i % 97}, handoff batch {i // 97}."}
                                for i in range(volume)])
                        for prior in case.get("prior_sessions", []):
                            old = Session(user_id=1, agent_id=prior.get("agent", case["agent"]),
                                triggered_by="user", model_used=model, recap=prior.get("recap"))
                            db.add(old)
                            await db.commit()
                            await seed_history(old, prior["history"])
                        session = Session(user_id=1, agent_id=case["agent"], triggered_by="user", model_used=model)
                        db.add(session)
                        await db.commit()
                        await seed_history(session, case.get("history", []))
                    except Exception as exc:
                        case_result.update(status="incomplete", error="Fixture preparation: " + type(exc).__name__,
                                           http_status=getattr(exc, "status_code", None),
                                           fixture_preparation_elapsed_ms=round((time.perf_counter() - fixture_started) * 1000, 1))
                        auth_failed = case_result["http_status"] in (401, 403)
                        checkpoint()
                        if hasattr(memory_cache, "close"):
                            await memory_cache.close()
                        continue
                    case_result["fixture_preparation_elapsed_ms"] = round((time.perf_counter() - fixture_started) * 1000, 1)
                    steps = case.get("turns") or [{}]
                    for turn_number, step in enumerate(steps):
                        if isinstance(step, str):
                            step = {"message": step}
                        if step.get("new_session"):
                            session = Session(user_id=1, agent_id=case["agent"], triggered_by="user", model_used=model)
                            db.add(session)
                            await db.commit()
                        if "message" in step:
                            await sessions.save_message(db, session.id, "user", step["message"])
                        result = {"id": case["id"], "agent": case["agent"], "turn": turn_number,
                                  "model_calls": [], "events": [], "status": "captured"}
                        case_result["turns"].append(result)
                        active_result = result
                        history = await sessions.load_history(db, session.id)
                        from app.core.surface import annotate_last_user
                        from app.schemas.chat import ClientContext
                        client_context = step.get("client_context", case.get("client_context"))
                        if client_context:
                            annotate_last_user(history, ClientContext(**client_context),
                                getattr(profiles.get(case["agent"]), "canvas_brief", ""))
                        context = AgentContext(agent_id=case["agent"], user_id=1, session_id=session.id,
                            request_id=f"{case['id']}:{turn_number}", triggered_by="user", trigger_payload={},
                            output_mode="respond", model=model, system_prompt="", conversation_history=history,
                            db=db, timezone="Europe/Istanbul")
                        if step.get("custom_instructions"):
                            if not hasattr(context, "custom_instructions"):
                                raise ValueError("Selected revision cannot apply client personality instructions")
                            context.custom_instructions = step["custom_instructions"]
                        context.extra.update(registry=registry,
                            active_servers=sessions.get_loaded_servers(session.id),
                            loaded_tools=sessions.get_loaded_tools(session.id),
                            mark_tools_loaded=lambda names: sessions.mark_tools_loaded(session.id, names))
                        from sqlalchemy import select, func
                        from app.models.message_embedding import MessageEmbedding
                        from app.services import lexical
                        result["indexing_state"] = {
                            "messages": (await db.execute(select(func.count(Message.id)))).scalar(),
                            "embeddings": (await db.execute(select(func.count(MessageEmbedding.id)))).scalar(),
                            "lexical_message_ids": sorted(await lexical.indexed_ids(db, lexical.MESSAGES)),
                        }
                        result["language"] = {"configured_default": settings.agent_language,
                            "explicit_request": step.get("requested_language", case.get("requested_language")),
                            "scope": case.get("language_scope", "reply"),
                            "expected": step.get("requested_language", case.get("requested_language", settings.agent_language))}
                        client = RecordedClient(result)
                        orchestrator = AgentOrchestrator(registry, client, profiles, memory_cache)
                        cache_state = step.get("cache_state", case.get("cache_state", "cold" if turn_number == 0 else "retained"))
                        result["cache_state"] = cache_state
                        if cache_state == "cold":
                            if hasattr(memory_cache, "close"):
                                await memory_cache.close()
                                memory_cache.conversation_vectors.clear()
                            for module in (semantic_search, observations):
                                if hasattr(module, "_VECTOR_CACHE"):
                                    module._VECTOR_CACHE.clear()
                        if cache_state == "warm":
                            from app.skills.semantic_search import _vectors_for
                            import inspect
                            warm_started = time.monotonic()
                            if "cache" in inspect.signature(_vectors_for).parameters:
                                await _vectors_for(db, 1, memory_cache.conversation_vectors)
                            else:
                                await _vectors_for(db, 1)
                            await observations.vectors_for(db, 1)
                            result["cache_warmup_ms"] = round((time.monotonic() - warm_started) * 1000, 1)
                        started = time.monotonic()
                        client.started = started
                        try:
                            with ExitStack() as turn_scope:
                                if step.get("embedding_outage", case.get("embedding_outage", False)):
                                    async def unavailable_embedding(texts):
                                        raise ConnectionError("Evaluation fixture: query embedding outage")
                                    result["injected_outage"] = "Query embeddings only; generation settings unchanged"
                                    turn_scope.enter_context(patch.object(embedding_service, "embed_texts", unavailable_embedding))
                                    turn_scope.enter_context(patch.object(semantic_search, "embed_texts", unavailable_embedding))
                                async for event in orchestrator.run(context):
                                    result["events"].append(json.loads(event.to_json()))
                                    if event.type == "chunk" and event.data:
                                        result.setdefault("first_chunk_ms", round((time.monotonic() - started) * 1000, 1))
                            result["response"] = "".join(e["data"] for e in result["events"] if e["type"] == "chunk")
                            result["status"] = "completed" if any(e["type"] == "done" for e in result["events"]) else "incomplete"
                            returned = [m for call in result["model_calls"] for m in call["returned_models"]]
                            result["model_identity_verified"] = bool(returned) and all(m == parse_model_ref(model)[1] for m in returned)
                            if not result["model_identity_verified"]:
                                result.update(status="incomplete", error="Provider model identity missing or different")
                            if result["status"] == "completed":
                                result["conversation_elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
                                content = [{"type": "text", "text": result["response"]}]
                                if context.extra.get("fixture_tool_calls"):
                                    content.append({"type": "_speda_meta", "tools": context.extra["fixture_tool_calls"]})
                                await sessions.save_message(db, session.id, "assistant", content)
                                sessions.mark_servers_loaded(session.id, context.extra.get("active_servers", set()))
                                if step.get("post_turn", case.get("post_turn", True)):
                                    post_started = time.monotonic()
                                    from app.services.memory import run_post_turn_tasks
                                    await run_post_turn_tasks(session.id, context.request_id, 1,
                                        profiles.get(case["agent"]).background_model(model))
                                    from app.services.task_queue import queue_stats
                                    result["post_turn_queue"] = await queue_stats()
                                    result["post_turn_elapsed_ms"] = round((time.monotonic() - post_started) * 1000, 1)
                        except Captured:
                            pass
                        except UnavailableFixtureTool as exc:
                            result.update(status="incomplete", blocked_tool=str(exc))
                        except Exception as exc:
                            result.update(status="failed", error=type(exc).__name__, http_status=getattr(exc, "status_code", None))
                            auth_failed = result["http_status"] in (401, 403)
                        result["response"] = result.get("response", "".join(e["data"] for e in result["events"] if e["type"] == "chunk"))
                        result["recall"] = copy.deepcopy({k: context.extra.get(k) for k in
                            ("automatic_recall", "recall_trace", "recall_errors", "fixture_tool_calls")})
                        if "request" in result:
                            serialized = json.dumps(result["request"], ensure_ascii=False, sort_keys=True)
                            result.update(input_sha256=digest(result["request"]),
                                stored_fixture_paths=list(case.get("memory", {})),
                                context_checks={value: value in serialized for value in case.get("required_context", [])})
                        result["language"]["diagnostic_fragments"] = language.detect_leak(result["response"], target=result["language"]["expected"])
                        if not live:
                            result.pop("response", None)
                        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
                        error = provider_trace_error(result.get("supporting_calls", []) + result.get("embedding_calls", []), index_data["dimensions"])
                        if live and error:
                            result.update(status="incomplete", error=error)
                        if context.extra.get("recall_errors"):
                            result.update(status="incomplete", error="Stored vector dimensions are incompatible")
                        checkpoint()
                        for compat in client._compat_clients.values():
                            await compat.close()
                        await client._anthropic.client.close()
                        if result["status"] != "completed":
                            break
                    case_result["status"] = case_result["turns"][-1]["status"] if case_result["turns"] else "incomplete"
                    # Retain the old single-turn report fields for existing review tools.
                    if len(case_result["turns"]) == 1:
                        case_result.update(case_result["turns"][0])
                    if hasattr(memory_cache, "close"):
                        await memory_cache.close()
                    if live and output:
                        import sqlite3
                        storage = output.with_name(output.stem + "-" + case["id"] + ".sqlite3")
                        def preserve_storage():
                            with sqlite3.connect(Path(isolated) / "evaluation.db") as source:
                                with sqlite3.connect(storage) as destination:
                                    source.backup(destination)
                        await asyncio.to_thread(preserve_storage)
                        case_result["persisted_storage"] = {"path": str(storage),
                            "sha256": hashlib.sha256(storage.read_bytes()).hexdigest()}
                    checkpoint()
        finally:
            for client in support_clients:
                for compat in client._compat_clients.values():
                    await compat.close()
                await client._anthropic.client.close()
            await engine.dispose()
        if index_path and index_path.exists():
            report["embedding_fixture"] = {"path": str(index_path), "sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(), "model": index_data["model"]}
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
    parser.add_argument("--cases", type=Path, help="Built-in or private representative cases; private fixtures stay outside Git")
    parser.add_argument("--embedding-fixture", type=Path, help="Shared indexed fixture vectors for identical revision inputs")
    parser.add_argument("--section-override", nargs=2, metavar=("SECTION", "FILE"),
                        help="Controlled comparison: replace one prompt section with this file")
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
    asyncio.run(run_cases(args.model, args.live, args.case, config, args.output,
                         args.cases, args.section_override, args.embedding_fixture))


if __name__ == "__main__":
    main()
