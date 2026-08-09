"""
The Legion runner — executes legionnaire (worker) loops.

A worker is an isolated agentic loop on the provider-agnostic LLMClient: fresh
messages, a role prompt from the roster, the parent's tool surface scoped down
(never Task/dispatch — no recursion, no persona traffic), and a per-worker
iteration budget. Deliberately NOT orchestrator.run(): a worker carries no
session, no memory recall, no SSE stream, no identity — that weight is exactly
what it exists to avoid (running a full persona is dispatch_agent's job).

Background workers write agent_messages tickets (kind="legion") — the same
table dispatch uses, so the comms tray shows them and legion_status can
retrieve results. Background execution captures only plain values from the
request context, never the request-scoped db session.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from app.legion.roster import (
    DEFAULT_LEGIONNAIRE,
    LEGION_ROSTER,
    MAX_LEGION_BACKGROUND,
    MAX_WORKER_RESULT_CHARS,
    WORKER_EXCLUDED_TOOLS,
    LegionnaireDef,
    resolve_worker_model,
)

if TYPE_CHECKING:
    from app.core.context import AgentContext
    from app.core.registry import CapabilityRegistry
    from app.profiles.registry import ProfileRegistry
    from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)


class LegionRunner:
    """One instance, owned by the CapabilityRegistry (Tier 0)."""

    def __init__(
        self,
        client: "LLMClient",
        registry: "CapabilityRegistry",
        profiles: "ProfileRegistry | None",
    ) -> None:
        self._client = client
        self._registry = registry
        self._profiles = profiles
        self._background: set[asyncio.Task] = set()
        # Set late in the lifespan (see set_report_hook): the orchestrator and
        # turn registry it closes over do not exist yet at Tier-0 registration.
        self._report_hook = None

    def set_report_hook(self, hook) -> None:
        """Install the callback a finished BACKGROUND worker fires to report in.

        Kept as an injected awaitable rather than logic here: the Legion runs
        workers and must not know about sessions, delivery or the turn registry
        (Rule 1). None = the old behaviour, where a background result waits in
        its ticket until someone asks legion_status.
        """
        self._report_hook = hook

    # ── Entry point (called by registry.execute for tool "Task") ─────────────

    async def run_worker(self, args: dict, context: "AgentContext") -> str:
        if self._client is None:
            logger.error("legion_no_client", extra={"request_id": context.request_id})
            return "The Legion is unavailable: LLMClient was not injected into the registry."

        description = args.get("description", "")
        prompt = args.get("prompt", "")
        worker_key = args.get("legionnaire") or DEFAULT_LEGIONNAIRE
        worker = LEGION_ROSTER.get(worker_key)
        if worker is None:
            return (
                f"Error: unknown legionnaire '{worker_key}'. Valid types: "
                f"{', '.join(LEGION_ROSTER)}. Re-call with one of those (or omit "
                "the field for a general worker)."
            )

        model = resolve_worker_model(
            worker,
            explicit=args.get("model"),
            parent_model=context.model,
            profile=self._resolve_profile(context.agent_id),
        )
        tools = self._worker_tools(worker, context)

        if args.get("run_in_background"):
            return await self._launch_background(
                worker=worker, model=model, tools=tools,
                description=description, prompt=prompt, context=context,
            )

        return await self._loop(
            worker=worker, model=model, tools=tools,
            description=description, prompt=prompt,
            request_id=context.request_id, context=context,
        )

    def _resolve_profile(self, agent_id: str):
        """Parent agent's profile — its per-provider cheap tiers drive worker
        model resolution. Falls back to a minimal shim when profiles are absent
        (unit tests): background_model then just returns the parent model."""
        if self._profiles is not None:
            try:
                return self._profiles.require(agent_id)
            except Exception:  # noqa: BLE001
                pass

        class _Inherit:
            @staticmethod
            def background_model(active_model_ref: str) -> str:
                return active_model_ref

        return _Inherit()

    @staticmethod
    def _fold_spend(context: "AgentContext", uncached: int, cache_read: int,
                    cache_write: int, output: int) -> None:
        """Add one worker call's tokens onto the PARENT turn's running spend.

        A legionnaire is billed to the same invoice as the turn that deployed
        it, so it belongs in the same counter. Inline workers share the parent's
        live context and land straight on the turn total the runner persists;
        a background worker holds a detached context (its parent's HTTP turn is
        long gone), so its tally accumulates there and is logged on completion
        instead — the tokens are reported either way, which is the whole point.
        """
        spend = context.extra.setdefault(
            "token_usage",
            {"input": 0, "output": 0, "billable_input": 0,
             "cache_read": 0, "cache_write": 0},
        )
        spend["input"] = spend.get("input", 0) + uncached + cache_read + cache_write
        spend["billable_input"] = spend.get("billable_input", 0) + uncached
        spend["cache_read"] = spend.get("cache_read", 0) + cache_read
        spend["cache_write"] = spend.get("cache_write", 0) + cache_write
        spend["output"] = spend.get("output", 0) + output

    # ── Tool scoping ──────────────────────────────────────────────────────────

    def _worker_tools(self, worker: LegionnaireDef, context: "AgentContext") -> list[dict]:
        """The parent's tool surface, scoped down for this worker. Read-only
        workers keep only read-only Tier-1 skills plus research MCP servers."""
        tools = [
            t for t in self._registry.list_tools(
                # Toolsets the parent already loaded this turn are visible to
                # its workers too (they inherit the parent's surface, minus
                # the exclusions below).
                active_servers=context.extra.get("active_servers"),
                allowlist=context.extra.get("tool_allowlist"),
                agent_id=context.agent_id,
            )
            if t["name"] not in WORKER_EXCLUDED_TOOLS
        ]

        # An exact allowlist wins over the read-only bucket and is applied first:
        # a specialist worker should see the handful of tools its job needs, not
        # every read-only tool the parent happened to have loaded.
        if worker.tool_scope is not None:
            return [t for t in tools if t["name"] in worker.tool_scope]

        if not worker.read_only:
            return tools

        kept = []
        for t in tools:
            kind, owner = self._registry.tool_owner(t["name"])
            if kind == "skill" and self._registry.skill_is_read_only(t["name"]):
                kept.append(t)
            elif kind == "mcp" and owner in worker.mcp_servers:
                kept.append(t)
        return kept

    # ── The worker loop ───────────────────────────────────────────────────────

    async def _loop(
        self,
        *,
        worker: LegionnaireDef,
        model: str,
        tools: list[dict],
        description: str,
        prompt: str,
        request_id: str,
        context: "AgentContext",
    ) -> str:
        from app.services.llm_client import blocks_to_dicts

        started = time.monotonic()
        logger.info(
            "legion_worker_start",
            extra={
                "request_id": request_id,
                "worker": worker.worker_id,
                "model": model,
                "tools": len(tools),
                "description": description,
            },
        )

        messages: list[dict] = [{"role": "user", "content": prompt}]
        # Task framing rides the first USER message, not the system prompt. Every
        # worker of a given type then shares one byte-identical system block, so
        # the provider's cache (explicit on Anthropic, implicit on the rest) can
        # serve it across a whole fan-out; appending the task made every worker's
        # prefix unique and guaranteed a cold cache on each one.
        system = worker.system_prompt
        messages[0]["content"] = f"Task: {description}\n\n{prompt}"
        iterations = 0
        salvage: list[str] = []  # accumulated text, returned on guard trip
        # Per-worker spend. Folded onto the parent turn below so a Legion
        # deployment stops being invisible in the session's token counters.
        spend = {"input": 0, "output": 0, "billable_input": 0,
                 "cache_read": 0, "cache_write": 0}

        while True:
            if iterations >= worker.max_iterations:
                logger.error(
                    "legion_safety_guard",
                    extra={
                        "request_id": request_id,
                        "worker": worker.worker_id,
                        "iterations": iterations,
                        "description": description,
                        "billable_input": spend["billable_input"],
                        "cache_read": spend["cache_read"],
                        "output": spend["output"],
                    },
                )
                partial = "\n".join(s for s in salvage if s.strip())
                if partial:
                    return (
                        f"[PARTIAL — iteration cap ({worker.max_iterations}) reached; "
                        f"findings gathered so far:]\n{partial}"[:MAX_WORKER_RESULT_CHARS]
                    )
                return (
                    f"Legion safety guard triggered after {iterations} tool iterations "
                    "with no salvageable output. Task incomplete."
                )

            response = await self._client.create_message(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=8096,
            )

            # Account for the call BEFORE any branch can return. A worker that
            # trips the iteration guard, dies on an unknown stop reason, or
            # finishes on its first lap has still been billed for every call it
            # made, and the old loop recorded none of them.
            _u = getattr(response, "usage", None)
            if _u is not None:
                _uncached = getattr(_u, "input_tokens", 0) or 0
                _read = getattr(_u, "cache_read_input_tokens", 0) or 0
                _write = getattr(_u, "cache_creation_input_tokens", 0) or 0
                spend["input"] += _uncached + _read + _write
                spend["billable_input"] += _uncached
                spend["cache_read"] += _read
                spend["cache_write"] += _write
                spend["output"] += getattr(_u, "output_tokens", 0) or 0
                self._fold_spend(context, _uncached, _read, _write,
                                 getattr(_u, "output_tokens", 0) or 0)

            stop_reason = response.stop_reason
            messages.append({"role": "assistant", "content": blocks_to_dicts(response.content)})
            salvage.extend(
                b.text for b in response.content if getattr(b, "type", "") == "text" and b.text
            )

            if stop_reason == "end_turn":
                text_parts = [
                    b.text for b in response.content if hasattr(b, "text") and b.text
                ]
                result = ("\n".join(text_parts) or "(the worker returned no text)")
                result = result[:MAX_WORKER_RESULT_CHARS]
                logger.info(
                    "legion_worker_done",
                    extra={
                        "request_id": request_id,
                        "worker": worker.worker_id,
                        "model": model,
                        "iterations": iterations,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "result_length": len(result),
                        "input_read": spend["input"],
                        "billable_input": spend["billable_input"],
                        "cache_read": spend["cache_read"],
                        "cache_write": spend["cache_write"],
                        "output": spend["output"],
                    },
                )
                return result

            if stop_reason == "tool_use":
                iterations += 1
                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

                for block in tool_use_blocks:
                    logger.info(
                        "legion_tool_call",
                        extra={
                            "request_id": request_id,
                            "worker": worker.worker_id,
                            "tool": block.name,
                            "tool_id": block.id,
                        },
                    )

                # Execute all tools in parallel (research skills are read-only
                # annotated — Rule 9 makes this safe).
                exec_tasks = [
                    self._registry.execute(block.name, block.input, context)
                    for block in tool_use_blocks
                ]
                results = await asyncio.gather(*exec_tasks)

                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": res,
                    }
                    for block, res in zip(tool_use_blocks, results)
                ]
                messages.append({"role": "user", "content": tool_results})

            elif stop_reason in ("max_tokens", "pause_turn"):
                messages.append(
                    {"role": "user", "content": [{"type": "text", "text": "Continue."}]}
                )

            else:
                logger.warning(
                    "legion_unknown_stop",
                    extra={
                        "request_id": request_id,
                        "worker": worker.worker_id,
                        "stop_reason": stop_reason,
                    },
                )
                return f"Worker stopped unexpectedly (reason: {stop_reason})."

    # ── Background mode ───────────────────────────────────────────────────────

    async def _launch_background(
        self,
        *,
        worker: LegionnaireDef,
        model: str,
        tools: list[dict],
        description: str,
        prompt: str,
        context: "AgentContext",
    ) -> str:
        live = sum(1 for t in self._background if not t.done())
        if live >= MAX_LEGION_BACKGROUND:
            return (
                f"Refused: already running {live} background legionnaires (max "
                f"{MAX_LEGION_BACKGROUND}). Wait for one to finish, or run this "
                "one inline."
            )

        # Capture plain values only — the request-scoped db/context must not
        # outlive the request. The loop needs a context solely for tool
        # execution routing, so hand it a detached shallow stand-in.
        msg_id = await self._log_start(
            request_id=context.request_id,
            from_agent=context.agent_id,
            worker_id=worker.worker_id,
            task=f"{description} — {prompt}",
            origin_session_id=context.extra.get("room_session_id") or context.session_id,
        )
        bg_context = _detached_context(context)

        async def _run_and_finish() -> None:
            started = time.monotonic()
            try:
                result = await self._loop(
                    worker=worker, model=model, tools=tools,
                    description=description, prompt=prompt,
                    request_id=context.request_id, context=bg_context,
                )
                status = "ok"
            except asyncio.CancelledError:
                # Shutdown cancelled the worker. Close the ticket here — nothing
                # else will, and a ticket left "running" claims to be working
                # forever in the comms tray.
                await self._log_finish(
                    msg_id, status="cancelled",
                    result="Cancelled — the backend shut down while this worker was running.",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                raise
            except Exception as e:  # noqa: BLE001
                result = f"Background worker failed: {e}"
                status = "error"
                logger.error(
                    "legion_background_error",
                    extra={"request_id": context.request_id, "worker": worker.worker_id, "error": str(e)},
                )
            _spend = bg_context.extra.get("token_usage") or {}
            logger.info(
                "legion_background_cost",
                extra={
                    "request_id": context.request_id,
                    "worker": worker.worker_id,
                    "model": model,
                    "status": status,
                    "input_read": _spend.get("input", 0),
                    "billable_input": _spend.get("billable_input", 0),
                    "cache_read": _spend.get("cache_read", 0),
                    "output": _spend.get("output", 0),
                },
            )
            await self._log_finish(
                msg_id, status=status, result=result,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

            # Report in. The ticket is written FIRST so the result is durable
            # even if the reporting turn cannot start (registry at capacity,
            # provider down) — the owner can still retrieve it with
            # legion_status. Cancellation is not reported: that path is a
            # shutdown, not a finding, and it returns above before reaching here.
            if self._report_hook is not None:
                try:
                    await self._report_hook(
                        agent_id=context.agent_id,
                        worker_id=worker.worker_id,
                        task=description or prompt,
                        result=result,
                        status=status,
                        ticket=msg_id,
                    )
                except Exception as e:  # noqa: BLE001 — never break on delivery
                    logger.error(
                        "legion_report_failed",
                        extra={
                            "request_id": context.request_id,
                            "worker": worker.worker_id,
                            "error": str(e),
                        },
                    )

        task = asyncio.create_task(_run_and_finish())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        ticket = f"#{msg_id}" if msg_id else "(untracked)"
        return (
            f"Background legionnaire deployed: {worker.worker_id} on '{description}' "
            f"— ticket {ticket}. The result is NOT available yet, so never guess or "
            "fabricate it. When the worker finishes you will be woken with its "
            "findings and you will deliver them to the owner then — so tell him it "
            "is running and that you will report back, and do NOT promise to check "
            "on it yourself or ask him to remind you. legion_status is only for "
            "when he asks before it lands."
        )

    async def shutdown(self) -> None:
        """Cancel in-flight background workers on app shutdown."""
        for task in list(self._background):
            if not task.done():
                task.cancel()
        self._background.clear()

    # ── Tickets (agent_messages, kind="legion") ───────────────────────────────

    async def _log_start(
        self, *, request_id: str, from_agent: str, worker_id: str, task: str,
        origin_session_id: int | None = None,
    ) -> int | None:
        try:
            from app.database import AsyncSessionLocal
            from app.models.agent_message import AgentMessage

            async with AsyncSessionLocal() as db:
                row = AgentMessage(
                    request_id=request_id,
                    from_agent=from_agent,
                    to_agent=f"legion/{worker_id}",
                    kind="legion",
                    protocol="direct",
                    task=task[:4000],
                    status="running",
                    origin_session_id=origin_session_id,
                    created_at=datetime.utcnow(),
                )
                db.add(row)
                await db.commit()
                return row.id
        except Exception as e:  # noqa: BLE001 — telemetry, never load-bearing
            logger.warning("legion_ticket_log_failed", extra={"error": str(e)})
            return None

    async def _log_finish(
        self, msg_id: int | None, *, status: str, result: str, duration_ms: int,
    ) -> None:
        if msg_id is None:
            return
        try:
            from app.database import AsyncSessionLocal
            from app.models.agent_message import AgentMessage

            async with AsyncSessionLocal() as db:
                row = await db.get(AgentMessage, msg_id)
                if row is None:
                    return
                row.status = status
                row.result = result[:8000]
                row.duration_ms = duration_ms
                await db.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("legion_ticket_log_failed", extra={"error": str(e)})


def _detached_context(context: "AgentContext"):
    """A copy of the request context safe to outlive the request: same routing
    identity (agent_id, request_id, model, allowlist), no db, no history."""
    from app.core.context import AgentContext

    return AgentContext(
        agent_id=context.agent_id,
        user_id=context.user_id,
        session_id=context.session_id,
        request_id=context.request_id,
        triggered_by=context.triggered_by,
        trigger_payload={},
        output_mode="silent",
        model=context.model,
        system_prompt="",
        conversation_history=[],
        db=None,
        timezone=context.timezone,
        extra={"tool_allowlist": context.extra.get("tool_allowlist"),
               "active_servers": set(context.extra.get("active_servers", set())),
               # Its own tally: a detached worker outlives the parent turn's
               # counter, so it accumulates here and is logged on completion.
               "token_usage": {"input": 0, "output": 0, "billable_input": 0,
                               "cache_read": 0, "cache_write": 0}},
    )
