from app.profiles.base import AgentProfile
from app.prompts.loader import assemble, build_skills_manifest

PROMPT_SECTIONS = [
    "agents/sentinel/01_identity.md",
    "core/04_decision_policy.md",
    "core/05_output_policy.md",
    "core/06_visual_output.md",
    "core/07_formatting.md",
    "core/08_memory.md",
]


class SentinelProfile(AgentProfile):
    """Sentinel — finance & budget intelligence. Markets, holdings, budgets,
    and the numbers behind a decision, with a not-a-licensed-advisor boundary."""

    agent_id = "sentinel"
    name = "Sentinel"
    domain = "finance & budget intelligence"

    # Finance domain: market data + research + budget/market watchers + synthesis.
    # No sandbox/code, no OSINT/surveillance, no security. Runtime skills
    # (memory/read_skill/use_toolset) are always available regardless.
    tool_allowlist = [
        "alpha_vantage",                # market data — the finance MCP server
        "tavily", "exa", "fetch",       # market news, filings, research
        "generate_document",            # budget reviews, investment memos
        "search_history",               # recall prior finance discussions
        "manage_automations",           # price/budget threshold watchers
        "Task",                         # multi-source financial research
    ]

    sonnet_model = "claude-sonnet-4-6"
    haiku_model = "claude-haiku-4-5-20251001"
    background_models = {
        "openai": "openai:gpt-5-mini",
        "gemini": "gemini:gemini-2.5-flash",
    }

    def build_system_prompt(self, context_vars: dict) -> str:
        core = assemble(PROMPT_SECTIONS, context_vars)
        manifest = build_skills_manifest()
        return f"{core}\n\n{manifest}" if manifest else core
