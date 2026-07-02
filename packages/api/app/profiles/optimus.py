from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest

PROMPT_SECTIONS = [
    "agents/optimus/01_identity.md",
    "core/04_decision_policy.md",
    "core/05_output_policy.md",
    "core/06_visual_output.md",
    "core/07_formatting.md",
    "core/08_memory.md",
    "core/09_agent_network.md",
]


class OptimusProfile(AgentProfile):
    """Optimus — systems, code & infrastructure.

    Optimus is architecturally unique: its real engine is the standalone
    Optimus backend (the Claude Code-class coding framework), which connects to
    this backend as a WebSocket peer. While that peer is online, /chat/optimus
    turns are proxied to it and inter-agent dispatches route to it external-first
    — full agentic coding on the peer's own filesystem. This in-process profile
    is the identity/voice layer and the fallback engine whenever the peer is
    offline.
    """

    agent_id = "optimus"
    name = "Optimus"
    domain = "systems, code & infrastructure"
    doc_theme = DocTheme(accent="#2eb6ac")   # signature teal — matches the UI brand
    external_backend = True

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
