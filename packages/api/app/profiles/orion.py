from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest

# Orion assembles his own identity + the audit procedure, then the shared policy
# sections. He deliberately keeps the same memory + agent-network protocol as the
# rest of the roster — he must speak the same file law he enforces.
PROMPT_SECTIONS = [
    "agents/orion/01_identity.md",
    "agents/orion/02_audit.md",
    "core/05_output_policy.md",
    "core/07_formatting.md",
    "core/08_memory.md",
    "core/09_agent_network.md",
]


class OrionProfile(AgentProfile):
    """
    Orion — the Mark VI custodian. Not a domain specialist for the outside world:
    his subject IS the system. He owns memory hygiene (the boundary law in
    docs/MEMORY_ARCHITECTURE.md), the nightly audit, and host maintenance via the
    Orion-only system_ops skill. Terse, procedural, reports in changelogs.

    tool_allowlist is None (full registry, matching the rest of the roster). His
    exclusive host capability is guarded at the SKILL level — system_ops declares
    restricted_to={"orion"}, so no other agent can see or call it regardless of
    their allowlist. He is a dispatch target: other agents may hand him cleanup.
    """

    agent_id = "orion"
    name = "Orion"
    domain = "Mark VI maintenance — memory custodian & host ops"
    doc_theme = DocTheme(accent="#8a7fd6")   # signature indigo

    tool_allowlist = None
    dispatch_target = True

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
