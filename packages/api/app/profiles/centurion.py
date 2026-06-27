from app.profiles.base import AgentProfile
from app.prompts.loader import assemble, build_skills_manifest

PROMPT_SECTIONS = [
    "agents/centurion/01_identity.md",
    "core/04_decision_policy.md",
    "core/05_output_policy.md",
    "core/06_visual_output.md",
    "core/07_formatting.md",
    "core/08_memory.md",
]


class CenturionProfile(AgentProfile):
    """Centurion — cyber security. Defensive, authorized security work for the
    owner's own assets: CVE/threat intelligence, exposure assessment, hardening."""

    agent_id = "centurion"
    name = "Centurion"
    domain = "cyber security"

    # Security domain: CVE/threat intelligence + advisory research + repo/dep
    # review + security watchers + synthesis. cve_intelligence is Centurion's
    # primary MCP server. No finance, no health, no sandbox/code execution.
    # Runtime skills (memory/read_skill/use_toolset) are always available.
    tool_allowlist = [
        "cve_intelligence",             # CVE / vulnerability intelligence (primary)
        "tavily", "exa", "fetch",       # advisories, security news, sources
        "github",                       # repo / dependency security review
        "generate_document",            # assessments, remediation plans
        "search_history",               # recall prior security context
        "manage_automations",           # advisory / CVE watchers
        "Task",                         # multi-source threat research
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
