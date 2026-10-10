"""Saved Legion executions and controls for the executor owned by this app.

Codex reference: agent/control/{inspection,interrupt,mailbox,delivery}.rs.
Saved identity is independent of runtime residency. A missing live executor is
reported as unavailable; this service never resurrects work or steals a claim.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update

from app.models.session import Session
from app.models.worker_execution import WorkerCompletion, WorkerEvent, WorkerExecution, WorkerInput
from app.services.workspaces import WorkspaceError


@dataclass
class _Live:
    task: asyncio.Task
    signal: asyncio.Event
    backend: str
    interruption_sent: bool = False


class WorkerControlService:
    def __init__(self, factory, *, artifact_root: str = ""):
        self._factory = factory
        self._artifact_root = artifact_root
        self._live: dict[str, _Live] = {}
        self._changed = asyncio.Condition()
        self._version = 0

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise WorkspaceError("Invalid worker execution or message ID", status_code=400)
        return value

    async def _owned(self, db, execution_id, user_id, agent_id=None):
        row = await db.get(WorkerExecution, self._id(execution_id))
        if row is None or row.user_id != user_id or (agent_id is not None and row.agent_id != agent_id):
            raise WorkspaceError("Worker execution not found", status_code=404)
        return row

    async def begin(self, execution_id, *, context, worker, model, task, ui_run_id, background, ticket_id=None):
        # Fail closed before any provider call or Cell construction. A fresh
        # factory session avoids concurrent use of the parent's request session.
        async with self._factory() as db:
            session = await db.get(Session, context.session_id)
            if session is None or session.user_id != context.user_id or session.agent_id != context.agent_id:
                raise WorkspaceError("Worker origin session not found", status_code=404)
            selected = context.extra.get("forge_input_selection")
            inputs = [{k: item.get(k) for k in ("input_id", "name", "media_type", "artifact_hash", "size")}
                      for item in context.extra.get("forge_inputs", [])
                      if selected is None or item.get("input_id") in selected]
            db.add(WorkerExecution(
                id=self._id(execution_id), user_id=context.user_id, agent_id=context.agent_id,
                session_id=context.session_id, parent_request_id=context.request_id,
                ui_run_id=ui_run_id, ticket_id=ticket_id, worker=worker.worker_id,
                backend=worker.backend, background=background, model=model, task=task,
                workspace=context.extra.get("cwd"), workshop_project_id=context.workshop_project_id,
                inputs=inputs, last_sequence=1,
            ))
            await db.flush()
            db.add(WorkerEvent(execution_id=execution_id, sequence=1, payload={"phase": "admitted"}))
            await db.commit()
        await self._notify()

    def bind(self, execution_id, task, signal, backend):
        self._live[execution_id] = _Live(task, signal, backend)

    def unbind(self, execution_id):
        self._live.pop(execution_id, None)

    async def executor_stopped(self, execution_id):
        self.unbind(execution_id)
        await self._notify()

    async def settlement_status(self, execution_id, backend, fallback):
        if backend != "forge" or not self._artifact_root:
            return fallback
        from forge.workshop import Workshop
        try:
            store = await asyncio.to_thread(Workshop, Path(self._artifact_root), readonly=True)
            claim = await asyncio.to_thread(store.run_status, execution_id)
        except (FileNotFoundError, ValueError):
            return fallback  # admission failed before there was an execution claim
        except Exception:
            return "unknown"
        # The journal is a projection. The workshop remains the authority on
        # whether a possibly live Cell still owns its checkout.
        if claim["status"] in {"running", "interrupted"}:
            return "unknown"
        return {"succeeded": "completed", "failed": "failed", "cancelled": "interrupted"}[claim["status"]]

    async def _notify(self):
        async with self._changed:
            self._version += 1
            self._changed.notify_all()

    async def _append(self, db, execution_id, payload):
        sequence = (await db.execute(update(WorkerExecution)
            .where(WorkerExecution.id == execution_id, WorkerExecution.status == "running")
            .values(last_sequence=WorkerExecution.last_sequence + 1)
            .returning(WorkerExecution.last_sequence))).scalar_one_or_none()
        if sequence is None:
            raise WorkspaceError("Worker execution is already terminal")
        db.add(WorkerEvent(execution_id=execution_id, sequence=sequence, payload=payload))
        return sequence

    async def append(self, execution_id, event):
        payload = dict(event)
        # Preserve complete tool output off the worker mount when it exceeds
        # an event page's practical size. The address is hash verified on read.
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > 512 * 1024:
            if not self._artifact_root:
                raise WorkspaceError("Large worker event storage is not configured")
            from forge.artifacts import ArtifactStore
            digest = await asyncio.to_thread(ArtifactStore(Path(self._artifact_root)).put, encoded)
            payload = {"phase": event.get("phase"), "event_artifact": digest, "size": len(encoded)}
        async with self._factory() as db:
            sequence = await self._append(db, execution_id, payload)
            await db.commit()
        await self._notify()
        return sequence

    async def finish(self, execution_id, status, result):
        if status not in {"completed", "failed", "interrupted", "unknown"}:
            raise ValueError("Invalid worker terminal status")
        async with self._factory() as db:
            # Status + final event form one transaction. A failed commit never
            # advertises a saved success to the parent or subscribers.
            sequence = await self._append(db, execution_id, {"phase": "settled", "status": status, "report": result})
            await db.execute(update(WorkerExecution).where(WorkerExecution.id == execution_id)
                             .values(status=status, result=result, finished_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            row = await db.get(WorkerExecution, execution_id)
            if row.background:
                # Terminal outcome and pending parent receipt commit together.
                # No live callback or legacy ticket is needed for recovery.
                db.add(WorkerCompletion(execution_id=execution_id,
                                       request_id=f"worker-report-{execution_id}"))
            await db.commit()
        await self._notify()
        return sequence

    @staticmethod
    def _receipt(row):
        return {"message_id": row.id, "execution_id": row.execution_id, "status": row.status,
                "text": row.text, "created_at": row.created_at.isoformat(),
                "boundary_at": row.boundary_at.isoformat() if row.boundary_at else None}

    def _snapshot(self, row):
        live = self._live.get(row.id)
        available = live is not None
        running = available and not live.task.done()
        return {"execution_id": row.id, "ui_run_id": row.ui_run_id, "ticket_id": row.ticket_id,
                "agent_id": row.agent_id, "session_id": row.session_id,
                "parent_request_id": row.parent_request_id, "worker": row.worker,
                "backend": row.backend, "background": row.background, "model": row.model,
                "workshop_project_id": row.workshop_project_id, "workspace": row.workspace,
                "inputs": row.inputs, "task": row.task, "stored_status": row.status,
                "status": ("unknown" if not available else "settling" if not running else "running") if row.status == "running" else row.status,
                "executor_available": available,
                "interrupt_requested": row.interrupt_requested, "last_sequence": row.last_sequence,
                "result": row.result, "created_at": row.created_at.isoformat(),
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                "can_interrupt": running and row.status == "running",
                "can_message": running and row.status == "running" and row.backend == "forge" and not row.interrupt_requested}

    async def inspect(self, execution_id, *, user_id, agent_id=None):
        async with self._factory() as db:
            row = await self._owned(db, execution_id, user_id, agent_id)
            result = self._snapshot(row)
            inputs = (await db.execute(select(WorkerInput).where(WorkerInput.execution_id == row.id)
                                      .order_by(WorkerInput.created_at, WorkerInput.id))).scalars().all()
            result["messages"] = [self._receipt(item) for item in inputs]
            completion = await db.get(WorkerCompletion, row.id)
            result["completion"] = self._completion(completion)
            return result

    @staticmethod
    def _completion(row):
        if row is None:
            return None
        return {column.name: (getattr(row, column.name).isoformat()
                             if isinstance(getattr(row, column.name), datetime)
                             else getattr(row, column.name)) for column in row.__table__.columns}

    async def list(self, *, user_id, agent_id=None, session_id=None, limit=50):
        stmt = select(WorkerExecution).where(WorkerExecution.user_id == user_id)
        if agent_id is not None:
            stmt = stmt.where(WorkerExecution.agent_id == agent_id)
        if session_id is not None:
            stmt = stmt.where(WorkerExecution.session_id == session_id)
        async with self._factory() as db:
            rows = (await db.execute(stmt.order_by(WorkerExecution.created_at.desc(), WorkerExecution.id)
                                    .limit(max(1, min(limit, 100))))).scalars().all()
            completions = (await db.execute(select(WorkerCompletion).where(
                WorkerCompletion.execution_id.in_([row.id for row in rows])))).scalars().all() if rows else []
            receipts = {row.execution_id: self._completion(row) for row in completions}
            return [{**self._snapshot(row), "completion": receipts.get(row.id)} for row in rows]

    async def events(self, execution_id, *, user_id, agent_id=None, after=0, limit=200):
        if after < 0:
            raise WorkspaceError("Event cursor must be nonnegative", status_code=400)
        async with self._factory() as db:
            row = await self._owned(db, execution_id, user_id, agent_id)
            if after > row.last_sequence:
                raise WorkspaceError("Event cursor is ahead of this execution", status_code=400)
            events = (await db.execute(select(WorkerEvent).where(
                WorkerEvent.execution_id == execution_id, WorkerEvent.sequence > after)
                .order_by(WorkerEvent.sequence).limit(max(1, min(limit, 200))))).scalars().all()
            return {"events": [{**ev.payload, "execution_id": execution_id, "sequence": ev.sequence} for ev in events],
                    "next_cursor": events[-1].sequence if events else after,
                    "last_sequence": row.last_sequence, "execution": self._snapshot(row)}

    async def event_artifact(self, execution_id, digest, *, user_id, agent_id=None):
        async with self._factory() as db:
            await self._owned(db, execution_id, user_id, agent_id)
            found = (await db.execute(select(WorkerEvent.sequence).where(
                WorkerEvent.execution_id == execution_id,
                WorkerEvent.payload["event_artifact"].as_string() == digest).limit(1))).first()
            if not found or not self._artifact_root:
                raise WorkspaceError("Worker event artifact not found", status_code=404)
        from forge.artifacts import ArtifactStore
        return await asyncio.to_thread(ArtifactStore(Path(self._artifact_root)).read, digest)

    async def subscribe(self, execution_id, *, user_id, agent_id=None, after=0):
        while True:
            # Capture notification generation before reading durable events.
            # A commit during the read prevents sleeping past that event.
            async with self._changed:
                version = self._version
            page = await self.events(execution_id, user_id=user_id, agent_id=agent_id, after=after)
            for event in page["events"]:
                yield event
            after = page["next_cursor"]
            if after < page["last_sequence"]:
                continue
            if page["execution"]["stored_status"] != "running":
                return
            if not page["execution"]["executor_available"]:
                yield {"execution_id": execution_id, "phase": "executor_unavailable", "status": "unknown"}
                return
            async with self._changed:
                if version == self._version:
                    # A bounded wait permits disconnected clients to release
                    # their subscription. This is an event tail, not a scheduler.
                    try:
                        await asyncio.wait_for(self._changed.wait(), 15)
                    except TimeoutError:
                        pass

    async def interrupt(self, execution_id, *, user_id, agent_id=None):
        async with self._factory() as db:
            row = await self._owned(db, execution_id, user_id, agent_id)
            await db.execute(update(WorkerExecution).where(WorkerExecution.id == execution_id)
                             .values(last_sequence=WorkerExecution.last_sequence))
            await db.refresh(row)
            before = self._snapshot(row)
            if row.status != "running":
                return {"accepted": False, "execution": before}
            live = self._live.get(execution_id)
            if live is None:
                raise WorkspaceError("Executor unavailable; reconcile the execution and its Cell before retrying")
            if live.task.done():
                return {"accepted": False, "execution": before}
            if not row.interrupt_requested:
                await self._append(db, execution_id, {"phase": "interrupt_requested"})
                await db.execute(update(WorkerExecution).where(WorkerExecution.id == execution_id)
                                 .values(interrupt_requested=True))
                await db.commit()
            # A saved request is not proof the live signal was dispatched.
            # Retrying closes a cancelled-HTTP-after-commit gap, while the live
            # latch prevents repeated cancellation from breaking child cleanup.
            if not live.interruption_sent:
                live.interruption_sent = True
                live.signal.set()
                if live.backend != "forge":
                    live.task.cancel()
            await self._notify()
            return {"accepted": True, "execution": before}

    async def message(self, execution_id, text, *, user_id, agent_id=None, message_id=None):
        text = text.strip()
        if not text or len(text) > 16000:
            raise WorkspaceError("Worker message needs 1–16000 characters", status_code=400)
        message_id = self._id(message_id or uuid.uuid4().hex)
        async with self._factory() as db:
            row = await self._owned(db, execution_id, user_id, agent_id)
            # Acquire the same row write lock as event/finish transactions before
            # checking receipt identity or terminal state (including retries).
            await db.execute(update(WorkerExecution).where(WorkerExecution.id == execution_id)
                             .values(last_sequence=WorkerExecution.last_sequence))
            await db.refresh(row)
            existing = await db.get(WorkerInput, message_id)
            if existing is not None:
                if existing.execution_id != execution_id or existing.text != text:
                    raise WorkspaceError("Message ID already belongs to different input")
                return self._receipt(existing)
            if not self._snapshot(row)["can_message"] or row.interrupt_requested:
                raise WorkspaceError("Messages require a live, uninterrupted Forge execution")
            pending = (await db.execute(select(WorkerInput.id).where(
                WorkerInput.execution_id == execution_id, WorkerInput.status == "queued"))).all()
            if len(pending) >= 64:
                raise WorkspaceError("Worker input queue is full")
            receipt = WorkerInput(id=message_id, execution_id=execution_id, text=text)
            db.add(receipt)
            await self._append(db, execution_id, {"phase": "message_queued", "message_id": message_id})
            await db.commit()
            result = self._receipt(receipt)
        await self._notify()
        return result

    async def claim_messages(self, execution_id):
        async with self._factory() as db:
            # Exactly one execution consumes its own messages. Neither a control
            # acknowledgment nor a server restart is a consumption boundary.
            await db.execute(update(WorkerExecution).where(WorkerExecution.id == execution_id)
                             .values(last_sequence=WorkerExecution.last_sequence))
            rows = (await db.execute(select(WorkerInput).where(
                WorkerInput.execution_id == execution_id, WorkerInput.status == "queued")
                .order_by(WorkerInput.created_at, WorkerInput.id))).scalars().all()
            return [(row.id, row.text) for row in rows]

    async def record_boundary(self, execution_id, ids, transcript):
        # Called AFTER insertion into the Anthropic transcript, BEFORE its next
        # provider call. Save the transcript and receipts together. "boundary"
        # means recorded input, never that a model or tool acted on it.
        async with self._factory() as db:
            await self._append(db, execution_id, {"phase": "input_boundary", "message_ids": ids,
                                                 "messages": transcript})
            await db.execute(update(WorkerInput).where(WorkerInput.execution_id == execution_id,
                WorkerInput.id.in_(ids), WorkerInput.status == "queued")
                .values(status="boundary_recorded", boundary_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            await db.commit()
        await self._notify()


class WorkerInbox:
    """Async Warden boundary source with durable, separately recorded receipts."""
    def __init__(self, service, execution_id):
        self._service = service
        self._id = execution_id
        self._claimed = []

    def __bool__(self):
        return True

    async def claim(self):
        self._claimed = await self._service.claim_messages(self._id)
        return [text for _, text in self._claimed]

    async def record_boundary(self, transcript):
        if self._claimed:
            await self._service.record_boundary(self._id, [key for key, _ in self._claimed], transcript)
            self._claimed = []
