# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import re
from pathlib import Path

from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest, derive_iteration

_IDENTITY_PATH = Path(__file__).parent.parent / "prompts" / "core" / "01_identity.md"


def _derive_agent_name() -> str:
    """
    Single source of the agent's display name: the identity file's H1 heading.
    `# IDENTITY — Speda Mark VI` → "Speda Mark VI". Falls back to "Speda".
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
    return "Speda"


# Derived once at import — the agent's display name (used for the FastAPI title).
AGENT_NAME = _derive_agent_name()

# Core sections assembled in order.
# The skills manifest is appended dynamically — it reads SKILL.md frontmatter at
# request time so adding a new skill only requires dropping a SKILL.md file.
PROMPT_SECTIONS = [
    "core/01_identity.md",   # full identity: who/how/Superior Six/boundaries/runtime
    "core/02_voice.md",      # register — shared by the whole roster, one file to tune
    "core/03_capabilities.md",
    "core/04_decision_policy.md",
    "core/05_output_policy.md",
    "core/06_visual_output.md",   # always-loaded — no tool backs this, must be in core
    "core/07_formatting.md",      # math/LaTeX + currency formatting guidance
    "core/08_memory.md",          # persistent memory protocol (Anthropic memory pattern)
    "core/09_agent_network.md",   # inter-agent dispatch + House Party Protocol
    "core/10_environment.md",   # Mark VI ecosystem glossary (Igor/Heartbreaker/Legion/Forge/sandbox/n8n)
    "core/11_patterns.md",   # the pattern loop: induce a repeat, pre-empt it next time
    "core/12_skyfall.md",    # the owner's launch rail — arm the countdown, never claim it fired
    "core/13_signature.md",   # sign every artifact you leave outside the chat
    "core/14_calendar.md",   # all time-related work: read the calendar, then write it properly
    "core/15_language.md",   # the one language the owner picked — every word, every surface
]


class SPEDAProfile(AgentProfile):
    """
    Speda — the orchestrator profile and primary owner-facing agent.
    One profile among several in the multi-tenant backend; addressed by
    agent_id="speda". Never put prompt content in core modules.
    """

    agent_id = "speda"
    domain = "orchestration & general executive assistance"
    doc_theme = DocTheme(accent="#36abca")   # signature cyan — matches the UI brand
    # None = full registry access. Speda is the orchestrator; it sees every
    # tool. The domain-specialised agents declare narrower allowlists.
    tool_allowlist = None
    # Orchestrator privilege: a new Speda session is seeded with recent session
    # recaps from EVERY agent (tagged by agent_id), not just its own — the
    # executive needs cross-agent situational awareness. Specialists keep the
    # base "own" scope.
    episodic_recall_scope = "all"
    # Mission commander under the House Party Protocol — Speda plans and
    # dispatches; every other profile is an operative. The war-room alias
    # (profiles/warroom.py) subclasses this and inherits the command role.
    house_party_commander = True

    name = AGENT_NAME   # derived from 01_identity.md — never hardcode here
    # The agent's own iteration, read off PROMPT_SECTIONS[0] (its identity
    # prompt). Bump the `Iteration:` line there and the signature follows.
    mark = derive_iteration(PROMPT_SECTIONS[0])
    sonnet_model = "claude-sonnet-4-6"
    haiku_model = "claude-haiku-4-5-20251001"
    # ElevenLabs "Adam" (deep, steady — the default assistant voice) — verify this id exists in the
    # owner's ElevenLabs voice library (GET /voice/voices) and swap it
    # if not; premade voice ids can vary by plan/account.
    # Was Azure "en-US-BrianMultilingualNeural" before the ElevenLabs move.
    voice_id = "elevenlabs:eleven_multilingual_v2:pNInz6obpgDQGcFmaJgB"

    # Cheapest sensible model per non-Anthropic provider, used for background
    # tasks when the user is chatting on that provider (see base.background_model).
    background_models = {
        "openai": "openai:gpt-5-mini",
        "gemini": "gemini:gemini-3.5-flash-lite",
        "vertex": "vertex:google/gemini-3.5-flash-lite",
        "zai": "zai:glm-4.5-air",
        "deepseek": "deepseek:deepseek-v4-flash",
        "nvidia": "nvidia:meta/llama-3.1-8b-instruct",
    }

    def build_system_prompt(self, context_vars: dict) -> str:
        core = assemble(PROMPT_SECTIONS, context_vars)
        manifest = build_skills_manifest()
        return f"{core}\n\n{manifest}" if manifest else core
