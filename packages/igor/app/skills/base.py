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
    # Progressive tool disclosure (Anthropic's `defer_loading` pattern).
    #
    # False = core: the full definition ships in the prompt prefix every call.
    # True  = deferred: only the NAME is advertised; the model calls `tool_search`
    #         to pull the full schema in when a task actually needs it.
    #
    # Rule 11 makes every tool description 3-4 sentences, which is right for
    # selection accuracy and expensive to ship: 41 always-on skills measured
    # 12.1k tokens, re-sent on every iteration of every turn. Deferral keeps the
    # descriptions intact and stops paying for the ones this turn never uses.
    #
    # The mechanism differs by provider but the model-facing behaviour does not:
    # on Anthropic the tool is sent with `defer_loading: true` and the API's own
    # tool-search server tool resolves it; everywhere else the registry omits it
    # and our `tool_search` skill appends it. Both APPEND the resolved schema
    # rather than rebuilding the array, which is what keeps the cached prefix
    # intact — a swap would invalidate everything after it.
    deferred: bool = False

    # Extra words `tool_search` matches on, beyond the name and description.
    # Descriptions are written in the domain's own vocabulary ("PPTX", "OSINT",
    # "geocode") while the model searches in the owner's ("powerpoint", "look up
    # this IP", "where is this place") — without a bridge, a deferred tool that
    # exists is simply never found, which is a worse failure than a slightly
    # larger prefix. Only meaningful on deferred skills; ignored otherwise.
    search_keywords: str = ""
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
