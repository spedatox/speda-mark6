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
        system = f"{worker.system_prompt}\n\nTask: {description}"
        iterations = 0
        salvage: list[str] = []  # accumulated text, returned on guard trip

        while True:
            if iterations >= worker.max_iterations:
                logger.error(
                    "legion_safety_guard",
                    extra={
                        "request_id": request_id,
                        "worker": worker.worker_id,
                        "iterations": iterations,
                        "description": description,
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
            except Exception as e:  # noqa: BLE001
                result = f"Background worker failed: {e}"
                status = "error"
                logger.error(
                    "legion_background_error",
                    extra={"request_id": context.request_id, "worker": worker.worker_id, "error": str(e)},
                )
            await self._log_finish(
                msg_id, status=status, result=result,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        task = asyncio.create_task(_run_and_finish())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        ticket = f"#{msg_id}" if msg_id else "(untracked)"
        return (
            f"Background legionnaire deployed: {worker.worker_id} on '{description}' "
            f"— ticket {ticket}. The result is NOT available yet; tell the owner it "
            "is running and check later with legion_status. Never guess or fabricate "
            "the result."
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
               "active_servers": set(context.extra.get("active_servers", set()))},
    )
