from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest

PROMPT_SECTIONS = [
    "agents/sentinel/01_identity.md",
    "core/04_decision_policy.md",
    "core/05_output_policy.md",
    "core/06_visual_output.md",
    "core/07_formatting.md",
    "core/08_memory.md",
    "core/09_agent_network.md",
    "core/10_environment.md",   # Mark VI ecosystem glossary (Forge/Heartbreaker/sandbox/n8n)
]


class SentinelProfile(AgentProfile):
    """Sentinel — finance & budget intelligence. Markets, holdings, budgets,
    and the numbers behind a decision, with a not-a-licensed-advisor boundary."""

    agent_id = "sentinel"
    name = "Sentinel"
    domain = "finance & budget intelligence"
    doc_theme = DocTheme(accent="#d99c44")   # signature gold — matches the UI brand

    # Unrestricted — all tools available (same as SPEDA). Previously a narrow
    # allowlist; broadened so every agent can use every registered capability.
    tool_allowlist = None

    sonnet_model = "claude-sonnet-4-6"
    haiku_model = "claude-haiku-4-5-20251001"
    background_models = {
        "openai": "openai:gpt-5-mini",
        "gemini": "gemini:gemini-2.5-flash",
        "zai": "zai:glm-4.5-air",
        "deepseek": "deepseek:deepseek-v4-flash",
    }

    def build_system_prompt(self, context_vars: dict) -> str:
        core = assemble(PROMPT_SECTIONS, context_vars)
        manifest = build_skills_manifest()
        return f"{core}\n\n{manifest}" if manifest else core
