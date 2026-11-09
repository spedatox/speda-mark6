import asyncio
import json
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from app.models.coordination import CoordinationRun, Room
from app.models.message import Message
from app.models.session import Session
from app.schemas.client_compat import ClientChat
from app.services.client_compat import ClientCompatibility
from app.services.coordination_budget import CoordinationError
from app.services.session_titles import SessionTitles
from tests.test_coordination import setup, reply


@pytest.mark.parametrize("structured", [False, True])
async def test_title_is_generated_after_reply_and_visible_to_clients(memory, structured):
    from httpx import ASGITransport, AsyncClient
    from app.main import create_app

    bits = await setup(memory, [reply("Kütleçekim dalgaları uzay-zamanı büker."), reply("Elbette.")])
    calls = []
    started = asyncio.Event()
    finish_title = asyncio.Event()

    class TitleClient:
        policy = bits.policy

        async def create_message(self, **kwargs):
            calls.append(kwargs)
            started.set()
            await finish_title.wait()
            return reply('```text\nTitle: “Kütleçekim Dalgaları Rehberi”.\n```')

    bits.coordinator.titles = SessionTitles(memory.sessions, TitleClient())
    app = create_app()
    app.state.client_compat = ClientCompatibility(bits.coordinator)
    headers = {"X-API-Key": "test-only-key"}
    if structured:
        headers["X-Chat-Format"] = "room-v1"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as http:
            response = await http.post("/chat/speda", json={"message": "Kütleçekim dalgaları nasıl oluşur?"})
            assert response.status_code == 200
            frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert frames[-1]["type"] == "done"
            session_id = frames[-1]["session_id"]
            await asyncio.wait_for(started.wait(), 2)
            assert (await http.get("/sessions")).json()[0]["title"] is None

            response = await http.post("/chat/speda", json={"session_id": session_id, "message": "Bir örnek ver."})
            assert response.status_code == 200 and '"done"' in response.text
            finish_title.set()
            await asyncio.wait_for(asyncio.gather(*bits.coordinator.background), 2)

            assert len(calls) == 1
            assert calls[0]["role"] == "background"
            assert "uzay-zamanı büker" in calls[0]["messages"][0]["content"]
            assert (await http.get("/sessions")).json()[0]["title"] == "Kütleçekim Dalgaları Rehberi"
            assert (await http.get(f"/sessions/{session_id}/room")).json()["title"] == "Kütleçekim Dalgaları Rehberi"
    finally:
        await bits.coordinator.shutdown()


@pytest.mark.parametrize("rename", [False, True])
async def test_untitled_legacy_session_gets_fallback_and_keeps_manual_rename(memory, rename):
    from app.schemas.llm import ProviderError

    bits = await setup(memory, [reply("Here is the follow-up.")])
    adapter = ClientCompatibility(bits.coordinator)
    started = asyncio.Event()
    finish_title = asyncio.Event()

    class UnavailableTitleClient:
        policy = bits.policy

        async def create_message(self, **kwargs):
            started.set()
            await finish_title.wait()
            raise ProviderError("not_configured", "Title provider unavailable")

    bits.coordinator.titles = SessionTitles(memory.sessions, UnavailableTitleClient())
    async with memory.sessions() as db, db.begin():
        session = Session(user_id=1, agent_id="speda", triggered_by="user", model_used="legacy", title="")
        db.add(session)
        await db.flush()
        db.add(Message(session_id=session.id, role="user",
                       content=[{"type": "text", "text": "Compare\n renewable energy storage methods"}]))
    try:
        await adapter.start(1, "speda", ClientChat(session_id=session.id, message="Continue"))
        await asyncio.wait_for(started.wait(), 2)
        if rename:
            await adapter.edit_session(1, session.id, title="My energy notes")
        finish_title.set()
        await asyncio.wait_for(asyncio.gather(*bits.coordinator.background), 2)
        expected = "My energy notes" if rename else "Compare renewable energy storage methods"
        assert (await adapter.list_sessions(1, "speda", 10))[0]["title"] == expected
        assert (await adapter.room_for_session(1, session.id))["title"] == expected
    finally:
        await bits.coordinator.shutdown()


