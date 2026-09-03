# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from app.profiles.base import AgentProfile, DocTheme
from app.prompts.loader import assemble, build_skills_manifest, derive_iteration

# Orion assembles his own identity + the audit procedure, then the shared policy
# sections. He deliberately keeps the same memory + agent-network protocol as the
# rest of the roster — he must speak the same file law he enforces.
PROMPT_SECTIONS = [
    "agents/orion/01_identity.md",
    "agents/orion/02_audit.md",
    "agents/orion/03_operations.md",   # server ops runbook (system_ops host bridge)
    "agents/orion/04_lifeboat.md",     # host resource pressure — owner-led reclamation
    "agents/orion/05_doormat.md",      # moving the deployment to a new domain
    "agents/orion/06_octavius.md",     # the database backup, and how to restore one
    "core/02_voice.md",   # shared register — see prompts/core/02_voice.md
    "core/05_output_policy.md",
    "core/07_formatting.md",
    "core/08_memory.md",
    "core/09_agent_network.md",
    "core/10_environment.md",   # Mark VI ecosystem glossary
    "core/11_patterns.md",   # the pattern loop: induce a repeat, pre-empt it next time
    "core/13_signature.md",   # sign every artifact you leave outside the chat
    "core/14_calendar.md",   # all time-related work: read the calendar, then write it properly
    "core/15_language.md",   # the one language the owner picked — every word, every surface
]


class OrionProfile(AgentProfile):
    """
    Orion — the Mark VI custodian. Not a domain specialist for the outside world:
    his subject IS the system. He owns memory hygiene (the boundary law in
    docs/MEMORY_ARCHITECTURE.md), the nightly audit, and host maintenance via the
    Orion-only system_ops skill. Terse, procedural, reports in changelogs.

    tool_allowlist is None (full registry, matching the rest of the roster). The
    host capability is guarded at the SKILL level — system_ops declares
    restricted_to={"orion", "optimus"}, so no other agent can see or call it
    regardless of their allowlist. Orion is the primary custodian; Optimus shares
    it for infrastructure work. He is a dispatch target: other agents may hand him
    cleanup.
    """

    agent_id = "orion"
    name = "Orion"
    # The agent's own iteration, read off PROMPT_SECTIONS[0] (its identity
    # prompt). Bump the `Iteration:` line there and the signature follows.
    mark = derive_iteration(PROMPT_SECTIONS[0])
    domain = "Mark VI maintenance — memory custodian & host ops"

    # ElevenLabs "Elli" (calm, thoughtful) — verify this id exists in the
    # owner's ElevenLabs voice library (GET /voice/voices) and swap it
    # if not; premade voice ids can vary by plan/account.
    voice_id = "elevenlabs:eleven_multilingual_v2:MF3mGyEYCl7XYWbV9V6O"
    doc_theme = DocTheme(accent="#8a7fd6")   # signature indigo

    tool_allowlist = None
    dispatch_target = True

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
