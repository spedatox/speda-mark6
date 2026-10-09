"""Owner personality preferences in the existing runtime store; no prompt assembly here."""
from fastapi import HTTPException
from app.core import runtime_state
from app.schemas.agent import AgentPersonalityInfo, AgentPersonalitySet, PersonalitySettings


def settings_for_profile(profile) -> PersonalitySettings:
    return PersonalitySettings.model_validate(
        runtime_state.get_agent_personalities().get(profile.personality_key, {}))


def list_personalities(profiles) -> list[AgentPersonalityInfo]:
    return [AgentPersonalityInfo(agent_id=p.agent_id, name=p.name, domain=p.domain,
                                settings=settings_for_profile(p))
            for p in profiles.roster() if p.dispatch_target and not p.external_backend]


def save_personality(profiles, body: AgentPersonalitySet) -> list[AgentPersonalityInfo]:
    profile = profiles.get(body.agent_id)
    if profile is None or not profile.dispatch_target or profile.external_backend:
        raise HTTPException(status_code=404, detail="Unknown editable agent")
    try:
        runtime_state.set_agent_personality(profile.personality_key,
                                            body.settings.model_dump(exclude_defaults=True))
    except OSError as error:
        raise HTTPException(status_code=503, detail="Could not save personality settings; please retry") from error
    return list_personalities(profiles)