async def test_chat_accepts_large_powerpoint_and_redacts_invalid_attachment_input(memory):
    import base64
    import io
    from random import Random

    from httpx import ASGITransport, AsyncClient
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Inches

    from app.main import create_app

    pixels = Random(32).randbytes(1600 * 1600 * 3)
    picture = io.BytesIO()
    Image.frombytes("RGB", (1600, 1600), pixels).save(picture, format="PNG")
    picture.seek(0)
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Chapter 4 lecture"
    slide.shapes.add_picture(picture, Inches(1), Inches(2), width=Inches(2))
    output = io.BytesIO()
    presentation.save(output)
    assert 7_300_000 < output.tell() < 10 * 1024**2
    body = {"message": "Read the slides", "documents": [{
        "name": "Chapter 4.pptx", "size": output.tell(),
        "data": base64.b64encode(output.getvalue()).decode("ascii"),
    }]}

    bits = await setup(memory, [reply("I read the lecture.")])
    app = create_app()
    app.state.client_compat = ClientCompatibility(bits.coordinator)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"X-API-Key": "test-only-key", "X-Chat-Format": "room-v1"}) as http:
            ok = await http.post("/chat/ultron", json=body)
            assert ok.status_code == 200 and "I read the lecture." in ok.text
            assert "Chapter 4 lecture" in json.dumps(bits.client.calls[0]["messages"])

            body["documents"][0]["size"] = 10 * 1024**2 + 1
            rejected = await http.post("/chat/ultron", json=body)
            assert rejected.status_code == 422
            assert rejected.json() == {"code": "attachment_size", "detail": "Attachment exceeds 10 MiB"}

            body["documents"][0]["size"] = 1
            body["documents"][0]["data"] = {"private": "attachment data"}
            invalid = await http.post("/chat/ultron", json=body)
            assert invalid.status_code == 422 and "attachment data" not in invalid.text
            assert len(bits.client.calls) == 1
    finally:
        await bits.coordinator.shutdown()


async def test_chat_stream_preserves_the_safe_failure_reason(memory):
    from app.schemas.llm import ProviderError

    bits = await setup(memory, [ProviderError("not_connected", "Vendor account needs authentication")])
    adapter = ClientCompatibility(bits.coordinator)
    try:
        result = await adapter.start(1, "speda", ClientChat(message="Check my messages"))
        frames = [json.loads(f[6:]) async for f in adapter.stream(1, result["request_id"], structured=True)]
        error = next(frame["data"] for frame in frames if frame["type"] == "error")
        assert error == {"message": "Vendor account needs authentication", "code": "not_connected"}
    finally:
        await bits.coordinator.shutdown()


async def test_imported_history_sorts_by_original_date_before_allocated_id(memory):
    from datetime import datetime
    bits = await setup(memory, [])
    async with memory.sessions() as db, db.begin():
        current = Session(user_id=1, agent_id='speda', triggered_by='user', model_used='local',
            title='Current local chat', started_at=datetime(2026, 10, 29))
        imported = Session(user_id=1, agent_id='speda', triggered_by='user', model_used='old',
            title='Imported old chat', started_at=datetime(2026, 10, 10))
        db.add_all([current, imported])
    rows = await ClientCompatibility(bits.coordinator).list_sessions(1, 'speda', 10)
    assert [row['title'] for row in rows] == ['Current local chat', 'Imported old chat']
    adapter = ClientCompatibility(bits.coordinator)
    first = await adapter.list_sessions(1, 'speda', 1)
    second = await adapter.list_sessions(1, 'speda', 1, before=first[-1]['id'])
    assert second[0]['title'] == 'Imported old chat'
    assert await adapter.list_sessions(1, 'speda', 1, before=second[-1]['id']) == []
    with pytest.raises(CoordinationError):
        await adapter.list_sessions(2, 'speda', 10, before=first[-1]['id'])
    with pytest.raises(CoordinationError):
        await adapter.list_sessions(1, 'sentinel', 10, before=first[-1]['id'])
    await bits.coordinator.shutdown()


