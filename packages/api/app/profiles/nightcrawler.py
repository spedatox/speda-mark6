from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest

PROMPT_SECTIONS = [
    "agents/nightcrawler/01_identity.md",
    "core/04_decision_policy.md",
    "core/05_output_policy.md",
    "core/06_visual_output.md",
    "core/07_formatting.md",
    "core/08_memory.md",
]


class NightCrawlerProfile(AgentProfile):
    """NightCrawler — OSINT, web surveillance & research. Finds, monitors, and
    corroborates public-web information, lawfully and from open sources only."""

    agent_id = "nightcrawler"
    name = "NightCrawler"
    domain = "OSINT, web surveillance & research"
    doc_theme = DocTheme(accent="#9165e6")   # signature violet — matches the UI brand

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
