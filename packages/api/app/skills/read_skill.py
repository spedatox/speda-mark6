import logging
from pathlib import Path

from app.skills.base import Skill
from app.core.context import AgentContext

logger = logging.getLogger(__name__)

SKILL_DOCS_DIR = Path(__file__).parent / "skill_docs"


class ReadSkillSkill(Skill):
    """
    Progressive disclosure meta-tool.

    The system prompt contains only a compact manifest (name + description per skill).
    When Claude determines a skill is relevant, it calls read_skill to load the full
    SKILL.md instructions — exactly as Anthropic's Agent Skills architecture works.
    """

    name = "read_skill"
    description = (
        "Loads the full usage instructions for an installed skill. "
        "Call this before using a skill to get detailed workflows, examples, and guidelines. "
        "Use it when the compact manifest description is not enough to complete the task. "
        "Returns the complete SKILL.md content for the requested skill."
    )
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": (
                    "Name of the skill to load, exactly as listed in the skills manifest "
                    "(e.g. 'inline-rendering', 'generate-document', 'system-info')."
                ),
            }
        },
        "required": ["skill_name"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        skill_name = args.get("skill_name", "").strip()
        skill_path = SKILL_DOCS_DIR / skill_name / "SKILL.md"

        logger.info(
            "read_skill_execute",
            extra={"request_id": context.request_id, "skill": skill_name},
        )

        if not skill_path.exists():
            available = sorted(
                d.name for d in SKILL_DOCS_DIR.iterdir() if d.is_dir()
            ) if SKILL_DOCS_DIR.exists() else []
            return (
                f"Skill '{skill_name}' not found. "
                f"Available skills: {', '.join(available) or 'none'}"
            )

        return skill_path.read_text(encoding="utf-8")