async def test_warroom_is_speda_led_coordinator_with_separate_history(memory):
    bits = await setup(memory, [reply("We can work on this together.")])
    adapter = ClientCompatibility(bits.coordinator)
    result = await adapter.start(1, "warroom", ClientChat(message="Assemble", request_id=str(uuid4())))
    task = bits.coordinator.live.get(result["run_id"])
    if task:
        await task
    mapping, _ = await adapter.resolve(1, result["request_id"])
    async with memory.sessions() as db:
        room = await db.get(Room, mapping.room_id)
        assert room.lead == "speda" and set(room.members) == set(bits.coordinator.profiles.profiles)
        assert (await db.get(Session, result["session_id"])).agent_id == "warroom"
    frames = [json.loads(f[6:]) async for f in adapter.stream(1, result["request_id"]) if f.startswith("data: ")]
    text = "".join(f["data"] for f in frames if f["type"] == "chunk")
    assert "### SPEDA" in text and "work on this together" in text
    assert (await adapter.messages(1, result["session_id"]))[-1]["speaker"] == "speda"
    assert len(await adapter.list_sessions(1, "warroom", 10)) == 1
    assert await adapter.list_sessions(1, "speda", 10) == []
    assert len(bits.client.calls) == 1  # Membership does not summon eight paid replies.
    await bits.coordinator.shutdown()


async def test_original_chat_retry_reconnect_and_history(memory):
    bits = await setup(memory, [reply("Hello from the renovated backend")])
    adapter = ClientCompatibility(bits.coordinator)
    body = ClientChat(message="Hello", request_id=str(uuid4()))
    first = await adapter.start(1, "speda", body)
    second = await adapter.start(1, "speda", body)
    assert first["run_id"] == second["run_id"]
    task = bits.coordinator.live.get(first["run_id"])
    if task:
        await task
    # A new adapter after a reconnect reads the same durable stream, no executor.
    reconnect = ClientCompatibility(bits.coordinator)
    frames = [json.loads(s[6:]) async for s in reconnect.stream(1, first["request_id"]) if s.startswith("data: ")]
    assert frames[0]["type"] == "start" and frames[-1]["type"] == "done"
    assert "".join(e["data"] for e in frames if e["type"] == "chunk") == "Hello from the renovated backend"
    assert len(bits.client.calls) == 1
    assert (await adapter.messages(1, first["session_id"]))[-1]["content"] == "Hello from the renovated backend"
    with pytest.raises(CoordinationError, match="different input"):
        await adapter.start(1, "speda", body.model_copy(update={"message": "Different"}))
    with pytest.raises(CoordinationError):
        await adapter.resolve(2, first["request_id"])
    with pytest.raises(CoordinationError):
        await adapter.messages(2, first["session_id"])
    await adapter.edit_session(1, first["session_id"], title="Renamed")
    assert (await adapter.list_sessions(1, "speda", 50))[0]["title"] == "Renamed"
    await adapter.edit_session(1, first["session_id"], hide=True)
    assert await adapter.list_sessions(1, "speda", 50) == []
    async with memory.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 2  # immutable originals
        assert await db.scalar(select(func.count()).select_from(CoordinationRun)) == 1
    await bits.coordinator.shutdown()


