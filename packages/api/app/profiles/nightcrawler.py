from app.profiles.base import AgentProfile
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

    # Investigation toolkit: broad web search + browser automation for
    # surveillance + watchers + synthesis. Playwright runs container-isolated
    # (CVE-2025-9611, internal network only — see CLAUDE.md Security). No finance,
    # no security CVE tooling, no sandbox/code. Runtime skills always available.
    tool_allowlist = [
        "tavily", "exa", "brave_search", "fetch",   # web search + retrieval
        "playwright",                                 # browser surveillance (isolated)
        "arxiv",                                      # research sources
        "generate_document",                          # intelligence briefs
        "search_history",                             # recall prior findings
        "manage_automations",                         # web/feed surveillance watchers
        "Task",                                       # multi-source investigations
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
