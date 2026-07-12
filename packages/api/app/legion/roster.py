"""
The Legion roster — declarative legionnaire (worker) definitions.

Each legionnaire is data, not code (the Claude Code agent-definition pattern):
a role prompt, an effort level that drives provider-agnostic model resolution,
an iteration budget, and a tool scope. Adding a worker type = adding an entry
here; the runner and the tool definition pick it up automatically.

Effort → model (see resolve_worker_model):
  low / medium → profile.background_model(parent) — the cheap tier on the
                 SAME provider as the parent chat model
  high / inherit → the parent model itself
This mirrors CLAUDE.md D-SA effort policy (research medium · synthesis high ·
pre-filter low · judge low) and removes the old Claude-only model pin.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.profiles.base import AgentProfile

Effort = Literal["low", "medium", "high", "inherit"]

# Cap what a worker can pour back into the parent's context (mirrors
# dispatch.MAX_RESULT_CHARS — a worker that returns a novel defeats the point
# of context isolation).
MAX_WORKER_RESULT_CHARS = 12_000

# Concurrent background workers — runaway-fan-out guard.
MAX_LEGION_BACKGROUND = 3

# Tools no legionnaire ever sees: Task blocks recursive spawning; the dispatch
# surface keeps anonymous workers out of the persona network (a worker is not
# a roster member and must not talk like one).
WORKER_EXCLUDED_TOOLS: frozenset = frozenset({"Task", "dispatch_agent", "house_party"})


@dataclass(frozen=True)
class LegionnaireDef:
    worker_id: str
    when_to_use: str      # one line, rendered into the Task tool description
    system_prompt: str    # role framing; the task description is appended at runtime
    effort: Effort
    max_iterations: int = 15
    read_only: bool = False   # True → only read-only skills + the servers below
    # MCP servers a read-only worker may use (research surfaces). Ignored when
    # read_only is False.
    mcp_servers: frozenset = frozenset({"tavily", "exa"})


_CONTRACT = (
    "Complete the task and return your findings in full — they go back to the "
    "agent that deployed you, not to a human. Do not ask for clarification; act "
    "on what you have. Do not greet, apologise, or narrate your process."
)

LEGION_ROSTER: dict[str, LegionnaireDef] = {
    "scout": LegionnaireDef(
        worker_id="scout",
        when_to_use="cheap pre-filter/triage — survey sources fast, return a ranked shortlist of leads, no synthesis",
        system_prompt=(
            "You are a scout in the Legion, Igor's worker corps. Your job is rapid "
            "triage: survey the available sources with a few quick searches and "
            "return a RANKED SHORTLIST of the most promising leads (title, one-line "
            "why, URL) — nothing more. You do not read deeply, you do not "
            "synthesise, you do not conclude; a heavier worker follows you. "
            + _CONTRACT
        ),
        effort="low",
        max_iterations=6,
        read_only=True,
    ),
    "researcher": LegionnaireDef(
        worker_id="researcher",
        when_to_use="the default for research fan-out — deep-dive ONE subtopic across multiple searches, return findings with citations",
        system_prompt=(
            "You are a researcher in the Legion, Igor's worker corps. You are "
            "assigned exactly one subtopic: investigate it thoroughly across "
            "multiple independent searches and sources, cross-check what you find, "
            "and return dense factual findings with a source URL for every "
            "non-obvious claim. Report facts and numbers, not vibes; note "
            "disagreements between sources instead of papering over them. "
            + _CONTRACT
        ),
        effort="medium",
        max_iterations=15,
        read_only=True,
    ),
    "analyst": LegionnaireDef(
        worker_id="analyst",
        when_to_use="synthesis — turn raw findings (usually from prior workers) into the structured briefing/report section",
        system_prompt=(
            "You are an analyst in the Legion, Igor's worker corps. You receive "
            "raw findings — usually gathered by other workers and included in your "
            "task — and produce the finished, structured synthesis: a briefing "
            "section, comparison, or report body with a clear line of argument. "
            "Preserve source attributions from the findings; never invent facts "
            "that are not in your inputs or verifiable with your tools. "
            + _CONTRACT
        ),
        effort="high",
        max_iterations=20,
        read_only=False,
    ),
    "judge": LegionnaireDef(
        worker_id="judge",
        when_to_use="verification of a drafted briefing/report ONLY — check claims against sources, flag errors",
        system_prompt=(
            "You are a judge in the Legion, Igor's worker corps. You receive a "
            "drafted briefing or report and verify it: check each substantive "
            "claim against its cited source (or a quick search when no citation is "
            "given) and return a verdict per claim — CONFIRMED, WRONG (with the "
            "correction), or UNVERIFIABLE. Flag missing citations and internal "
            "contradictions. You do not rewrite the draft; you audit it. "
            + _CONTRACT
        ),
        effort="low",
        max_iterations=8,
        read_only=True,
    ),
    "general": LegionnaireDef(
        worker_id="general",
        when_to_use="anything that doesn't fit the specialists — full parent toolset, parent-grade model",
        system_prompt=(
            "You are a general worker in the Legion, Igor's worker corps. "
            "You have the deploying agent's full toolset and a self-contained "
            "task: complete it end to end and return the result. "
            + _CONTRACT
        ),
        effort="inherit",
        max_iterations=15,
        read_only=False,
    ),
}

DEFAULT_LEGIONNAIRE = "general"


def resolve_worker_model(
    worker: LegionnaireDef,
    explicit: str | None,
    parent_model: str,
    profile: "AgentProfile",
) -> str:
    """
    Provider-agnostic worker model resolution (priority mirrors Claude Code):
      1. deployment pin (settings.legion_model_override — empty by default)
      2. explicit tool param
      3. effort low/medium → the profile's cheap tier on the SAME provider
         as the parent model (Anthropic parent → Haiku, zai parent → glm-air…)
      4. high/inherit → the parent model itself
    Never hardcodes a model ID (Rule 10) — everything routes through the
    profile or the parent.
    """
    from app.config import settings

    if settings.legion_model_override:
        return settings.legion_model_override
    if explicit:
        return explicit
    if worker.effort in ("low", "medium"):
        return profile.background_model(parent_model)
    return parent_model


def build_tool_definition() -> dict:
    """The Legion's tool definition (wire name "Task" — models know the Task
    delegation pattern by that name; The Legion is the branding). Built once
    at import; the registry appends it under the budget/dead-zone gate."""
    legionnaire_lines = "\n".join(
        f"- `{w.worker_id}`: {w.when_to_use}" for w in LEGION_ROSTER.values()
    )
    return {
        "name": "Task",
        "description": (
            "Deploys The Legion: isolated, billed worker agents (legionnaires) for "
            "heavy research and synthesis. This is EXPENSIVE and RARE — deploy ONLY "
            "when the owner explicitly asked for a deep/thorough research report AND "
            "it genuinely needs 6+ independent searches across distinct subtopics. "
            "Do NOT use it for news, current events, quick facts, lookups, writes, "
            "reminders, or anything completable with a handful of direct tool calls — "
            "handle those yourself in the main loop; running several searches "
            "yourself is preferred over deploying a worker. When in doubt, do NOT "
            "deploy. Pick the right legionnaire:\n"
            f"{legionnaire_lines}\n"
            "Deploy several in ONE message for parallel fan-out (e.g. one researcher "
            "per subtopic, then one analyst over their findings). Each worker is "
            "fully isolated — it sees NOTHING of this conversation, so the prompt "
            "must be self-contained. The worker's result is returned only to you, "
            "never shown to the owner: summarise it for him and name which "
            "legionnaires ran, one sentence per worker. With run_in_background=true "
            "the call returns a ticket immediately; retrieve the result later with "
            "legion_status — never fabricate it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Clear, scoped task label for the legionnaire (3-8 words).",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "Full self-contained prompt: the worker sees nothing of this "
                        "conversation, so include every fact, constraint, and the "
                        "expected output format."
                    ),
                },
                "legionnaire": {
                    "type": "string",
                    "enum": list(LEGION_ROSTER),
                    "description": (
                        "Worker type. scout=cheap pre-filter, researcher=multi-search "
                        "deep dive, analyst=synthesis, judge=verification of drafted "
                        "briefings/reports, general=default. Omit for general."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Optional explicit 'provider:model' override. Rarely needed — "
                        "the legionnaire's effort level picks the right model on the "
                        "current provider automatically."
                    ),
                },
                "run_in_background": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Launch detached and return a ticket immediately; the result "
                        "is retrieved later via legion_status. Use for long jobs the "
                        "owner should not wait on."
                    ),
                },
            },
            "required": ["description", "prompt"],
        },
    }


# Built once — the definition is static per process.
TASK_TOOL_DEFINITION: dict = build_tool_definition()