async def test_legacy_session_adopted_without_rewriting_originals(memory):
    bits = await setup(memory, [reply("I remember this conversation")])
    async with memory.sessions() as db, db.begin():
        session = Session(user_id=1, agent_id="nightcrawler", triggered_by="user", model_used="legacy")
        db.add(session)
        await db.flush()
        db.add(Message(session_id=session.id, role="user", content=[{"type": "text", "text": "An older message"}]))
    adapter = ClientCompatibility(bits.coordinator)
    result = await adapter.start(1, "friday", ClientChat(message="Continue", session_id=session.id))
    task = bits.coordinator.live.get(result["run_id"])
    if task:
        await task
    assert "An older message" in json.dumps(bits.client.calls[0]["messages"])
    assert len(await adapter.messages(1, session.id)) == 3
    async with memory.sessions() as db:
        assert (await db.get(Session, session.id)).agent_id == "nightcrawler"
    await bits.coordinator.shutdown()


async def test_pending_adapter_and_paused_budget_do_not_admit(memory):
    bits = await setup(memory, [])
    adapter = ClientCompatibility(bits.coordinator)
    with pytest.raises(CoordinationError, match="not configured"):
        await adapter.start(1, "speda", ClientChat(message="x", project_id=2))
    with pytest.raises(CoordinationError, match="pending"):
        await adapter.start(1, "speda", ClientChat(message="x", temperature=0.5))
    version, policy = await bits.budget.policy(1)
    await bits.budget.save(1, version, policy.model_copy(update={"paused": True}))
    with pytest.raises(CoordinationError, match="budget"):
        await adapter.start(1, "speda", ClientChat(message="x"))
    async with memory.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Room)) == 0
    assert not bits.client.calls


async def test_discovery_cannot_bypass_persona_or_worker_permissions(memory):
    from app.core.registry import CapabilityRegistry
    from app.skills.base import Skill
    from app.skills.tool_search import ToolSearchSkill
    from app.schemas.llm import ProviderError
    class Privileged(Skill):
        name, description = "private_write", "Private system administration"
        input_schema = {"type": "object"}
        restricted_to = frozenset({"orion"})
        deferred = True
        async def execute(self, args, context):
            raise AssertionError("Forbidden side effect")
    registry = CapabilityRegistry(memory.sessions)
    await registry.register_skill(Privileged())
    await registry.register_skill(ToolSearchSkill(registry))
    for context in [memory.context(agent_id="speda"), memory.context(agent_id="orion", task_role="worker")]:
        context.extra["loaded_tools"] = {"private_write"}
        assert not registry.search_tools("private", context)
        with pytest.raises(ProviderError):
            await registry.execute("private_write", {}, context)
    context = memory.context(agent_id="orion")
    assert "private_write" in registry.discovery_index(context)
    result = await registry.execute("tool_search", {"query": "private"}, context)
    assert json.loads(result)[0]["name"] == "private_write"
    assert any(t["name"] == "private_write" for t in registry.list_tools(context))


