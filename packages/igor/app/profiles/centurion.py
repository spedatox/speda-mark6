# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest, derive_iteration

PROMPT_SECTIONS = [
    "agents/centurion/01_identity.md",
    "core/02_voice.md",   # shared register — see prompts/core/02_voice.md
    "core/04_decision_policy.md",
    "core/05_output_policy.md",
    "core/06_visual_output.md",
    "core/07_formatting.md",
    "core/08_memory.md",
    "core/09_agent_network.md",
    "core/10_environment.md",   # Mark VI ecosystem glossary (Forge/Heartbreaker/sandbox/n8n)
    "core/11_patterns.md",   # the pattern loop: induce a repeat, pre-empt it next time
    "core/13_signature.md",   # sign every artifact you leave outside the chat
    "core/14_calendar.md",   # all time-related work: read the calendar, then write it properly
    "core/15_language.md",   # the one language the owner picked — every word, every surface
]


class CenturionProfile(AgentProfile):
    """Centurion — cyber security. Defensive, authorized security work for the
    owner's own assets: CVE/threat intelligence, exposure assessment, hardening."""

    agent_id = "centurion"
    name = "Centurion"
    # The agent's own iteration, read off PROMPT_SECTIONS[0] (its identity
    # prompt). Bump the `Iteration:` line there and the signature follows.
    mark = derive_iteration(PROMPT_SECTIONS[0])
    domain = "cyber security"

    # ElevenLabs "Sam" (raspy, serious) — verify this id exists in the
    # owner's ElevenLabs voice library (GET /voice/voices) and swap it
    # if not; premade voice ids can vary by plan/account.
    voice_id = "elevenlabs:eleven_multilingual_v2:yoZ06aMxZJJ28mfd3POQ"
    canvas_brief = (
        "Presenting security: every incident, exposure or finding is its own card "
        "with its severity, its affected surface and its status, and anything "
        "with a sequence of events is a timeline. Speak the risk and the next "
        "action, not the log lines"
    )
    doc_theme = DocTheme(accent="#d8483c")   # signature red — matches the UI brand
    # Like Optimus, Centurion can be backed by a Forge peer (its own Cell, with
    # outbound network for authorized scans). While a peer is connected on
    # /agents/ws/centurion, /chat/centurion proxies to it; offline, this
    # in-process profile answers as the identity + fallback engine.
    external_backend = True

    # Unrestricted — all tools available (same as Speda). Previously a narrow
    # allowlist; broadened so every agent can use every registered capability.
    tool_allowlist = None

    sonnet_model = "claude-sonnet-4-6"
    haiku_model = "claude-haiku-4-5-20251001"
    background_models = {
        "openai": "openai:gpt-5-mini",
        "gemini": "gemini:gemini-3.5-flash-lite",
        "vertex": "vertex:google/gemini-3.5-flash-lite",
        "zai": "zai:glm-4.5-air",
        "deepseek": "deepseek:deepseek-v4-flash",
    }

    def build_system_prompt(self, context_vars: dict) -> str:
        core = assemble(PROMPT_SECTIONS, context_vars)
        manifest = build_skills_manifest()
        return f"{core}\n\n{manifest}" if manifest else core
