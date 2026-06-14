import logging

from app.profiles.base import AgentProfile

logger = logging.getLogger(__name__)

# The agent that backs every legacy/unscoped path (default sessions, the bare
# /chat route until /chat/{agent_id} lands, import jobs that just need a cheap
# model). Must always be registered.
DEFAULT_AGENT_ID = "speda"


class ProfileRegistry:
    """
    Holds every enabled in-process AgentProfile, keyed by agent_id.

    Replaces the single ``app.state.profile`` of the pre-multi-tenant design.
    The orchestrator resolves a profile per request from ``context.agent_id``;
    presence ("is agent X available?") is a dict lookup here, not a socket.
    Optimus is NOT in here — it is an external WebSocket peer, not a profile.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, AgentProfile] = {}

    def register(self, profile: AgentProfile) -> None:
        """Register one profile. agent_id must be unique across the suite."""
        agent_id = profile.agent_id
        if agent_id in self._profiles:
            raise ValueError(
                f"Duplicate agent_id '{agent_id}': "
                f"{type(self._profiles[agent_id]).__name__} already registered"
            )
        self._profiles[agent_id] = profile
        logger.info(
            "profile_register",
            # `name` is a reserved LogRecord attribute — passing it in extra
            # raises KeyError at record creation. Use a namespaced key.
            extra={"agent_id": agent_id, "profile_name": profile.name, "domain": profile.domain},
        )

    def get(self, agent_id: str) -> AgentProfile | None:
        """Return the profile for agent_id, or None if no such agent is enabled."""
        return self._profiles.get(agent_id)

    def require(self, agent_id: str) -> AgentProfile:
        """Return the profile for agent_id or raise KeyError. The orchestrator
        uses this — an unknown agent_id is a routing error, not a soft miss."""
        try:
            return self._profiles[agent_id]
        except KeyError:
            raise KeyError(
                f"Unknown agent_id '{agent_id}'. Enabled agents: "
                f"{sorted(self._profiles)}"
            ) from None

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._profiles

    @property
    def default(self) -> AgentProfile:
        """The SPEDA profile — backs unscoped paths. Raises if never registered."""
        return self.require(DEFAULT_AGENT_ID)

    def roster(self) -> list[AgentProfile]:
        """All enabled profiles, sorted by agent_id — the agent directory."""
        return [self._profiles[k] for k in sorted(self._profiles)]