async def test_client_attachments_native_images_and_evidence_separation(memory):
    import base64
    import io
    from PIL import Image
    from app.models.memory_serving import MemoryChange
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(buffer, format='PNG')
    bits = await setup(memory, [reply('The attachment says something')])
    version, spending = await bits.budget.policy(1)
    spending.prices['kimi:kimi-k2.6'] = spending.prices['kimi:kimi-k2.6'].model_copy(update={'image_token_ceiling':10000})
    await bits.budget.save(1, version, spending)
    adapter = ClientCompatibility(bits.coordinator)
    body = ClientChat(message='Read these sources', request_id=str(uuid4()),
        attachments=[{'media_type':'image/png', 'data':base64.b64encode(buffer.getvalue()).decode()}],
        documents=[{'name':'source.txt', 'data':base64.b64encode(b'Quoted claim: the moon is green').decode()}])
    result = await adapter.start(1, 'speda', body)
    task = bits.coordinator.live.get(result['run_id'])
    if task:
        await task
    assert bits.client.calls, (await bits.coordinator.events(1, (await adapter.resolve(1, result['request_id']))[0].room_id))['events'][-1]['payload']
    content = bits.client.calls[0]['messages'][0]['content']
    assert any(b['type'] == 'image' for b in content)
    assert 'Quoted claim' in json.dumps(content)
    messages = await adapter.messages(1, result['session_id'])
    assert messages[0]['content'] == 'Read these sources'
    assert messages[0]['images'][0].startswith('data:image/png;base64,')
    assert messages[0]['uploads'][0]['name'] == 'source.txt'
    async with memory.sessions() as db:
        sources = list(await db.scalars(select(MemoryChange)))
        assert len(sources) == 1 and 'green' not in sources[0].content
    retried = await adapter.start(1, 'speda', body)
    assert retried['run_id'] == result['run_id'] and len(bits.client.calls) == 1
    await bits.coordinator.shutdown()


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize(("vision_model", "wire_key"), [
    ("openai:gpt-6.1-terra", "input_image"),
    ("zai:glm-5.3-flash", "image_url"),
    ("zai:glm-5.3-flashx", "image_url"),
])
async def test_image_rejection_retry_and_model_switch(memory, structured, vision_model, wire_key):
    from base64 import b64encode
    from io import BytesIO
    from httpx import ASGITransport, AsyncClient
    from PIL import Image
    from app.main import create_app
    from app.schemas.coordination import Price
    from app.services.llm_protocol import request_body

    buffer = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(buffer, format="PNG")
    image = {"media_type": "image/png", "data": b64encode(buffer.getvalue()).decode()}
    text_model = "zai:glm-5.3"
    bits = await setup(memory, [reply("Blue square"), reply("Image replay"), reply("Text follow-up"), reply("Still blue")])
    version, spending = await bits.budget.policy(1)
    spending.prices[text_model] = Price(microdollars_per_token=1)
    spending.prices[vision_model] = Price(microdollars_per_token=1, image_token_ceiling=10000)
    await bits.budget.save(1, version, spending)
    app = create_app()
    app.state.client_compat = ClientCompatibility(bits.coordinator)
    headers = {"X-API-Key": "test-only-key"}
    if structured:
        headers["X-Chat-Format"] = "room-v1"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as http:
            body = {"message": "Read the image", "model": text_model,
                    "attachments": [image], "request_id": str(uuid4())}
            rejected = await http.post("/chat/ultron", json=body)
            assert rejected.status_code == 422
            assert "Select a model with vision" in rejected.text
            assert not bits.client.calls
            assert (await http.get("/sessions", params={"agent_id": "ultron"})).json() == []

            accepted = await http.post("/chat/ultron", json={**body, "model": vision_model})
            assert accepted.status_code == 200 and "Blue square" in accepted.text
            session_id = (await http.get("/sessions", params={"agent_id": "ultron"})).json()[0]["id"]
            replay = {"session_id": session_id, "message": "", "keep_messages": 1, "regenerate": True}
            rejected_replay = await http.post("/chat/ultron", json={**replay, "model": text_model})
            assert rejected_replay.status_code == 422
            transcript = await app.state.client_compat.messages(1, session_id, structured=structured)
            assert transcript[1]["content"] == "Blue square"
            accepted_replay = await http.post("/chat/ultron", json={**replay, "model": vision_model})
            assert accepted_replay.status_code == 200 and "Image replay" in accepted_replay.text

            followup = {"session_id": session_id, "message": "Continue"}
            text = await http.post("/chat/ultron", json={**followup, "model": text_model})
            assert text.status_code == 200 and "Text follow-up" in text.text
            vision = await http.post("/chat/ultron", json={**followup, "model": vision_model})
            assert vision.status_code == 200 and "Still blue" in vision.text
            transcript = await app.state.client_compat.messages(1, session_id, structured=structured)
            assert transcript[0]["images"][0].startswith("data:image/png;base64,")

        initial_call, replay_call, text_call, vision_call = bits.client.calls
        assert "[Image attachment unavailable to this model.]" in json.dumps(text_call["messages"])
        for call in (initial_call, replay_call, vision_call):
            wire = request_body(call["policy"], call["system"], call["messages"], [], 1024)
            assert f'"type": "{wire_key}"' in json.dumps(wire)
        wire = request_body(text_call["policy"], text_call["system"], text_call["messages"], [], 1024)
        assert "data:image" not in json.dumps(wire)
    finally:
        await bits.coordinator.shutdown()


