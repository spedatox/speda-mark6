# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared and language-specific voice preferences, stored in runtime state."""

from app.config import settings
from app.core.runtime_state import get_voice_overrides, set_voice_override
from app.schemas.voice import VoiceAgentPatch
from app.services import tts


def agent_configs(profiles) -> dict:
    overrides = get_voice_overrides()
    agents = []
    for profile in profiles.roster():
        if not profile.dispatch_target:
            continue
        override = overrides.get(profile.agent_id, {})
        languages = {}
        for code in ("tr", "en"):
            scoped = override.get("languages", {}).get(code, {})
            languages[code] = {
                "voice_id": scoped.get("voice_id") or None,
                "effective_voice": tts.resolve_voice(None, profile.agent_id, profile=profile, locale=code),
                "voice_settings": {k: scoped[k] for k in tts.VOICE_SETTINGS_KEYS if k in scoped} or None,
                "effective_voice_settings": tts.resolve_voice_settings(profile.agent_id, code),
            }
        agents.append({
            "agent_id": profile.agent_id,
            "name": profile.name,
            "domain": profile.domain,
            "default_voice": profile.voice_id or settings.tts_default_voice,
            "voice_id": override.get("voice_id") or None,
            # These legacy fields describe the shared fallback, independent of
            # the active system language, for older mobile/desktop editors.
            "effective_voice": tts._current_voice_ref(override.get("voice_id") or profile.voice_id or settings.tts_default_voice),
            "voice_settings": {k: override[k] for k in tts.VOICE_SETTINGS_KEYS if k in override} or None,
            "languages": languages,
        })
    return {"agents": agents}


def update_agent(profiles, agent_id: str, patch: VoiceAgentPatch) -> dict:
    profile = profiles.get(agent_id)
    if profile is None or not profile.dispatch_target:
        raise LookupError(f"Unknown voice agent '{agent_id}'")
    current = dict(get_voice_overrides().get(agent_id, {}))
    languages = dict(current.get("languages", {}))
    scope = dict(languages.get(patch.language, {})) if patch.language else current
    changes = patch.model_dump(exclude_unset=True, exclude={"language"})
    if not changes:
        scope.clear()
    else:
        for key, value in changes.items():
            if value is None or (key == "voice_id" and not value.strip()):
                scope.pop(key, None)
            else:
                scope[key] = value.strip() if key == "voice_id" else value
    if patch.language:
        if scope:
            languages[patch.language] = scope
        else:
            languages.pop(patch.language, None)
    # Legacy {} resets shared settings only; never erase language pins through
    # a client that does not know they exist.
    if languages:
        current["languages"] = languages
    else:
        current.pop("languages", None)
    set_voice_override(agent_id, current or None)
    return agent_configs(profiles)
