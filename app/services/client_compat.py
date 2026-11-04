"""Translate existing client contracts to the coordinator's durable authority.

Socket lifetime does not own execution. Client IDs name admissions, while every
status, cancellation and event is read from the coordinator's original records.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from uuid import uuid4, uuid5, NAMESPACE_URL
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from app.models.client_compat import ClientTurn, ClientSession, HiddenMessage, ClientReplay
from app.models.coordination import Room, RoomEvent, CoordinationRun, AgentStep
from app.models.coordination import Project
from app.models.session import Session
from app.models.message import Message
from app.schemas.coordination import RoomInput
from app.services.coordination_budget import CoordinationError, digest


class ClientCompatibility:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.sessions = coordinator.sessions

    def presentation_id(self, agent):
        return "warroom" if agent.lower().strip() == "warroom" else self.coordinator.profiles.canonical(agent)

    async def selected_project(self, owner, body, agent='speda'):
        if not body.cwd and not body.workshop_project_id and not body.project_id:return None
        projects=self.coordinator.projects
        if not projects:
            raise CoordinationError('project_unconfigured','Native project execution is not configured',503)
        projects.configured()
        selected=None
        if body.project_id:
            from app.services.project_workspace import ProjectWorkspace
            native_id = await ProjectWorkspace(self.coordinator).resolve(owner, agent, body.project_id,
                new_chat=body.session_id is None)
            async with self.sessions() as db:
                selected = await db.get(Project, native_id)
        if body.workshop_project_id:
            async with self.sessions() as db:
                requested=await db.scalar(select(Project).where(Project.user_id == owner,
                    (Project.id == body.workshop_project_id) | (Project.legacy_id == body.workshop_project_id)))
            if requested is None:
                raise CoordinationError('not_found','Selected native project is unavailable; register its approved workspace first',404)
            if selected and selected.id != requested.id:
                raise CoordinationError('project_binding', 'Project selections disagree', 409)
            selected = requested
            await projects.owned(owner,selected.id)
        if body.cwd:
            from pathlib import PurePosixPath
            from app.schemas.coordination import CreateProject
            path=body.cwd
            wire=PurePosixPath(path)
            if wire.parts[:3] == ('/','Forge','workspaces'):
                if '..' in wire.parts or '\\' in path:
                    raise CoordinationError('project_scope','Workspace path left its approved mount',422)
                path=str(projects.root.joinpath(*wire.parts[3:]))
            resolved=projects.path(path)
            if selected and str(resolved) != selected.path:
                raise CoordinationError('project_binding','Workspace path does not match the selected project',409)
            if selected is None:
                result=await projects.create(owner,CreateProject(name=resolved.name,path=str(resolved)))
                return result['id']
        return selected.id if selected else None

    async def websocket(self, socket):
        import hmac
        from app.config import settings
        from app.schemas.client_compat import ClientChat
        from starlette.websockets import WebSocketDisconnect
        key = socket.headers.get('x-api-key', '')
        if not key or not hmac.compare_digest(key, settings.speda_api_key):
            await socket.close(code=1008)
            return
        await socket.accept()
        try:
            while True:
                incoming = await socket.receive_json()
                try:
                    body = ClientChat.model_validate(incoming)
                    result = await self.start(1, incoming.get('agent_id', 'speda'), body)
                    async for frame in self.stream(1, result['request_id']):
                        if frame.startswith('data: '):
                            await socket.send_text(frame[6:].strip())
                except (ValueError, TypeError, CoordinationError) as exc:
                    await socket.send_json({'type':'error', 'data':{'message':str(exc)}})
        except WebSocketDisconnect:
            return

    async def start(self, owner, agent, body, *, structured=False):
        presentation_id = self.presentation_id(agent)
        aid = "speda" if presentation_id == "warroom" else presentation_id
        # Do not silently discard original UI operations while their adapters
        # are being integrated. No history or paid work is changed on rejection.
        unsupported = [key for key in ("temperature",)
            if getattr(body, key) is not None and getattr(body, key) != '']
        if unsupported:
            raise CoordinationError("compatibility_pending", "Client adapter pending for: " + ", ".join(unsupported), 422)
        if not body.message.strip() and not (body.attachments or body.documents or body.regenerate):
            raise CoordinationError("empty_message", "A message is required", 422)
        request_id = body.request_id or str(uuid4())
        request_body = body.model_dump(exclude={"request_id"})
        for added_field in ('addressed_agent_id', 'reply_to'):
            if request_body[added_field] is None:
                request_body.pop(added_field)
        request_hash = digest({"agent": presentation_id, "body": request_body,
            **({'format': 'room-v1'} if structured else {})})
        # An exact retry acknowledges an existing run even if spending was paused
        # afterwards. It cannot create another call or change the saved input.
        async with self.sessions() as db:
            previous = await db.get(ClientTurn, (owner, request_id))
            if previous:
                if previous.request_hash != request_hash:
                    raise CoordinationError("input_conflict", "Request ID belongs to different input", 409)
                run = await db.scalar(select(CoordinationRun).where(CoordinationRun.user_id == owner,
                    CoordinationRun.room_id == previous.room_id, CoordinationRun.input_id == request_id))
                if run:
                    return {"request_id":request_id, "session_id":previous.session_id,
                        "run_id":run.id, "status":run.status, "duplicate":True}
        # Preflight policy before creating presentation state.
        _, spending = await self.coordinator.budget.policy(owner)
        if spending.paused:
            raise CoordinationError("budget_paused", "Configure and unpause the coordination budget before paid work")
        await self.coordinator.preflight_shared_spending(owner, aid, body, images=bool(body.attachments))
        project_id=await self.selected_project(owner,body,presentation_id)
        if project_id and self.coordinator.projects:
            async with self.sessions() as db:
                await self.coordinator.projects.cloud.binding(db, project_id)
        from app.services.room_attachments import Attachments, prepare_client_attachment
        prepared = []
        replay_id = None
        for attachment in body.attachments:
            prepared.append(await asyncio.to_thread(prepare_client_attachment, attachment, image=True))
        for document in body.documents:
            prepared.append(await asyncio.to_thread(prepare_client_attachment, document))
        try:
            async with self.sessions() as db, db.begin():
                mapping = await db.get(ClientTurn, (owner, request_id))
                if mapping is None:
                    if body.session_id is not None:
                        await db.execute(update(Session).where(Session.id == body.session_id, Session.user_id == owner)
                                         .values(title=Session.title))
                        session = await db.get(Session, body.session_id)
                        if session is None or session.user_id != owner:
                            raise CoordinationError("not_found", "Conversation unavailable", 404)
                        if self.presentation_id(session.agent_id) != presentation_id:
                            raise CoordinationError("agent_mismatch", "Conversation belongs to another agent", 409)
                        presentation = await db.get(ClientSession, session.id)
                        if presentation and presentation.hidden:
                            raise CoordinationError("not_found", "Conversation unavailable", 404)
                    else:
                        session = Session(user_id=owner, agent_id=presentation_id, triggered_by="user",
                                          model_used=body.model or "unassigned", title=body.message[:80])
                        db.add(session)
                        await db.flush()
                    room = await db.scalar(select(Room).where(Room.session_id == session.id, Room.user_id == owner))
                    if room is None:
                        room = Room(id=uuid5(NAMESPACE_URL, f"speda:{owner}:session:{session.id}").hex,
                            user_id=owner, session_id=session.id, title=session.title or "Conversation", lead=aid,
                            members=list(self.coordinator.profiles.profiles) if presentation_id == "warroom" else [aid],
                            project_id=project_id)
                        db.add(room)
                        await db.flush()
                        # Existing originals stay untouched. Only reconstruct the
                        # bounded operational conversation view, with attribution.
                        history = list((await db.scalars(select(Message).where(Message.session_id == session.id)
                            .order_by(Message.id.desc()).limit(64))).all())
                        for message in reversed(history):
                            text = self.text(message.content)
                            if text:
                                await self.coordinator.event(db, room, "user_message" if message.role == "user" else "message",
                                    "owner" if message.role == "user" else aid,
                                    {"text": text, "message_id": message.id, "imported_view": True})
                        if presentation_id == 'warroom' and not history:
                            for member in room.members:
                                if member != room.lead:
                                    await self.coordinator.event(db, room, 'membership', 'owner',
                                        {'action': 'invite', 'agent_id': member})
                    if project_id and room.project_id != project_id:
                        if room.project_id:
                            raise CoordinationError('project_binding','This chat is bound to another project; start a new chat to select a different workspace',409)
                        room.project_id=project_id
                        await self.coordinator.event(db,room,'project_bound','owner',{'project_id':project_id})
                    if body.keep_messages is not None or body.regenerate:
                        if room.active_run:
                            raise CoordinationError('conversation_busy', 'Stop the active reply before editing or regenerating', 409)
                        visible = list(await db.scalars(self.visible_messages(session.id)))
                        originals = visible
                        if structured:
                            consult_ids = set(await db.scalars(select(AgentStep.id).join(CoordinationRun,
                                CoordinationRun.id == AgentStep.run_id).where(
                                    CoordinationRun.room_id == room.id, AgentStep.mode == 'consult')))
                            visible = [m for m in visible if not any(isinstance(b, dict) and
                                b.get('type') == '_speaker' and b.get('step_id') in consult_ids
                                for b in m.content if isinstance(m.content, list))]
                        if body.keep_messages is not None:
                            if body.keep_messages > len(visible):
                                raise CoordinationError('history_boundary', 'Edit boundary exceeds visible history', 422)
                            cutoff = visible[body.keep_messages - 1].id if body.keep_messages else 0
                            removed = [m for m in originals if m.id > cutoff] if structured else visible[body.keep_messages:]
                            for message in removed:
                                db.add(HiddenMessage(session_id=session.id, message_id=message.id, request_id=request_id))
                            visible = visible[:body.keep_messages]
                        if body.regenerate:
                            original = next((m for m in reversed(visible) if m.role == 'user' and self.text(m.content)), None)
                            if original is None:
                                raise CoordinationError('regenerate_empty', 'No owner message remains to regenerate', 422)
                            replay_id = original.id
                    if replay_id:
                        db.add(ClientReplay(user_id=owner, request_id=request_id, message_id=replay_id))
                    mapping = ClientTurn(user_id=owner, request_id=request_id, request_hash=request_hash,
                        room_id=room.id, session_id=session.id, agent_id=presentation_id)
                    db.add(mapping)
        except IntegrityError:
            # A concurrent retry may have won admission. Only exact identity is reusable.
            async with self.sessions() as db:
                mapping = await db.get(ClientTurn, (owner, request_id))
                if mapping is None:
                    raise CoordinationError("admission_conflict", "Conversation changed; retry the same request", 409)
        if mapping.request_hash != request_hash:
            raise CoordinationError("input_conflict", "Request ID belongs to different input", 409)
        async with self.sessions() as db:
            replay = await db.get(ClientReplay, (owner, request_id))
            replay_id = replay.message_id if replay else None
        attachment_ids = await Attachments(self.coordinator).store_client(owner, mapping.room_id, request_id, prepared) if prepared else []
        result = await self.coordinator.start(owner, mapping.room_id, RoomInput(input_id=request_id,
            message=body.message or '[Attached files]', agent_id=body.addressed_agent_id, model=body.model, thinking=body.thinking,
            reply_to=body.reply_to,
            attachments=attachment_ids, replay_message_id=replay_id, owner_instructions=body.system_prompt or ''),client_context=body.client_context)
        return {"request_id": request_id, "session_id": mapping.session_id, **result}

    @staticmethod
    def text(content):
        if isinstance(content, str):
            return content
        return "\n".join(block.get("text", "") for block in content if block.get("type") == "text")

    async def resolve(self, owner, request_id):
        async with self.sessions() as db:
            mapping = await db.get(ClientTurn, (owner, request_id))
            if mapping is None:
                raise CoordinationError("not_found", "Turn unavailable", 404)
            run = await db.scalar(select(CoordinationRun).where(CoordinationRun.room_id == mapping.room_id,
                CoordinationRun.input_id == mapping.request_id, CoordinationRun.user_id == owner))
            return mapping, run

    async def stream(self, owner, request_id, *, structured=False):
        mapping, run = await self.resolve(owner, request_id)
        if run is None:
            raise CoordinationError("admission_pending", "Turn admission did not finish; resend the same request", 409)
        def encode(kind, data):
            return "data: " + json.dumps({"type": kind, "data": data, "session_id": mapping.session_id,
                                         "request_id": request_id}, ensure_ascii=False) + "\n\n"
        async with self.sessions() as db:
            owner_step = await db.scalar(select(AgentStep.id).where(AgentStep.run_id == run.id, AgentStep.operation_id == 'owner'))
            room = await self.coordinator.owned_room(db, owner, mapping.room_id)
        yield encode("start", {"agent_id": mapping.agent_id, "room_id": mapping.room_id, 'step_id': owner_step,
            **({'room': self.coordinator.room_view(room), 'run_id': run.id} if structured else {})})
        cursor = 0
        private = set()
        while True:
            page = await self.coordinator.events(owner, mapping.room_id, cursor)
            for event in page["events"]:
                cursor = event["sequence"]
                if event["run_id"] != run.id:
                    continue
                kind, payload = event["kind"], event["payload"]
                if structured:
                    if kind == 'speaker_started' and payload.get('mode') == 'consult':
                        private.add(event['step_id'])
                    if event['step_id'] in private or kind == 'consult_result':
                        continue
                    if kind in {'user_message', 'speaker_started', 'delta', 'message', 'membership', 'thinking',
                            'tool', 'tool_result', 'file', 'contribution_stopped', 'contribution_skipped'}:
                        yield encode('room_event', event)
                        continue
                if kind == "speaker_started" and not structured and (mapping.agent_id == "warroom" or event["speaker"] != mapping.agent_id):
                    speaker = self.coordinator.profiles.require(event["speaker"])
                    yield encode("chunk", f"\n\n### {speaker.name}\n\n")
                elif kind == "delta":
                    yield encode("chunk", payload["text"])
                elif kind in {"tool", "tool_result", "file", "thinking", "policy"}:
                    yield encode(kind, payload["data"])
                elif self.coordinator.workers and (kind == 'worker_event' or kind.startswith('project_')
                        or kind in {'task_cancel_requested','task_input_queued','task_input_delivered'}):
                    execution_id=payload.get('execution_id',payload.get('task_id'))
                    if execution_id:
                        snapshot=await self.coordinator.workers.inspect(owner,execution_id)
                        projected=self.coordinator.workers.envelope(event,snapshot)
                        if projected:yield encode('subagent',projected)
                elif kind == "run_stopped":
                    yield encode("error", {"message": payload.get("reason", payload["status"]), "code": payload["status"]})
                    yield encode("done", {"status": payload["status"]})
                    return
                elif kind == "round_finished":
                    yield encode("done", {"status": payload["status"]})
                    return
            if page["events"]:
                continue
            _, current = await self.resolve(owner, request_id)
            if current.status in {"succeeded", "partial", "cancelled", "failed", "expired", "reconciling", "waiting_tasks"}:
                if current.status not in {"succeeded", "waiting_tasks"}:
                    yield encode("error", {"message": "Turn ended: " + current.status, "code": current.status})
                yield encode("done", {"status": current.status})
                return
            try:
                async with self.coordinator._changed:
                    await asyncio.wait_for(self.coordinator._changed.wait(), 1)
            except TimeoutError:
                yield ": keepalive\n\n"

    async def active(self, owner, session_id=None, agent_id=None):
        async with self.sessions() as db:
            stmt = select(ClientTurn, CoordinationRun).join(CoordinationRun,
                (CoordinationRun.room_id == ClientTurn.room_id) & (CoordinationRun.input_id == ClientTurn.request_id))
            stmt = stmt.where(ClientTurn.user_id == owner, CoordinationRun.user_id == owner,
                CoordinationRun.status.in_(["queued", "waiting_room", "running"]))
            if session_id is not None:
                stmt = stmt.where(ClientTurn.session_id == session_id)
            if agent_id:
                stmt = stmt.where(ClientTurn.agent_id == self.presentation_id(agent_id))
            return [{"request_id": turn.request_id, "session_id": turn.session_id, "agent_id": turn.agent_id,
                "started_at": run.created_at.isoformat(), "status": run.status, "steerable": run.status == 'running',
                "running_s": max(0, time.time() - run.created_at.replace(tzinfo=timezone.utc).timestamp()), "idle_s": 0}
                for turn, run in (await db.execute(stmt)).all()]

    async def owned_session(self, db, owner, session_id):
        session = await db.get(Session, session_id)
        if session is None or session.user_id != owner:
            raise CoordinationError("not_found", "Conversation unavailable", 404)
        presentation = await db.get(ClientSession, session_id)
        if presentation and presentation.hidden:
            raise CoordinationError("not_found", "Conversation unavailable", 404)
        return session

    async def list_sessions(self, owner, agent, limit, before=None, project_native_id=None):
        aid = self.presentation_id(agent)
        ids = [aid] if aid == "warroom" else [aid, self.coordinator.profiles.require(aid).stable_id]
        async with self.sessions() as db:
            stmt = select(Session).outerjoin(ClientSession).where(Session.user_id == owner, Session.agent_id.in_(ids),
                (ClientSession.hidden.is_(None)) | (ClientSession.hidden.is_(False))).order_by(Session.started_at.desc(), Session.id.desc()).limit(limit)
            if project_native_id:
                stmt = stmt.join(Room, Room.session_id == Session.id).where(Room.project_id == project_native_id,
                    Room.user_id == owner)
            if before is not None:
                from sqlalchemy import or_, and_
                anchor = await self.owned_session(db, owner, before)
                if anchor.agent_id not in ids:
                    raise CoordinationError('agent_mismatch', 'History cursor belongs to another agent', 409)
                stmt = stmt.where(or_(Session.started_at < anchor.started_at,
                    and_(Session.started_at == anchor.started_at, Session.id < anchor.id)))
            sessions = list(await db.scalars(stmt))
            from app.models.client_compat import ClientProject
            bindings = {sid: (native, pid, name, cwd, host) for sid, native, pid, name, cwd, host in (await db.execute(
                select(Room.session_id, Room.project_id, ClientProject.id, ClientProject.name, Project.path, Project.host_id)
                .outerjoin(Project, (Project.id == Room.project_id) & (Project.user_id == owner))
                .outerjoin(ClientProject, (ClientProject.native_id == Room.project_id)
                    & (ClientProject.user_id == owner) & (ClientProject.agent_id == aid))
                .where(Room.user_id == owner, Room.session_id.in_([s.id for s in sessions])))).all()}
            return [{"id": s.id, "title": s.title, "started_at": s.started_at.isoformat(),
                "project_id": bindings.get(s.id, (None,None,None))[1],
                "project_name": bindings.get(s.id, (None,None,None))[2],
                "workshop_project_id": bindings.get(s.id, (None,None,None))[0],
                "cwd": bindings.get(s.id, (None,None,None,None,None))[3],
                "host_id": bindings.get(s.id, (None,None,None,None,None))[4],
                "tokens_in": s.token_count_input or 0, "tokens_out": s.token_count_output or 0}
                for s in sessions]

    async def room_for_session(self, owner, session_id, agent_id=None):
        async with self.sessions() as db:
            session = await self.owned_session(db, owner, session_id)
            if agent_id and self.presentation_id(session.agent_id) != self.presentation_id(agent_id):
                raise CoordinationError('agent_mismatch', 'Conversation belongs to another channel', 409)
            room = await db.scalar(select(Room).where(Room.session_id == session_id, Room.user_id == owner))
            return self.coordinator.room_view(room) if room else None

    async def messages(self, owner, session_id, *, structured=False):
        from app.services.chat_history import rows_from_messages
        async with self.sessions() as db:
            session = await self.owned_session(db, owner, session_id)
            messages = list(await db.scalars(self.visible_messages(session_id)))
            rows = rows_from_messages(messages)
            speakers = {str(m.id): next((b['agent_id'] for b in m.content if isinstance(b, dict)
                and b.get('type') == '_speaker'), None) for m in messages if isinstance(m.content, list)}
            for row in rows:
                speaker = speakers.get(row['id'])
                if speaker and not structured and (session.agent_id == 'warroom' or speaker != self.presentation_id(session.agent_id)):
                    row['content'] = f"### {self.coordinator.profiles.require(speaker).name}\n\n" + row['content']
                if speaker:
                    row['speaker'] = speaker
            step_ids={str(message.id):next((block.get('step_id') for block in message.content if isinstance(block,dict)
                and block.get('type') == '_speaker'),None) for message in messages if isinstance(message.content,list)}
            events, steps = [], []
            if structured:
                room = await db.scalar(select(Room).where(Room.session_id == session_id, Room.user_id == owner))
                if room:
                    events = list(await db.scalars(select(RoomEvent).where(RoomEvent.room_id == room.id).order_by(RoomEvent.sequence)))
                    steps = list(await db.scalars(select(AgentStep).join(CoordinationRun,
                        CoordinationRun.id == AgentStep.run_id).where(CoordinationRun.room_id == room.id)))
        for row in rows:
            if step_ids.get(row['id']):
                row['coordinationStepId'] = step_ids[row['id']]
        if self.coordinator.workers:
            cards=await self.coordinator.workers.history_cards(owner,session_id)
            for row in rows:
                if step_ids.get(row['id']) in cards:
                    row['subagents']=cards[step_ids[row['id']]]
                if step_ids.get(row['id']):
                    row['coordinationStepId']=step_ids[row['id']]
        if structured:
            from app.services.client_room_projection import history
            return history(rows, events, steps)
        return rows

    @staticmethod
    def visible_messages(session_id):
        return select(Message).where(Message.session_id == session_id, Message.id.not_in(
            select(HiddenMessage.message_id).where(HiddenMessage.session_id == session_id))).order_by(Message.id)

    async def export(self, owner, session_id):
        from app.models.model_policy import ToolExecutionReceipt
        from app.models.coordination import ProjectTask, ModelCallReceipt
        def record(row):
            return {column.key: (value.isoformat() if isinstance(value, datetime) else value)
                for column in row.__table__.columns for value in [getattr(row, column.key)]}
        async with self.sessions() as db, db.begin():
            session = await self.owned_session(db, owner, session_id)
            room = await db.scalar(select(Room).where(Room.session_id == session_id, Room.user_id == owner))
            if room:
                room = await self.coordinator.owned_room(db, owner, room.id, lock=True)
                if room.active_run:
                    raise CoordinationError('conversation_busy', 'Wait for the running reply before exporting', 409)
            messages = list(await db.scalars(select(Message).where(Message.session_id == session_id).order_by(Message.id)))
            runs = list(await db.scalars(select(CoordinationRun).where(CoordinationRun.room_id == room.id))) if room else []
            run_ids = [run.id for run in runs]
            steps = list(await db.scalars(select(AgentStep).where(AgentStep.run_id.in_(run_ids))))
            tasks = list(await db.scalars(select(ProjectTask).where(ProjectTask.run_id.in_(run_ids))))
            from app.models.legion import LegionExecution
            children=list(await db.scalars(select(LegionExecution).where(LegionExecution.run_id.in_(run_ids))))
            receipts = list(await db.scalars(select(ToolExecutionReceipt).where(ToolExecutionReceipt.user_id == owner,
                ToolExecutionReceipt.request_id.in_([step.id for step in steps] + [task.id for task in tasks] + [child.id for child in children]))))
            calls = list(await db.scalars(select(ModelCallReceipt).where(ModelCallReceipt.run_id.in_(run_ids))))
            events = list(await db.scalars(select(RoomEvent).where(RoomEvent.room_id == room.id).order_by(RoomEvent.sequence))) if room else []
            hidden = list(await db.scalars(select(HiddenMessage).where(HiddenMessage.session_id == session_id)))
            return {'format':'speda.chat-debug', 'version':2, 'exported_at':datetime.now(timezone.utc).isoformat(),
                'session':record(session), 'messages':[record(m) for m in messages], 'tool_calls':[record(r) for r in receipts],
                'coordination_runs':[record(r) for r in runs], 'agent_steps':[record(s) for s in steps],
                'room_events':[record(e) for e in events], 'model_calls':[record(c) for c in calls],
                'worker_executions':[record(t) for t in tasks], 'legion_executions':[record(child) for child in children],
                'hidden_messages':[record(h) for h in hidden],
                'notes':['Original records retained; hidden messages describe edits to the visible conversation.',
                         'Saved status does not prove a live executor. External files are referenced, not bundled.']}

    async def edit_session(self, owner, session_id, *, title=None, hide=False):
        async with self.sessions() as db, db.begin():
            session = await self.owned_session(db, owner, session_id)
            room = await db.scalar(select(Room).where(Room.session_id == session_id))
            if hide and room and room.active_run:
                raise CoordinationError("conversation_busy", "Stop the active reply before removing its conversation", 409)
            if title is not None:
                session.title = title
                if room:
                    room.title = title
            if hide:
                presentation = await db.get(ClientSession, session_id)
                if presentation is None:
                    presentation = ClientSession(session_id=session_id)
                    db.add(presentation)
                presentation.hidden = True
        return {"status": "ok"}

    async def cancel(self, owner, request_id):
        mapping, run = await self.resolve(owner, request_id)
        if run is None:
            return {"cancelled": False}
        if run.status in {'queued', 'waiting_room'}:
            result = await self.coordinator.cancel(owner, run.id)
            return {'cancelled':result['accepted']}
        # The existing chat stop button means stop this reply; project tasks
        # have their own stop controls and must not be cancelled by a socket/UI.
        result = await self.coordinator.stop_replies(owner, run.id)
        return {"cancelled": result['accepted']}
