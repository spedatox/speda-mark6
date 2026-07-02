from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest

# Ultron-specific identity + shared policy sections. Only the identity differs
# from SPEDA; the decision / output / visual / formatting / memory policies are
# common to every agent and reused from prompts/core/.
PROMPT_SECTIONS = [
    "agents/ultron/01_identity.md",
    "core/04_decision_policy.md",   # Tavily->Exa search priority + sub-agent policy
    "core/05_output_policy.md",
    "core/06_visual_output.md",
    "core/07_formatting.md",
    "core/08_memory.md",
    "core/09_agent_network.md",
]


class UltronProfile(AgentProfile):
    """
    Ultron — the owner's academic life and university/work balance.

    A domain specialist: the owner studies and works simultaneously, and Ultron
    owns that whole front — coursework, exams, study planning, assignments, and
    keeping the university/work workload balanced. Research and synthesis are
    tools in service of that, not the identity.
    """

    agent_id = "ultron"
    name = "Ultron"
    domain = "academic life & university/work balance — study, planning, coursework"
    doc_theme = DocTheme(accent="#8a93a6")   # signature slate — matches the UI brand

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
