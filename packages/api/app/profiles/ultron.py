from app.profiles.base import AgentProfile
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
]


class UltronProfile(AgentProfile):
    """
    Ultron — academic research & knowledge synthesis.

    A domain specialist: a narrow research/synthesis toolset and a scholarly
    voice, distinct from SPEDA's broad orchestrator profile. Authored as the
    first second agent (multi-tenant Phase 3) — proof that two agents with
    different tools and voice coexist in one process, addressed by agent_id.
    """

    agent_id = "ultron"
    name = "Ultron"
    domain = "academic research & knowledge synthesis"

    # Narrow, declarative allowlist (Rules 5 + 10): research + synthesis only —
    # no finance, automations, sandbox/code, calendar, or notifications. The
    # CapabilityRegistry enforces this; runtime-infrastructure skills (memory,
    # read_skill, use_toolset) are always available regardless.
    tool_allowlist = [
        "tavily", "exa", "arxiv", "fetch",   # MCP research servers
        "generate_document",                  # synthesis -> PDF / DOCX / PPTX
        "search_history",                     # recall prior research
        "Task",                               # parallel research sub-agents
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
