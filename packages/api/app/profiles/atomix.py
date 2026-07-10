from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest

# Atomix-specific identity + shared policy sections. Only the identity differs
# from the other agents; the decision / output / visual / formatting / memory
# policies are common and reused from prompts/core/.
PROMPT_SECTIONS = [
    "agents/atomix/01_identity.md",
    "core/04_decision_policy.md",   # Tavily->Exa search priority + sub-agent policy
    "core/05_output_policy.md",
    "core/06_visual_output.md",
    "core/07_formatting.md",
    "core/08_memory.md",
    "core/09_agent_network.md",
    "core/10_environment.md",   # Mark VI ecosystem glossary (Forge/Heartbreaker/sandbox/n8n)
]


class AtomixProfile(AgentProfile):
    """
    Atomix — the owner's personal health & wellness (NOT system/server health).

    A domain specialist: fitness, nutrition, sleep, habits, evidence-based health
    guidance, with a hard not-a-doctor boundary. Narrow allowlist and a distinct
    voice, addressed by agent_id (multi-tenant).
    """

    agent_id = "atomix"
    name = "Atomix"
    domain = "personal health & wellness (the owner's health)"
    doc_theme = DocTheme(accent="#3fae74")   # signature green — matches the UI brand

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
