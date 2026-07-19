from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.context import AgentContext


class Skill(ABC):
    """
    Base class for all Tier 1 Python Skills.

    Rules (per CLAUDE.md Rule 11):
    - description must be 3–4 sentences minimum.
    - State: what it does, when to use it, when NOT to use it, what it returns.
    - read_only = True for all research/retrieval skills (enables parallel execution).
    """

    name: str
    description: str
    input_schema: dict
    read_only: bool = False  # Set True for research/retrieval skills (Rule 9)
    # Set True for skills that need an INTERNET uplink (not just a local
    # service) — they are filtered out under the Dead Zone Protocol.
    requires_network: bool = False
    # Hard per-agent restriction: when set, ONLY these agent_ids may see or call
    # this skill, regardless of their (inclusion) tool_allowlist. Use for
    # privileged capabilities that must never leak to the general roster — e.g.
    # system_ops is scoped to {"orion"} alone. None = available to every agent
    # whose allowlist permits it (the normal case).
    restricted_to: frozenset[str] | None = None

    @abstractmethod
    async def execute(self, args: dict, context: "AgentContext") -> str:
        """Execute the skill and return a string result for Claude to reason over."""
        ...

    def to_tool_definition(self) -> dict:
        """Return the Anthropic tool definition dict for this skill."""
        defn: dict = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
        return defn
