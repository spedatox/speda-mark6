"""
Prompt assembly and Agent Skills manifest builder.

Architecture mirrors Anthropic's Agent Skills progressive-disclosure model:
  - System prompt contains ONLY skill metadata (name + description, ~100 tokens/skill)
  - Full SKILL.md content is loaded on demand via the read_skill tool
  - Core prompt sections live in app/prompts/core/*.md
  - Skill docs live in app/skills/skill_docs/<name>/SKILL.md
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent
SKILL_DOCS_DIR = Path(__file__).parent.parent / "skills" / "skill_docs"


# ── Frontmatter ───────────────────────────────────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Parse YAML-style frontmatter from a markdown file.
    Returns (metadata_dict, body_text).
    Handles multi-line description values (continued lines indented with spaces).
    """
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if not match:
        return {}, text

    meta: dict[str, str] = {}
    current_key: str | None = None

    for line in match.group(1).splitlines():
        # Continuation line (indented)
        if current_key and line.startswith("  "):
            meta[current_key] = meta[current_key] + " " + line.strip()
            continue

        if ":" in line:
            key, _, value = line.partition(":")
            current_key = key.strip()
            meta[current_key] = value.strip()

    return meta, text[match.end():]


# ── Core section loader ───────────────────────────────────────────────────────

def load_section(relative_path: str, context_vars: dict | None = None) -> str:
    """
    Read a single .md section and substitute runtime vars.

    Only exact `{key}` tokens for keys in context_vars are replaced — literal
    braces elsewhere (LaTeX like \\frac{a}{b}, code, JSON) are left untouched.
    `str.format`/`format_map` cannot be used here because they treat every brace
    as a field and choke on math/code in the prompt files.
    """
    path = PROMPTS_DIR / relative_path
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        # A missing section must never take down a live request — log and skip.
        logger.warning("prompt_section_missing", extra={"section": relative_path})
        return ""
    if context_vars:
        for key, value in context_vars.items():
            text = text.replace("{" + key + "}", str(value))
    return text


def assemble(sections: list[str], context_vars: dict | None = None) -> str:
    """Load and join multiple prompt sections separated by a blank line."""
    parts = [load_section(s, context_vars) for s in sections]
    return "\n\n".join(parts)


# ── Skills manifest ───────────────────────────────────────────────────────────

_cached_manifest: str | None = None


def build_skills_manifest() -> str:
    """
    Build a compact skills manifest from SKILL.md frontmatter.

    Only names and descriptions are included here — the system prompt stays lean.
    Full instructions are available on demand via the read_skill tool.

    Cached after the first call: the manifest only changes when you add or remove
    a skill file (i.e. a deploy), never mid-request. Saves a filesystem scan on
    every turn.
    """
    global _cached_manifest
    if _cached_manifest is not None:
        return _cached_manifest

    if not SKILL_DOCS_DIR.exists():
        _cached_manifest = ""
        return ""

    entries: list[tuple[str, str]] = []
    for skill_dir in sorted(SKILL_DOCS_DIR.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        meta, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        name = meta.get("name", skill_dir.name)
        description = meta.get("description", "No description.")
        entries.append((name, description))

    if not entries:
        _cached_manifest = ""
        return ""

    lines = [
        "## Installed Skills",
        "",
        "Use `read_skill` with the skill name to load full instructions before using a skill.",
        "",
    ]
    for name, description in entries:
        lines.append(f"- **{name}**: {description}")

    _cached_manifest = "\n".join(lines)
    return _cached_manifest
