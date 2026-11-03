"""Chat lifecycle outside routers; originals are durable before model execution."""
from uuid import uuid4
import asyncio
import hmac
import json

from sqlalchemy import select

from app.core.context import AgentContext
from app.models.memory_serving import MemoryChange
from app.models.message import Message
from app.models.user import User
from app.schemas.sse import SSEEventType


class ChatTurns:
    def __init__(self, session_manager, profile, orchestrator, preparer, session_factory):
        self.sessions = session_manager
        self.profile = profile
        self.orchestrator = orchestrator
        self.preparer = preparer
        self.session_factory = session_factory
        self.background = set()

    async def start(self, db, body):
        user = await db.get(User, 1)
        if user is None:
            user = User(id=1, name="Owner", timezone="Europe/Istanbul")
            db.add(user)
            await db.commit()
        effective = None
        model = self.profile.allocate_model("user")
        if self.orchestrator._model_policy:
            effective = await self.orchestrator._model_policy.resolve(model=body.model,
                request=body.thinking, user_id=user.id)
            model = effective.spec.ref
        session = await self.sessions.get_or_create(db, user.id, "user",
            model, session_id=body.session_id)
        history = await self.sessions.load_history(db, session.id)
        row = await self.sessions.save_message(db, session.id, "user", body.message)
        source = (await db.execute(select(MemoryChange.id).where(
            MemoryChange.user_id == user.id, MemoryChange.message_id == row.id
        ))).scalar_one()
        history.append({"role": "user", "content": row.content})
        context = AgentContext(user_id=user.id, session_id=session.id,
            request_id=str(uuid4()), triggered_by="user", trigger_payload={},
            output_mode="respond", model=model,
            system_prompt="", conversation_history=history, db=db, timezone=user.timezone,
            memory_source_id=source)
        context.effective_policy = effective
        context.requested_model = body.model
        context.thinking_request = body.thinking
        await self.orchestrator.admit(context)
        await db.rollback()  # End the source-lookup read transaction before the model.
        return context

    async def stream(self, context):
        async for event in self.orchestrator.run(context):
            if event.type in {SSEEventType.DONE, SSEEventType.ERROR}:
                completed = context.extra.get("completed_messages", [])
                additions = completed[len(context.conversation_history):]
                async with self.session_factory() as db, db.begin():
                    for message in additions:
                        content = message["content"]
                        if isinstance(content, str):
                            content = [{"type": "text", "text": content}]
                        db.add(Message(session_id=context.session_id, role=message["role"], content=content))
            yield event.to_sse()

    async def after_turn(self, context):
        await self.preparer.drain(context)

    async def websocket(self, socket):
        from app.config import settings
        from app.schemas.chat import ChatRequest
        from starlette.websockets import WebSocketDisconnect
        key = socket.headers.get("x-api-key", "")
        if not key or not hmac.compare_digest(key, settings.speda_api_key):
            await socket.close(code=1008)
            return
        await socket.accept()
        try:
            while True:
                incoming = await socket.receive_text()
                try:
                    body = ChatRequest.model_validate(json.loads(incoming))
                    async with self.session_factory() as db:
                        context = await self.start(db, body)
                        async for event in self.stream(context):
                            await socket.send_text(event.removeprefix("data: ").strip())
                    task = asyncio.create_task(self.after_turn(context))
                    self.background.add(task)
                    task.add_done_callback(self.background.discard)
                except (ValueError, TypeError):
                    await socket.send_json({"type":"error", "data":"Invalid chat request"})
        except WebSocketDisconnect:
            return

    async def shutdown(self):
        for task in self.background:
            task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)

    async def recover(self):
        async with self.session_factory() as db:
            users = (await db.execute(select(User))).scalars().all()
        results = []
        for user in users:
            context = AgentContext(user_id=user.id, session_id=0, request_id=str(uuid4()),
                triggered_by="user", trigger_payload={}, output_mode="silent",
                model=self.profile.allocate_model("user", is_background=True),
                system_prompt="", conversation_history=[], db=None, timezone=user.timezone)
            if self.orchestrator._model_policy:
                context.task_role = "background"
                await self.orchestrator.admit(context)
            results.append(await self.preparer.drain(context))
        return {"owners": len(users), "results": results}