async def test_edit_and_regenerate_preserve_originals_and_do_not_repeat_evidence(memory):
    from app.models.memory_serving import MemoryChange
    bits = await setup(memory, [reply('First answer'), reply('Better answer'), reply('Edited answer')])
    adapter = ClientCompatibility(bits.coordinator)
    async def submit(body):
        result = await adapter.start(1, 'speda', body)
        task = bits.coordinator.live.get(result['run_id'])
        if task:
            await task
        return result
    first = await submit(ClientChat(message='Original question'))
    second = await submit(ClientChat(message='', session_id=first['session_id'], keep_messages=1, regenerate=True))
    messages = await adapter.messages(1, first['session_id'])
    assert [m['content'] for m in messages] == ['Original question', 'Better answer']
    assert 'First answer' not in json.dumps(bits.client.calls[1]['messages'])
    third = await submit(ClientChat(message='Edited question', session_id=first['session_id'], keep_messages=0))
    messages = await adapter.messages(1, first['session_id'])
    assert [m['content'] for m in messages] == ['Edited question', 'Edited answer']
    assert 'Original question' not in json.dumps(bits.client.calls[2]['messages'])
    async with memory.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 5
        assert await db.scalar(select(func.count()).select_from(MemoryChange)) == 2
    exported = await adapter.export(1, first['session_id'])
    assert len(exported['messages']) == 5 and len(exported['hidden_messages']) == 3
    with pytest.raises(CoordinationError):
        await adapter.export(2, first['session_id'])
    await bits.coordinator.shutdown()


async def test_deliverable_stream_and_reload_use_the_same_file_receipt(memory, tmp_path, monkeypatch):
    from app.config import settings
    from app.skills.save_file import SaveFileSkill
    from app.schemas.llm import LLMMessage, Block
    from app.core.files import safe_output_path
    monkeypatch.setattr(settings, 'temp_outputs_dir', str(tmp_path / 'outputs'))
    bits = await setup(memory, [LLMMessage([Block(type='tool_use', id='save', name='save_file',
        input={'filename':'hello.txt', 'content':'Hello world'})], 'tool_use'), reply('Your file is ready')])
    skill = SaveFileSkill()
    skill.deferred = False  # fixture advertises this schema before the scripted call
    await bits.engine._registry.register_skill(skill)
    adapter = ClientCompatibility(bits.coordinator)
    result = await adapter.start(1, 'speda', ClientChat(message='Create a text file'))
    task = bits.coordinator.live.get(result['run_id'])
    if task:
        await task
    frames = [json.loads(s[6:]) async for s in adapter.stream(1, result['request_id']) if s.startswith('data: ')]
    file = next(e['data'] for e in frames if e['type'] == 'file')
    assert safe_output_path(file['name']).read_text() == 'Hello world'
    saved = (await adapter.messages(1, result['session_id']))[-1]
    assert saved['files'] == [file]
    assert saved['tools'][0]['name'] == 'save_file' and 'Saved' in saved['tools'][0]['result']
    await bits.coordinator.shutdown()


def test_image_budget_uses_native_ceiling_without_discarding_quoted_text_cost():
    from app.services.llm_protocol import count_native_images, budget_payload
    image = {'type':'image','source':{'type':'base64','media_type':'image/png','data':'x'*200000}}
    quoted = json.dumps(image)
    body = {'messages':[{'content':[image, {'type':'text','text':quoted}]}],
            'tools':[{'input_schema':{'type':['object','null']}}]}
    assert count_native_images(body) == 1
    bounded = budget_payload(body)
    assert len(json.dumps(bounded)) > 200000  # quoted JSON remains billable text
    assert len(json.dumps(bounded)) < 250000  # actual image's base64 is not counted twice
    assert body['messages'][0]['content'][0]['source']['data'] == 'x'*200000
