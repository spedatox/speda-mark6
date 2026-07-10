import re
from pathlib import Path

from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest

_IDENTITY_PATH = Path(__file__).parent.parent / "prompts" / "core" / "01_identity.md"


def _derive_agent_name() -> str:
    """
    Single source of the agent's display name: the identity file's H1 heading.
    `# IDENTITY — SPEDA Mark VI` → "SPEDA Mark VI". Falls back to "SPEDA".
    This means editing prompts/core/01_identity.md rebrands the back-end too —
    no need to touch this file when forking an agent.
    """
    try:
        first_line = _IDENTITY_PATH.read_text(encoding="utf-8").splitlines()[0]
        match = re.match(r"#\s*IDENTITY\s*[—\-:]\s*(.+)", first_line)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return "SPEDA"


# Derived once at import — the agent's display name (used for the FastAPI title).
AGENT_NAME = _derive_agent_name()

# Core sections assembled in order.
# The skills manifest is appended dynamically — it reads SKILL.md frontmatter at
# request time so adding a new skill only requires dropping a SKILL.md file.
PROMPT_SECTIONS = [
    "core/01_identity.md",   # full identity: who/how/Superior Six/boundaries/runtime
    "core/03_capabilities.md",
    "core/04_decision_policy.md",
    "core/05_output_policy.md",
    "core/06_visual_output.md",   # always-loaded — no tool backs this, must be in core
    "core/07_formatting.md",      # math/LaTeX + currency formatting guidance
    "core/08_memory.md",          # persistent memory protocol (Anthropic memory pattern)
    "core/09_agent_network.md",   # inter-agent dispatch + House Party Protocol
    "core/10_environment.md",   # Mark VI ecosystem glossary (Forge/Heartbreaker/sandbox/n8n)
]


class SPEDAProfile(AgentProfile):
    """
    SPEDA — the orchestrator profile and primary owner-facing agent.
    One profile among several in the multi-tenant backend; addressed by
    agent_id="speda". Never put prompt content in core modules.
    """

    agent_id = "speda"
    domain = "orchestration & general executive assistance"
    doc_theme = DocTheme(accent="#36abca")   # signature cyan — matches the UI brand
    # None = full registry access. SPEDA is the orchestrator; it sees every
    # tool. The domain-specialised agents declare narrower allowlists.
    tool_allowlist = None

    name = AGENT_NAME   # derived from 01_identity.md — never hardcode here
    sonnet_model = "claude-sonnet-4-6"
    haiku_model = "claude-haiku-4-5-20251001"

    # Cheapest sensible model per non-Anthropic provider, used for background
    # tasks when the user is chatting on that provider (see base.background_model).
    background_models = {
        "openai": "openai:gpt-5-mini",
        "gemini": "gemini:gemini-2.5-flash",
        "zai": "zai:glm-4.5-air",
        "deepseek": "deepseek:deepseek-v4-flash",
        "nvidia": "nvidia:meta/llama-3.1-8b-instruct",
    }

    def build_system_prompt(self, context_vars: dict) -> str:
        core = assemble(PROMPT_SECTIONS, context_vars)
        manifest = build_skills_manifest()
        return f"{core}\n\n{manifest}" if manifest else core
