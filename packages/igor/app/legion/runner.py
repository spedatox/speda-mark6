# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

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
import inspect
import logging
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from app.config import settings
from app.legion.run_registry import LegionRunRegistry
from app.legion.roster import (
    DEFAULT_LEGIONNAIRE,
    LEGION_ALIASES,
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

_WEB_MCP_SERVERS = frozenset({"tavily", "exa", "brave_search", "fetch", "playwright"})
_WEB_TOOL_NAMES = frozenset({
    "browser", "browser_act", "read_article", "tavily_search", "exa_search",
    "brave_search", "fetch", "search_web",
})

_MEMORY_TOOL_NAMES = frozenset({
    "search_memory", "explore_memory", "forget_observation", "record_observation",
    "search_history", "semantic_search", "recall_conversations", "memory",
    "memory_state", "memory_audit", "memory_edit", "memory_event",
    "course_memory", "read_course_memory", "inspect_patterns", "pattern_feedback",
})

_FS_MCP_SERVERS = frozenset({"filesystem"})
_FS_TOOL_NAMES = frozenset({
    "save_file", "deliver_file", "write_file", "edit_file", "create_directory",
    "list_directory", "directory_tree", "move_file", "search_files",
    "get_file_info", "list_allowed_directories",
})


class WorkerInterrupted(RuntimeError):
    """A child was interrupted without cancelling the supervising parent turn."""


class LegionRunner:
    """One instance, owned by the CapabilityRegistry (Tier 0)."""

    def __init__(
        self,
        client: "LLMClient",
        registry: "CapabilityRegistry",
        profiles: "ProfileRegistry | None",
        workspace_service=None,
        input_service=None,
        worker_control=None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._profiles = profiles
        self._workspaces = workspace_service
        self._inputs = input_service
        self._control = worker_control
        self._background: set[asyncio.Task] = set()
        self._admitting = 0
        self._inline = 0
        self._closing = False
        # Set late in the lifespan (see set_report_hook): the orchestrator and
        # turn registry it closes over do not exist yet at Tier-0 registration.
        self._report_hook = None
        # Live progress for BACKGROUND legionnaires only — inline runs ride
        # the parent turn's own SSE stream and TurnRegistry buffer for free.
        self.runs = LegionRunRegistry()
        self._forge = None

    def set_report_hook(self, hook) -> None:
        """Install the callback a finished BACKGROUND worker fires to report in.

        Kept as an injected awaitable rather than logic here: the Legion runs
        workers and must not know about sessions, delivery or the turn registry
        (Rule 1). None = the old behaviour, where a background result waits in
        its ticket until someone asks legion_status.
        """
        self._report_hook = hook

    # ── Entry point (called by registry.execute for tool "Task") ─────────────

    async def run_worker(
        self, args: dict, context: "AgentContext", *,
        tool_call_id: str | None = None, emit=None,
    ) -> str:
        if self._client is None:
            logger.error("legion_no_client", extra={"request_id": context.request_id})
            return "The Legion is unavailable: LLMClient was not injected into the registry."

        description = args.get("description", "")
        prompt = args.get("prompt", "")
        worker_key = args.get("legionnaire") or DEFAULT_LEGIONNAIRE
        worker_key = LEGION_ALIASES.get(worker_key, worker_key)
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

        if args.get("workshop_project_id"):
            from app.services.workspaces import WorkspaceError
            if self._workspaces is None or context.db is None:
                return "Refused: durable desk selection is unavailable in this execution context."
            try:
                await self._workspaces.prepare(context, project_id=args["workshop_project_id"], explicit=True)
                if self._inputs is not None:
                    await self._inputs.prepare(context)
            except WorkspaceError as exc:
                return f"Refused: {exc}"

        selection = args.get("input_ids")
        if selection is not None:
            available = {item.get("input_id") for item in context.extra.get("forge_inputs", [])}
            if not isinstance(selection, list) or any(not isinstance(value, str) or value not in available for value in selection):
                return "Refused: an input ID is unavailable in this conversation's engineering desk."
        context.extra["forge_input_selection"] = selection

        if args.get("run_in_background"):
            return await self._launch_background(
                worker=worker, model=model, tools=tools,
                description=description, prompt=prompt, context=context,
            )

        # The run id a live panel keys on is the tool_use block's own id when
        # available (the orchestrator always has one) — that makes the
        # frontend's ToolBadge → SubagentRun correlation a plain lookup with
        # no separate id scheme. Only synthetic callers without one (tests,
        # a worker's own nested tool calls never reach here) need the fallback.
        run_id = tool_call_id or f"legion-{uuid.uuid4().hex[:10]}"
        if self._closing or self._worker_count() >= settings.legion_max_background_workers:
            return "Refused: worker execution capacity is unavailable. Wait for a running worker to finish."
        self._inline += 1
        try:
            return await self._controlled_loop(
                worker=worker, model=model, tools=tools,
                description=description, prompt=prompt,
                request_id=context.request_id, context=context,
                run_id=uuid.uuid4().hex if self._control else run_id,
                ui_run_id=run_id, emit=emit, background=False,
            )
        finally:
            self._inline -= 1

    def _worker_count(self) -> int:
        return self._admitting + self._inline + sum(not t.done() for t in self._background)

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

    def _is_tool_permitted(self, tool_name: str) -> bool:
        kind, owner = self._registry.tool_owner(tool_name)
        if not settings.legion_allow_web_search:
            if kind == "mcp" and owner.lower() in _WEB_MCP_SERVERS:
                return False
            if tool_name in _WEB_TOOL_NAMES or tool_name.startswith("browser_"):
                return False
        if not settings.legion_allow_memory_read:
            if tool_name in _MEMORY_TOOL_NAMES:
                return False
        if not settings.legion_allow_file_system:
            if kind == "mcp" and owner.lower() in _FS_MCP_SERVERS:
                return False
            if tool_name in _FS_TOOL_NAMES:
                return False
        return True

    @staticmethod
    def _worker_max_iterations(worker: LegionnaireDef) -> int:
        budgets = {
            "scout": settings.legion_max_iterations_scout,
            "researcher": settings.legion_max_iterations_researcher,
            "analyst": settings.legion_max_iterations_analyst,
            "judge": settings.legion_max_iterations_judge,
            "archivist": settings.legion_max_iterations_archivist,
            "general": settings.legion_max_iterations_general,
        }
        return budgets.get(worker.worker_id, worker.max_iterations)

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
        tools = [t for t in tools if self._is_tool_permitted(t["name"])]

        if worker.tool_scope is not None:
            return [t for t in tools if t["name"] in worker.tool_scope]

        is_read_only = worker.read_only
        if worker.worker_id == "general" and not settings.legion_general_allow_mutating:
            is_read_only = True

        if not is_read_only:
            return tools

        research_servers = {
            s.strip().lower()
            for s in settings.legion_research_mcp_servers.split(",")
            if s.strip()
        }
        allowed_mcp = research_servers if worker.mcp_servers else frozenset()

        kept = []
        for t in tools:
            kind, owner = self._registry.tool_owner(t["name"])
            if kind == "skill" and self._registry.skill_is_read_only(t["name"]):
                kept.append(t)
            elif kind == "mcp" and owner.lower() in allowed_mcp:
                kept.append(t)
        return kept

    # ── The worker loop ───────────────────────────────────────────────────────

    @staticmethod
    async def _safe_emit(emit, event: dict) -> None:
        """Push one progress event to the live panel. Best-effort: a UI-side
        callback must never break a worker over telemetry it doesn't need."""
        if emit is None:
            return
        try:
            published = emit(event)
            if inspect.isawaitable(published):
                await published
        except Exception as e:  # noqa: BLE001
            if getattr(emit, "_durable", False):
                raise
            logger.warning("legion_emit_failed", extra={"error": str(e)})

    async def _controlled_loop(self, *, ui_run_id, background, ticket_id=None, pre_admitted=False, **kwargs):
        if self._control is None:
            return await self._loop(**kwargs)
        from app.execution.forge import ForgeInterrupted
        from app.services.worker_control import WorkerInbox

        execution_id = kwargs["run_id"]
        context, worker = kwargs["context"], kwargs["worker"]
        if not pre_admitted:
            await self._control.begin(execution_id, context=context, worker=worker,
                model=kwargs["model"], task=f"{kwargs['description']}\n\n{kwargs['prompt']}",
                ui_run_id=ui_run_id, background=background, ticket_id=ticket_id)
        original_emit = kwargs.pop("emit", None)
        terminal = None

        async def publish(event):
            nonlocal terminal
            event = {**event, "id": ui_run_id, "execution_id": execution_id,
                     "parent_request_id": context.request_id}
            if event.get("phase") == "finished":
                terminal = event
                return
            sequence = await self._control.append(execution_id, event)
            await self._safe_emit(original_emit, {**event, "sequence": sequence})
        # Persistence errors must propagate; optional UI callback errors do not.
        publish._durable = True

        signal = asyncio.Event()
        task = asyncio.create_task(self._loop(**kwargs, emit=publish, signal=signal,
            inbox=WorkerInbox(self._control, execution_id) if worker.backend == "forge" else None))
        self._control.bind(execution_id, task, signal, worker.backend)
        status, result = "unknown", "Worker execution did not settle."
        settled_status = None
        try:
            result = await task
            status = "failed" if terminal and terminal.get("ok") is False else "completed"
            if status == "failed":
                raise RuntimeError(result)
            return result
        except ForgeInterrupted as exc:
            status, result = "interrupted", str(exc)
            raise
        except asyncio.CancelledError:
            # Hard task cancellation does not prove a Forge Cell is gone. Its
            # runtime keeps the claim; a signal-driven stop above is definitive.
            status = "unknown" if worker.backend == "forge" else "interrupted"
            result = "Worker interrupted; reconcile its Cell before retrying." if status == "unknown" else "Worker interrupted."
            if not asyncio.current_task().cancelling():
                raise WorkerInterrupted(result)
            raise
        except Exception as exc:
            status, result = "failed", str(exc)
            raise
        finally:
            async def settle():
                nonlocal settled_status
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                try:
                    final_status = await self._control.settlement_status(execution_id, worker.backend, status)
                    # A failed journal/event write cannot be upgraded to success
                    # by the workshop's clean Cell teardown alone.
                    if status != "completed" and final_status == "completed":
                        final_status = status
                    settled_status = final_status
                    sequence = await self._control.finish(execution_id, final_status, result)
                    await self._safe_emit(original_emit, {
                        **(terminal or {}), "id": ui_run_id, "execution_id": execution_id,
                        "parent_request_id": context.request_id, "phase": "finished",
                        "status": final_status, "ok": final_status == "completed", "report": result,
                        "sequence": sequence, "source": worker.backend,
                    })
                finally:
                    await self._control.executor_stopped(execution_id)
            # Codex tasks/mod.rs owns interruption cleanup. Repeated cancellation
            # must not interrupt our owned join or journal commit either.
            cleanup = asyncio.create_task(settle())
            cancelled_during_cleanup = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled_during_cleanup = True
                    continue
            cleanup.result()
            if cancelled_during_cleanup:
                raise asyncio.CancelledError
            if status == "completed" and settled_status != "completed":
                raise RuntimeError(f"Worker outcome is {settled_status}; inspect execution {execution_id} before continuing.")

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
        run_id: str | None = None,
        emit=None,
        signal=None,
        inbox=None,
    ) -> str:
        if worker.backend == "forge":
            return await self._forge_loop(
                worker=worker, model=model, description=description, prompt=prompt,
                context=context, run_id=run_id, emit=emit, signal=signal, inbox=inbox,
            )
        from app.services.llm_client import blocks_to_dicts

        started = time.monotonic()
        await self._safe_emit(emit, {
            "id": run_id, "agent": worker.worker_id, "label": description,
            "phase": "started", "prompt": prompt, "source": "legion",
        })
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
        permitted_names = {tool["name"] for tool in tools} - WORKER_EXCLUDED_TOOLS
        # Task framing rides the first USER message, not the system prompt. Every
        # worker of a given type then shares one byte-identical system block, so
        # the provider's cache (explicit on Anthropic, implicit on the rest) can
        # serve it across a whole fan-out; appending the task made every worker's
        # prefix unique and guaranteed a cold cache on each one.
        system = worker.system_prompt
        messages[0]["content"] = f"Task: {description}\n\n{prompt}"
        iterations = 0
        max_iters = self._worker_max_iterations(worker)
        salvage: list[str] = []  # accumulated text, returned on guard trip
        # Per-worker spend. Folded onto the parent turn below so a Legion
        # deployment stops being invisible in the session's token counters.
        spend = {"input": 0, "output": 0, "billable_input": 0,
                 "cache_read": 0, "cache_write": 0}

        while True:
            if iterations >= max_iters:
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
                    guard_result = (
                        f"[PARTIAL — iteration cap ({max_iters}) reached; "
                        f"findings gathered so far:]\n{partial}"[:settings.legion_max_result_chars]
                    )
                else:
                    guard_result = (
                        f"Legion safety guard triggered after {iterations} tool iterations "
                        "with no salvageable output. Task incomplete."
                    )
                await self._safe_emit(emit, {
                    "id": run_id, "phase": "finished", "ok": False,
                    "report": guard_result, "source": "legion",
                })
                return guard_result

            response = await self._client.create_message(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=settings.chat_max_output_tokens,
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
                result = result[:settings.legion_max_result_chars]
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
                await self._safe_emit(emit, {
                    "id": run_id, "phase": "finished", "ok": True,
                    "report": result, "source": "legion",
                })
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
                    await self._safe_emit(emit, {
                        "id": run_id, "phase": "tool", "tool": block.name,
                        "tool_call_id": block.id, "input": block.input,
                        "source": "legion",
                    })

                # Codex tools/parallel.rs gates parallelism on capability facts.
                # Even when general workers may mutate, writes form barriers.
                async def _execute_indexed(index, block):
                    if block.name not in permitted_names:
                        return index, f"Error: tool '{block.name}' is outside this worker's allowed tools."
                    return index, await self._registry.execute(
                        block.name, block.input, context,
                    )

                results = [None] * len(tool_use_blocks)

                async def execute_group(indices):
                    exec_tasks = [asyncio.create_task(_execute_indexed(index, tool_use_blocks[index])) for index in indices]
                    try:
                        for completed in asyncio.as_completed(exec_tasks):
                            index, res = await completed
                            results[index] = res
                            block = tool_use_blocks[index]
                            preview = res if isinstance(res, str) else str(res)
                            event = {
                                "id": run_id, "phase": "tool_result",
                                "tool_call_id": block.id, "tool": block.name,
                                "result": preview[:1500], "source": "legion", "full_result": res,
                            }
                            if preview.startswith("Error"):
                                event["error"] = preview[:1500]
                            await self._safe_emit(emit, event)
                    finally:
                        for task in exec_tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*exec_tasks, return_exceptions=True)

                reads = []
                for index, block in enumerate(tool_use_blocks):
                    if self._registry.call_is_read_only(block.name, block.input):
                        reads.append(index)
                    else:
                        await execute_group(reads)
                        reads = []
                        await execute_group([index])
                await execute_group(reads)

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
                unknown_result = f"Worker stopped unexpectedly (reason: {stop_reason})."
                await self._safe_emit(emit, {
                    "id": run_id, "phase": "finished", "ok": False,
                    "report": unknown_result, "source": "legion",
                })
                return unknown_result

    async def _forge_loop(
        self, *, worker: LegionnaireDef, model: str, description: str,
        prompt: str, context: "AgentContext", run_id: str | None, emit=None, signal=None, inbox=None,
    ) -> str:
        from app.execution.forge import ForgeExecutor, ForgeInterrupted

        workspace = str(context.extra.get("cwd") or "").strip()
        await self._safe_emit(emit, {
            "id": run_id, "agent": worker.worker_id, "label": description,
            "phase": "started", "prompt": prompt, "source": "forge",
        })
        if not workspace:
            result = (
                "Forge needs a workspace. Select a project with workshop_update "
                "or choose a Forge workspace in the client before deploying the worker."
            )
            await self._safe_emit(emit, {
                "id": run_id, "phase": "finished", "ok": False,
                "report": result, "source": "forge",
            })
            raise RuntimeError(result)

        if self._forge is None:
            self._forge = ForgeExecutor(self._client)

        role = {
            "autobot": "coder",
            "forge_coder": "coder",
            "forge_reviewer": "reviewer",
            "decepticon": "pentester",
            "forge_pentester": "pentester",
        }[worker.worker_id]

        logger.info(
            "forge_execution_started",
            extra={
                "request_id": context.request_id, "job_id": run_id,
                "tool": "Task", "worker": worker.worker_id, "role": role,
                "backend": "forge",
            },
        )

        async def forward(event: dict) -> None:
            await self._safe_emit(emit, {"id": run_id, **event})

        try:
            result = await self._forge.run(
                job_id=run_id or f"forge-{uuid.uuid4().hex}",
                role=role,
                task=f"{description}\n\n{prompt}",
                workspace=workspace,
                model_ref=model,
                inputs=[item for item in context.extra.get("forge_inputs", [])
                        if context.extra.get("forge_input_selection") is None
                        or item.get("input_id") in context.extra["forge_input_selection"]],
                emit=forward,
                **({"signal": signal, "inbox": inbox} if signal is not None else {}),
            )
        except ForgeInterrupted:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "forge_execution_finished",
                extra={
                    "request_id": context.request_id, "job_id": run_id,
                    "tool": "Task", "worker": worker.worker_id, "role": role,
                    "backend": "forge", "status": "failed", "error": str(exc),
                },
            )
            result = f"Forge worker failed: {exc}"
            await self._safe_emit(emit, {
                "id": run_id, "phase": "finished", "ok": False,
                "report": result, "source": "forge",
            })
            # Returning an error string made _launch_background record status=ok
            # and send a success report to the parent. Preserve failure as failure.
            raise RuntimeError(result) from exc
        logger.info(
            "forge_execution_finished",
            extra={
                "request_id": context.request_id, "job_id": run_id,
                "tool": "Task", "worker": worker.worker_id, "role": role,
                "backend": "forge", "status": "completed",
            },
        )
        await self._safe_emit(emit, {
            "id": run_id, "phase": "finished", "ok": True,
            "report": result[:settings.legion_max_result_chars], "source": "forge",
        })
        return result[:settings.legion_max_result_chars]

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
        live = self._worker_count()
        if self._closing:
            return "Refused: the worker executor is shutting down."
        if live >= settings.legion_max_background_workers:
            return (
                f"Refused: already using {live} worker slots (max "
                f"{settings.legion_max_background_workers}). Wait for a worker to finish. "
                "Inline and background workers share this limit."
            )

        # Codex agent/control/execution.rs reserves capacity before asynchronous
        # startup and releases its permit on every outcome. No await before this.
        self._admitting += 1
        try:
            return await self._start_background(
                worker=worker, model=model, tools=tools,
                description=description, prompt=prompt, context=context,
            )
        finally:
            self._admitting -= 1

    async def _start_background(
        self, *, worker: LegionnaireDef, model: str, tools: list[dict],
        description: str, prompt: str, context: "AgentContext",
    ) -> str:
        from app.execution.forge import ForgeInterrupted
        # Capture plain values only — the request-scoped db/context must not
        # outlive the request. The loop needs a context solely for tool
        # execution routing, so hand it a detached shallow stand-in.
        # The conversation this deployment was ordered from — the ticket records
        # it, and the completion report goes back INTO it so the agent reads its
        # own finding with the thread that explains why it was sent.
        room_session_id = context.extra.get("room_session_id") or context.session_id
        msg_id = await self._log_start(
            request_id=context.request_id,
            from_agent=context.agent_id,
            worker_id=worker.worker_id,
            task=f"{description} — {prompt}",
            origin_session_id=room_session_id,
        )
        if msg_id is None:
            return "Refused: the worker ticket could not be saved. No background execution was started."
        if self._closing:
            await self._log_finish(msg_id, status="cancelled", result="Executor shut down during admission.", duration_ms=0)
            return "Refused: the worker executor is shutting down."
        bg_context = _detached_context(context)
        background_run_id = f"legion-bg-{msg_id}" if msg_id is not None else f"legion-bg-{uuid.uuid4().hex}"
        background_execution_id = uuid.uuid4().hex if self._control else background_run_id
        if self._control:
            try:
                await self._control.begin(background_execution_id, context=bg_context, worker=worker,
                    model=model, task=f"{description}\n\n{prompt}", ui_run_id=background_run_id,
                    background=True, ticket_id=msg_id)
            except Exception as exc:
                await self._log_finish(msg_id, status="error", result=f"Admission failed: {exc}", duration_ms=0)
                return "Refused: the worker execution could not be saved. No background execution was started."
            if self._closing:
                await self._control.finish(background_execution_id, "interrupted", "Executor shut down during admission.")
                await self._log_finish(msg_id, status="cancelled", result="Executor shut down during admission.", duration_ms=0)
                return "Refused: the worker executor is shutting down."
        if msg_id is not None:
            self.runs.register(
                msg_id, agent=worker.worker_id, label=description,
                room_session_id=room_session_id,
            )
        bg_emit = (lambda ev: self.runs.emit(msg_id, ev)) if msg_id is not None else None

        async def _run_and_finish() -> None:
            started = time.monotonic()
            try:
                result = await self._controlled_loop(
                    worker=worker, model=model, tools=tools,
                    description=description, prompt=prompt,
                    request_id=context.request_id, context=bg_context,
                    run_id=background_execution_id, ui_run_id=background_run_id,
                    background=True, ticket_id=msg_id, emit=bg_emit,
                    pre_admitted=bool(self._control),
                )
                status = "ok"
            except (WorkerInterrupted, ForgeInterrupted) as exc:
                result, status = str(exc), "cancelled"
            except asyncio.CancelledError:
                # Shutdown cancelled the worker. Close the ticket here — nothing
                # else will, and a ticket left "running" claims to be working
                # forever in the comms tray.
                await self._log_finish(
                    msg_id, status="cancelled",
                    result="Cancelled — the backend shut down while this worker was running.",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                if msg_id is not None:
                    self.runs.finish(msg_id, ok=False)
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
            if msg_id is not None:
                self.runs.finish(msg_id, ok=(status == "ok"))

            # The controlled loop committed its terminal result and completion
            # receipt together. This callback only wakes a bounded outbox drain;
            # failure cannot lose that receipt. Legacy callers without a
            # control service still use their ticket callback. Shutdown returns
            # above, leaving any saved receipt for startup/n8n recovery.
            if self._report_hook is not None:
                try:
                    await self._report_hook(
                        agent_id=context.agent_id,
                        worker_id=worker.worker_id,
                        task=description or prompt,
                        result=result,
                        status=status,
                        ticket=msg_id,
                        room_session_id=room_session_id,
                        **({"execution_id": background_execution_id} if self._control else {}),
                        **({"workspace": bg_context.extra.get("cwd"),
                            **({"execution_id": background_execution_id} if not self._control else {}),
                            "workshop_project_id": bg_context.workshop_project_id}
                           if worker.backend == "forge" else {}),
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
            f"— ticket {ticket}, execution {background_execution_id}. The result is NOT available yet, so never guess or "
            "fabricate it. When the worker finishes you will be woken with its "
            "findings and you will deliver them to the owner then — so tell him it "
            "is running and that you will report back, and do NOT promise to check "
            "on it yourself or ask him to remind you. legion_status is only for "
            "when he asks before it lands."
        )

    async def shutdown(self) -> None:
        """Cancel in-flight background workers on app shutdown."""
        self._closing = True
        await asyncio.sleep(0)  # enter newly created workers' cleanup handlers
        tasks = list(self._background)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
        workshop_project_id=context.workshop_project_id,
        extra={"tool_allowlist": context.extra.get("tool_allowlist"),
               "active_servers": set(context.extra.get("active_servers", set())),
               "cwd": context.extra.get("cwd"),
               "forge_inputs": list(context.extra.get("forge_inputs") or []),
               "forge_input_selection": context.extra.get("forge_input_selection"),
               # Its own tally: a detached worker outlives the parent turn's
               # counter, so it accumulates here and is logged on completion.
               "token_usage": {"input": 0, "output": 0, "billable_input": 0,
                               "cache_read": 0, "cache_write": 0}},
    )
