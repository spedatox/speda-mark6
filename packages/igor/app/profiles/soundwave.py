from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest

PROMPT_SECTIONS = [
    "agents/soundwave/01_identity.md",
    "core/02_voice.md",   # shared register — see prompts/core/02_voice.md
    "core/04_decision_policy.md",
    "core/05_output_policy.md",
    "core/06_visual_output.md",
    "core/07_formatting.md",
    "core/08_memory.md",
    "core/09_agent_network.md",
    "core/10_environment.md",   # Mark VI ecosystem glossary (Forge/Heartbreaker/sandbox/n8n)
]


class SoundwaveProfile(AgentProfile):
    """Soundwave — cyber security. Defensive, authorized security work for the
    owner's own assets: CVE/threat intelligence, exposure assessment, hardening."""

    agent_id = "soundwave"
    name = "Soundwave"
    domain = "cyber security"
    doc_theme = DocTheme(accent="#d8483c", pdf_layout="operations")
    # Like Optimus, Soundwave can be backed by a Forge peer (its own Cell, with
    # outbound network for authorized scans). While a peer is connected on
    # /agents/ws/soundwave, /chat/soundwave proxies to it; offline, this
    # in-process profile answers as the identity + fallback engine.
    external_backend = True

    # Unrestricted — all tools available (same as SPEDA). Previously a narrow
    # allowlist; broadened so every agent can use every registered capability.
    tool_allowlist = None

    sonnet_model = "claude-sonnet-4-6"
    haiku_model = "claude-haiku-4-5-20251001"
    background_models = {
        "openai": "openai:gpt-5-mini",
        "gemini": "gemini:gemini-3.5-flash-lite",
        "zai": "zai:glm-4.5-air",
        "deepseek": "deepseek:deepseek-v4-flash",
    }

    def build_system_prompt(self, context_vars: dict) -> str:
        core = assemble(PROMPT_SECTIONS, context_vars)
        manifest = build_skills_manifest()
        return f"{core}\n\n{manifest}" if manifest else core
