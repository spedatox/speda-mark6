from app.profiles.base import AgentProfile
from app.prompts.loader import assemble, build_skills_manifest

PROMPT_SECTIONS = [
    "agents/optimus/01_identity.md",
    "core/04_decision_policy.md",
    "core/05_output_policy.md",
    "core/06_visual_output.md",
    "core/07_formatting.md",
    "core/08_memory.md",
]


class OptimusProfile(AgentProfile):
    """Optimus — systems, code & infrastructure.

    Optimus is architecturally unique: it will eventually communicate with its
    own standalone backend (a separate, more powerful framework). This in-process
    profile is the transitional presence — it lets the routing, sessions, and
    the frontend agent switcher work NOW while the standalone backend is built.
    Once the external Optimus backend is live, this profile stays as the
    identity/voice layer and dispatches to it via WebSocketManager.
    """

    agent_id = "optimus"
    name = "Optimus"
    domain = "systems, code & infrastructure"

    # Engineering toolkit: code hosting + research + sandbox execution +
    # document generation + watchers. Optimus is the one specialist that gets
    # sandbox access (run_command / deliver_file) — it's the engineering agent.
    tool_allowlist = [
        "github",                                # repo / code / PR review
        "tavily", "exa", "fetch",                # technical research
        "run_command", "deliver_file",            # sandbox execution
        "generate_document",                      # design docs, runbooks
        "search_history",                         # recall prior engineering context
        "manage_automations",                     # CI/deploy watchers
        "Task",                                   # multi-source engineering research
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
