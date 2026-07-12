"""
The Legion — Igor's disposable worker corps (Tier 0, wire name "Task").

Anonymous, single-purpose worker agents ("legionnaires") spawned by any
in-process agent to offload context-heavy research and synthesis work. Distinct
from the Superior Six persona roster (reached via dispatch_agent — see
core/dispatch.py): a legionnaire has no identity, no memory, no network channel,
and exists only for the duration of one task.

Provider-agnostic by design: worker models resolve from the PARENT's provider
(profile.background_model for the cheap tier, parent model for high effort), so
the Legion works on every provider the chat works on — never Claude-only.
"""

from app.legion.roster import (
    LEGION_ROSTER,
    LegionnaireDef,
    MAX_WORKER_RESULT_CHARS,
    WORKER_EXCLUDED_TOOLS,
    build_tool_definition,
    resolve_worker_model,
)
from app.legion.runner import LegionRunner

__all__ = [
    "LEGION_ROSTER",
    "LegionnaireDef",
    "LegionRunner",
    "MAX_WORKER_RESULT_CHARS",
    "WORKER_EXCLUDED_TOOLS",
    "build_tool_definition",
    "resolve_worker_model",
]
