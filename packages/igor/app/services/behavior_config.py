"""Allowlisted production settings/catalog snapshot; never exports credentials."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from app.config import settings
from app.core import runtime_state
from app.services.llm_client import parse_model_ref, _use_responses_api


CONFIG_FIELDS = (
    "agent_language", "chat_max_output_tokens", "thinking_visible_enabled",
    "anthropic_thinking_enabled", "thinking_default_effort",
    "anthropic_thinking_budget_low_tokens", "anthropic_thinking_budget_tokens",
    "anthropic_thinking_budget_high_tokens", "prompt_cache_ttl", "prompt_cache_conversation_ttl",
    "ace_enabled", "ace_tactical_max_patterns", "ace_tactical_max_countermeasures",
    "ace_tactical_max_chars", "recall_translate_queries", "recall_translation_model",
    "relevant_recall_enabled", "relevant_recall_limit", "relevant_recall_max_chars",
    "relevant_recall_min_query_chars", "automatic_recall_timeout_ms", "automatic_recall_max_chars",
    "dead_zone_mode", "llm_main_model", "llm_background_model", "llm_fallback_chain",
    "embedding_model", "recall_min_similarity", "recall_message_min_similarity",
    "episodic_recap_enabled", "episodic_recall_sessions", "episodic_recall_max_chars",
    "episodic_recap_max_tokens", "memory_injected_file_max_chars", "lazy_tools", "always_on_servers",
    "memory_review_model", "memory_review_vision_model", "memory_review_max_tokens",
    "memory_review_context_chars", "memory_review_timeout_s", "auto_extract_facts",
    "auto_extract_model", "auto_extract_max_facts",
)
RUNTIME_FIELDS = ("agent_models", "model_thinking", "budget_mode", "house_party",
                  "agent_sources", "disabled_servers", "agent_personalities")


def runtime_snapshot():
    return {"agent_models": runtime_state.get_agent_models(),
        "model_thinking": runtime_state.get_model_thinking(),
        "budget_mode": runtime_state.get_budget_mode(), "house_party": runtime_state.get_house_party(),
        "agent_sources": runtime_state.get_agent_sources(),
        "disabled_servers": sorted(runtime_state.get_disabled_servers()),
        "agent_personalities": getattr(runtime_state, "get_agent_personalities", lambda: {})()}


def behavior_config_snapshot(registry, profiles):
    root = Path(__file__).parents[1]
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(root.rglob("*")) if p.is_file() and p.suffix in (".py", ".md")}
    agents, tools, indexes, apis = {}, {}, {}, {}
    for profile in profiles.roster():
        model = profile.allocate_model("user")
        definitions = registry.list_tools(allowlist=profile.tool_allowlist, agent_id=profile.agent_id,
                                          defer_loading=parse_model_ref(model)[0] == "anthropic")
        provider, name = parse_model_ref(model)
        agents[profile.agent_id] = {"model": model, "background_model": profile.background_model(model),
                                    "recall_scope": profile.episodic_recall_scope}
        tools[profile.agent_id] = definitions
        indexes[profile.agent_id] = registry.tool_index(allowlist=profile.tool_allowlist, agent_id=profile.agent_id)
        apis[profile.agent_id] = "anthropic_messages" if provider == "anthropic" else (
            "responses" if _use_responses_api(provider, name, {"tools": definitions}) else "chat_completions")
    return {"version": 2, "production_verified": True,
        "source": "Authenticated running backend snapshot " + datetime.now(timezone.utc).isoformat(),
        "model": profiles.default.allocate_model("user"), "agents": agents,
        "settings": {key: getattr(settings, key) for key in CONFIG_FIELDS}, "runtime": runtime_snapshot(),
        "tool_catalog": registry.definition_snapshot(), "tool_definitions_by_agent": tools,
        "tool_index_by_agent": indexes, "api_by_agent": apis, "app_file_hashes": hashes}
