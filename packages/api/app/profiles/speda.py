import re
from pathlib import Path

from app.profiles.base import AgentProfile
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
]


class SPEDAProfile(AgentProfile):
    """
    SPEDA identity profile.
    Fork this file for each Superior Six agent — change name, models, and PROMPT_SECTIONS.
    Never put prompt content in core modules.
    """

    name = AGENT_NAME   # derived from 01_identity.md — never hardcode here
    sonnet_model = "claude-sonnet-4-6"
    haiku_model = "claude-haiku-4-5-20251001"

    def build_system_prompt(self, context_vars: dict) -> str:
        core = assemble(PROMPT_SECTIONS, context_vars)
        manifest = build_skills_manifest()
        return f"{core}\n\n{manifest}" if manifest else core
