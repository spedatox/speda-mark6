"""
sync_owner_memory_to_forge — Orion's half of the Forge owner-memory bridge.

Forge (the standalone coding-agent framework the owner also runs locally,
independent of this process) keeps its own on-disk snapshot of what Igor
knows about the owner (forge/agents/owner_memory.py), refreshed live whenever
a connected job happens to run. That snapshot has no freshness guarantee
beyond "whenever Forge last ran something" — a night with no Forge activity
means yesterday's memory, or older, keeps answering for the owner.

This is the other half: Orion, at the end of its own nightly audit (once
owner.md/current.md are freshly composed — see 02_audit.md Pass 6), pushes
that same composed context to whatever Forge/Optimus peer is connected, over
the existing agent WebSocket (app/core/dispatch.py's AgentDispatcher already
holds the wired WebSocketManager for exactly this kind of peer reach). The
receiving side is `forge/gate/peer.py`'s `owner_memory_sync` frame handler,
additive to the existing dispatch chain.

Restricted to Orion alone — this is custodial upkeep of the record it already
owns, not something any agent should trigger. Deliberately does not touch
Forge's own task/session memory (worktrees, ledgers, file cache): the only
thing that crosses this wire is the same owner-memory block already injected
into every in-process agent's prompt.
"""

from app.core.context import AgentContext
from app.skills.base import Skill
from app.skills.memory import MemoryRecallCache, recall_for_context

# Forge registers its peer connection under the Optimus agent_id — it is the
# in-process proxy/fallback for the same external peer (CLAUDE.md: "Optimus is
# the single exception"). Pushing under any other agent_id would reach nobody.
_FORGE_AGENT_ID = "optimus"


class SyncOwnerMemoryToForgeSkill(Skill):
    name = "sync_owner_memory_to_forge"
    deferred = True
    restricted_to = frozenset({"orion"})
    read_only = False
    requires_network = False
    search_keywords = "forge peer sync owner memory push optimus"
    description = (
        "Push the freshly-composed owner-memory context to any connected Forge/Optimus "
        "peer, so its local snapshot is never more than one audit cycle stale. Use it as "
        "the LAST step of your nightly audit, after owner.md and current.md have been "
        "recomposed (Pass 4) — pushing before that just ships yesterday's version. Do NOT "
        "call it mid-audit or for any reason other than closing out the nightly pass; it "
        "is not a way to check whether Forge is online, and it carries no memory-write "
        "capability of its own. Returns how many connected peers received the push, or "
        "says plainly that none were connected — that is a normal, expected outcome for a "
        "peer the owner runs on demand rather than as a standing service."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}

    def __init__(self, dispatcher) -> None:
        self._dispatcher = dispatcher

    async def execute(self, args: dict, context: AgentContext) -> str:
        if context.agent_id != "orion":
            return "sync_owner_memory_to_forge is restricted to Orion."

        # A throwaway cache, not the process-wide one: this runs once a night,
        # not on a hot request path, and the shared cache is keyed for live
        # chat turns — reusing it here would be borrowing an optimization this
        # call does not need and could stamp a stale watermark into.
        block = await recall_for_context(
            context.user_id, context.db, _FORGE_AGENT_ID,
            cache=MemoryRecallCache(),
        )
        reached = await self._dispatcher.push_to_peers(
            _FORGE_AGENT_ID,
            {"type": "owner_memory_sync", "block": block},
        )
        if reached == 0:
            return "No Forge/Optimus peer is currently connected — nothing to push."
        return f"Pushed the composed owner memory to {reached} connected Forge peer(s)."
