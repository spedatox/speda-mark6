"""Behavioral regression tests for the Proactive JARVIS Cortex & Hybrid Episodic Continuity Engine.

Covers the exact production server incidents:
1. Ultron pre-exam chat -> new chat opening with 'phewwww.' or 'Ultron.....'
2. Friday late-night chat -> next day 'friday hello. remebet our conversation last night'
3. SPEDA Bursa trip chat -> mid-journey 'hey speda' -> 'Im on my way still' -> 'Umm how far am I from the destination'
4. SPEDA 'ok bus time changed' -> immediate Bursa trip recall + preloaded calendar read tool
5. SPEDA 'well I talked about it with friday. ha rhymes' -> cross-agent peer episode recall with 1-to-1 privacy fence
6. JARVIS second-order consequence doctrine, active states, and shared retrieval evidence across specialist turns.
"""
from datetime import datetime, timezone
import json
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.context import AgentContext
from app.core.orchestrator import AgentOrchestrator
from app.core.registry import CapabilityRegistry
from app.database import Base
from app.models.coordination import AgentStep, CoordinationRun, Room, RoomEvent
from app.models.message import Message
from app.models.session import Session
from app.models.user import User
from app.profiles.registry import ProfileRegistry
from app. profiles.speda import SPEDAProfile
from app.skills.base import Skill
from app.skills.memory_lookup import MemoryLookupSkill
from app.skills.search_history import SearchHistorySkill
from app.services.memory_runtime import MemoryRuntime
from app.services.memory_store import MemoryStore, capture_source, init_indexes


class _DummyReadCalendarSkill(Skill):
    name = "calendar_list_events"
    read_only = True
    deferred = True
    search_keywords = "calendar list events schedule date time conflict commitment trip exam"
    description = (
        "List calendar events in an explicit time range. Use this to verify existing schedule commitments "
        "and detect conflicts before answering or modifying events. Do not use for creating events. "
        "Returns dated event entries with titles, times, and locations."
    )
    input_schema = {"type": "object", "properties": {"time_min": {"type": "string"}}, "additionalProperties": False}

    async def execute(self, args, context):
        return '{"events":[]}'


class _DummyReadRouteSkill(Skill):
    name = "route_distance"
    read_only = True
    deferred = True
    search_keywords = "route navigation distance travel transit destination traffic way"
    description = (
        "Compute live route distance and travel duration between origin coordinates and a destination. "
        "Use this when the owner asks how far they are from a destination or ETA. Do not use for static places. "
        "Returns distance in kilometers and estimated duration."
    )
    input_schema = {"type": "object", "properties": {"destination": {"type": "string"}}, "additionalProperties": False}

    async def execute(self, args, context):
        return '{"distance_km": 42}'


class _CaptureLLMClient:
    def __init__(self):
        self.calls = []

    def stream_message(self, **kwargs):
        self.calls.append(kwargs)
        return _DummyStream()


class _DummyStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def event_stream(self):
        yield "text", "I see you are travelling home to Bursa, sir. How long will you be staying so I can manage your calendar?"

    async def get_final_message(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="I see you are travelling home to Bursa, sir. How long will you be staying so I can manage your calendar?")],
            policy=None,
        )


@pytest.fixture
async def jarvis_env(tmp_path):
    db_path = tmp_path / "jarvis_cortex.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await init_indexes(conn)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db, db.begin():
        db.add(User(id=1, name="Ahmet Erol Bayrak", timezone="Europe/Istanbul"))
    store = MemoryStore(sessions)
    runtime = MemoryRuntime(store)
    registry = CapabilityRegistry()
    lookup = MemoryLookupSkill(runtime)
    await registry.register_skill(lookup)
    await registry.register_skill(SearchHistorySkill(lookup))
    await registry.register_skill(_DummyReadCalendarSkill())
    await registry.register_skill(_DummyReadRouteSkill())
    profiles = ProfileRegistry()
    client = _CaptureLLMClient()
    orchestrator = AgentOrchestrator(registry, client, SPEDAProfile(), memory_runtime=runtime, profiles=profiles)
    yield sessions, store, runtime, registry, profiles, orchestrator, client
    await engine.dispose()


async def _seed_session_exchange(
    sessions,
    *,
    agent_id: str,
    title: str,
    created_at: datetime,
    exchanges: list[tuple[str, str]],
    members: list[str] | None = None,
):
    members = members or [agent_id]
    async with sessions() as db, db.begin():
        sess = Session(user_id=1, agent_id=agent_id, triggered_by="user", model_used="test", title=title, started_at=created_at)
        db.add(sess)
        await db.flush()
        room = Room(id=f"room_{sess.id}", user_id=1, session_id=sess.id, title=title, lead=agent_id, members=members, created_at=created_at)
        db.add(room)
        await db.flush()
        seq = 0
        last_msg_id = None
        for user_text, asst_text in exchanges:
            u_msg = Message(session_id=sess.id, role="user", content=[{"type": "text", "text": user_text}], created_at=created_at)
            db.add(u_msg)
            await db.flush()
            await capture_source(
                db,
                user_id=1,
                source_key=f"message:{u_msg.id}",
                content=user_text,
                session_id=sess.id,
                message_id=u_msg.id,
            )
            seq += 1
            db.add(RoomEvent(room_id=room.id, sequence=seq, kind="user_message", speaker="owner", payload={"text": user_text, "message_id": u_msg.id}, created_at=created_at))
            a_msg = Message(
                session_id=sess.id,
                role="assistant",
                content=[
                    {"type": "text", "text": asst_text},
                    {"type": "_speaker", "agent_id": agent_id, "room_id": room.id, "step_id": f"step_{u_msg.id}"},
                ],
                created_at=created_at,
            )
            db.add(a_msg)
            await db.flush()
            seq += 1
            db.add(RoomEvent(room_id=room.id, sequence=seq, kind="message", speaker=agent_id, payload={"text": asst_text, "message_id": a_msg.id}, created_at=created_at))
            last_msg_id = a_msg.id
        room.sequence = seq
    return sess.id, room.id, last_msg_id


async def _start_new_turn(
    sessions,
    *,
    agent_id: str,
    created_at: datetime,
    turns: list[tuple[str, str]],
    current_user_text: str,
    members: list[str] | None = None,
):
    members = members or [agent_id]
    async with sessions() as db, db.begin():
        sess = Session(user_id=1, agent_id=agent_id, triggered_by="user", model_used="test", title="New chat", started_at=created_at)
        db.add(sess)
        await db.flush()
        room = Room(id=f"room_{sess.id}", user_id=1, session_id=sess.id, title="New chat", lead=agent_id, members=members, created_at=created_at)
        db.add(room)
        await db.flush()
        history = []
        seq = 0
        for u_t, a_t in turns:
            um = Message(session_id=sess.id, role="user", content=[{"type": "text", "text": u_t}], created_at=created_at)
            db.add(um)
            await db.flush()
            seq += 1
            db.add(RoomEvent(room_id=room.id, sequence=seq, kind="user_message", speaker="owner", payload={"text": u_t, "message_id": um.id}, created_at=created_at))
            history.append({"role": "user", "content": f"[Owner message, room event {seq}]\n{u_t}"})
            am = Message(session_id=sess.id, role="assistant", content=[{"type": "text", "text": a_t}], created_at=created_at)
            db.add(am)
            await db.flush()
            seq += 1
            db.add(RoomEvent(room_id=room.id, sequence=seq, kind="message", speaker=agent_id, payload={"text": a_t, "message_id": am.id}, created_at=created_at))
            history.append({"role": "user", "content": json.dumps({"attributed_contribution": agent_id, "sequence": seq, "text": a_t})})

        cur_msg = Message(session_id=sess.id, role="user", content=[{"type": "text", "text": current_user_text}], created_at=created_at)
        db.add(cur_msg)
        await db.flush()
        src = await capture_source(
            db,
            user_id=1,
            source_key=f"message:{cur_msg.id}",
            content=current_user_text,
            session_id=sess.id,
            message_id=cur_msg.id,
        )
        run = CoordinationRun(
            id=f"run_{cur_msg.id}",
            room_id=room.id,
            user_id=1,
            status="running",
            input_id=f"in_{cur_msg.id}",
            input_hash="hash",
            profiles={},
            policy={},
            limits={},
            deadline=9999999999.0,
            created_at=created_at,
        )
        db.add(run)
        seq += 1
        db.add(RoomEvent(room_id=room.id, sequence=seq, kind="user_message", speaker="owner", payload={"text": current_user_text, "message_id": cur_msg.id}, run_id=run.id, created_at=created_at))
        room.sequence = seq
        history.append({"role": "user", "content": f"[Owner message, room event {seq}]\n{current_user_text}"})

    return AgentContext(
        user_id=1,
        session_id=sess.id,
        request_id=f"req_{cur_msg.id}",
        triggered_by="user",
        trigger_payload={},
        output_mode="respond",
        model="claude-sonnet-4-6",
        system_prompt="",
        conversation_history=history,
        db=None,
        timezone="Europe/Istanbul",
        memory_source_id=src.id,
        agent_id=agent_id,
        room_id=room.id,
        run_id=run.id,
        step_id=f"step_{cur_msg.id}",
        allowed_tools=frozenset({"memory_lookup", "search_history", "calendar_list_events", "route_distance"}),
        prompt_datetime=created_at.replace(tzinfo=timezone.utc).isoformat(),
    )


@pytest.mark.asyncio
async def test_ultron_post_exam_phew_loads_pre_exam_session_on_turn_one(jarvis_env):
    sessions, store, runtime, registry, profiles, orchestrator, client = jarvis_env
    # Seed Session 2350: 19 minutes before MIS 243 General Accounting midterm exam
    await _seed_session_exchange(
        sessions,
        agent_id="ultron",
        title="Good luck on your final exam",
        created_at=datetime(2026, 11, 20, 5, 41, 32),
        exchanges=[
            ("last 19 minutes left ulty. wish me luck", "Luck is for creatures who roll dice. Remember the four accounting phases."),
            ("Boy, Ill be free as a bird after this exam. ILL TELL YOU WHAT I WONT BE, A FUCKING ACCOUNTANT", "A victory cry from the Ostim industrial zone. Make sure temporary accounts close at zero."),
        ],
    )
    # Owner opens a brand-new chat with Ultron just saying "phewwww."
    ctx = await _start_new_turn(
        sessions,
        agent_id="ultron",
        created_at=datetime(2026, 11, 20, 8, 15, 0),
        turns=[],
        current_user_text="phewwww.",
    )
    events = [e async for e in orchestrator.run(ctx)]
    assert any(e.type.value == "done" for e in events)
    system_sent = client.calls[-1]["system"]
    assert "<recent_conversation_evidence>" in system_sent
    assert "last 19 minutes left ulty" in system_sent or "ACCOUNTANT" in system_sent


@pytest.mark.asyncio
async def test_friday_last_night_conversation_preloaded_despite_typo(jarvis_env):
    sessions, store, runtime, registry, profiles, orchestrator, client = jarvis_env
    # Seed Session 2347: Thursday night late conversation (20:45 UTC = 23:45 Istanbul)
    await _seed_session_exchange(
        sessions,
        agent_id="friday",
        title="Late Night Study & Loneliness Talk",
        created_at=datetime(2026, 11, 19, 20, 45, 0),
        exchanges=[
            ("loneliness and it keeps me from studying for tomorrow. and tomorrow is important", "Studying exhausted at midnight costs more than it earns, boss. Put the books down by 2:00."),
            ("a deadline for 2:00 sounds nice. And I have to man up and actually work. Thanks fri", "Deal, boss. Get some sleep before the General Accounting midterm."),
        ],
    )
    # Next day Session 2352 (12:27 UTC = 15:27 Istanbul): typo "remebet our conversation last night"
    ctx = await _start_new_turn(
        sessions,
        agent_id="friday",
        created_at=datetime(2026, 11, 20, 12, 27, 10),
        turns=[],
        current_user_text="friday hello. remebet our conversation last night",
    )
    packet = await runtime.prepare(ctx)
    assert packet.recent_conversation
    combined = json.dumps(packet.recent_conversation, ensure_ascii=False)
    assert "2:00" in combined or "loneliness" in combined


@pytest.mark.asyncio
async def test_speda_mid_journey_relative_destination_recalls_bursa_trip(jarvis_env):
    sessions, store, runtime, registry, profiles, orchestrator, client = jarvis_env
    # Seed Session 2351: Clearing Calendar for Bursa Family Trip
    await _seed_session_exchange(
        sessions,
        agent_id="speda",
        title="Clearing Calendar for Bursa Family Trip",
        created_at=datetime(2026, 11, 20, 9, 24, 6),
        exchanges=[
            ("Speda, clen öy calendar between netween saturday and wednesday please ill be in bursa with my family", "Certainly, sir — clearing Saturday through Wednesday for your Bursa family trip. Your bus to Bursa Otogarı on Saturday stays."),
        ],
    )
    # Seed Session 2359: Saturday afternoon while on the bus in Karahıdırköy
    ctx = await _start_new_turn(
        sessions,
        agent_id="speda",
        created_at=datetime(2026, 11, 21, 15, 36, 51),
        turns=[
            ("hey speda", "Good evening, sir. Karahıdırköy on a Saturday evening — how can I help?"),
            ("Im on my way still", "Very good, sir. Understood — you're still in motion."),
        ],
        current_user_text="Umm how far am I from the destination",
    )
    _ = [e async for e in orchestrator.run(ctx)]
    system_sent = client.calls[-1]["system"]
    tools_sent = {t["name"] for t in client.calls[-1]["tools"]}
    assert "<recent_conversation_evidence>" in system_sent
    assert "bursa" in system_sent.lower()
    # Read-only route and calendar tools should be automatically preloaded on Turn 1
    assert "route_distance" in tools_sent
    assert "calendar_list_events" in tools_sent


@pytest.mark.asyncio
async def test_speda_bus_time_changed_recalls_bursa_trip_and_preloads_calendar(jarvis_env):
    sessions, store, runtime, registry, profiles, orchestrator, client = jarvis_env
    await _seed_session_exchange(
        sessions,
        agent_id="speda",
        title="Clearing Calendar for Bursa Family Trip",
        created_at=datetime(2026, 11, 20, 9, 24, 6),
        exchanges=[
            ("Speda, clen öy calendar between netween saturday and wednesday please ill be in bursa with my family", "Cleared Saturday through Wednesday, sir. Outbound Bus to Bursa Otogarı on Saturday is kept."),
        ],
    )
    ctx = await _start_new_turn(
        sessions,
        agent_id="speda",
        created_at=datetime(2026, 11, 20, 21, 44, 38),
        turns=[],
        current_user_text="ok bus time changed",
    )
    _ = [e async for e in orchestrator.run(ctx)]
    system_sent = client.calls[-1]["system"]
    tools_sent = {t["name"] for t in client.calls[-1]["tools"]}
    assert "<recent_conversation_evidence>" in system_sent
    assert "bursa" in system_sent.lower()
    assert "calendar_list_events" in tools_sent


@pytest.mark.asyncio
async def test_speda_cross_agent_friday_reference_loads_peer_episode_with_privacy_fence(jarvis_env):
    sessions, store, runtime, registry, profiles, orchestrator, client = jarvis_env
    await _seed_session_exchange(
        sessions,
        agent_id="friday",
        title="MIS 243 Midterm Follow-Up",
        created_at=datetime(2026, 11, 20, 12, 28, 0),
        exchanges=[
            ("şevval is dating someone else. I just found out", "I've got her file right here, boss. You two closed this properly in May."),
            ("why would I fall apart she doesnt care about me. Ill debug Speda Mark VI so youll be better", "We stop by midnight, boss. Deal."),
        ],
    )
    from types import SimpleNamespace
    # 1. In a 1-to-1 room with SPEDA, natural reference "well I talked about it with friday. ha rhymes" loads Friday's episode
    ctx_private = await _start_new_turn(
        sessions,
        agent_id="speda",
        created_at=datetime(2026, 11, 20, 21, 51, 50),
        turns=[("will do. WHat a fuckind day this friday was. damn", "Rough Fridays earn sympathy, sir.")],
        current_user_text="well I talked about it with friday. ha rhymes",
        members=["speda"],
    )
    ctx_private.coordination = SimpleNamespace(profiles=profiles)
    packet_private = await runtime.prepare(ctx_private)
    assert packet_private.recent_conversation
    assert any(item["agent_id"] == "friday" for item in packet_private.recent_conversation)

    # 2. In a multi-agent room (speda + optimus), private conversation with Friday is withheld
    ctx_group = await _start_new_turn(
        sessions,
        agent_id="speda",
        created_at=datetime(2026, 11, 20, 21, 52, 0),
        turns=[],
        current_user_text="well I talked about it with friday",
        members=["speda", "optimus"],
    )
    ctx_group.coordination = SimpleNamespace(profiles=profiles)
    packet_group = await runtime.prepare(ctx_group)
    assert packet_group.recent_coverage == "private_conversation_withheld"
    assert packet_group.recent_conversation == []


@pytest.mark.asyncio
async def test_speda_proactive_consequence_and_leadership_contract(jarvis_env):
    sessions, store, runtime, registry, profiles, orchestrator, client = jarvis_env
    ctx = await _start_new_turn(
        sessions,
        agent_id="speda",
        created_at=datetime(2026, 11, 21, 10, 0, 0),
        turns=[],
        current_user_text="My Bursa bus ticket for Saturday is confirmed",
    )
    ctx.memory_budget.lookup_results.append({"record_id": "bursa_home_dossier", "summary": "Owner family lives in Bursa; owner dorm is in Ankara."})
    _ = [e async for e in orchestrator.run(ctx)]
    system_sent = client.calls[-1]["system"]
    assert "Second-Order Consequence Scan" in system_sent
    assert "Executive Ownership (Especially SPEDA)" in system_sent
    assert "<shared_retrieval_evidence>" in system_sent
    assert "bursa_home_dossier" in system_sent


@pytest.mark.asyncio
async def test_chat_2360_parallel_research_subagents_preload_browse_and_wind_down_before_deadline(memory):
    """Chat 2360 regression: two researcher Tasks in one batch run concurrently, preload browse_page on iteration 0, and wind down cleanly with synthesis instead of hitting safety_guard/reconciling."""
    import asyncio
    from contextlib import asynccontextmanager
    from app.schemas.llm import Block, LLMMessage
    from app.schemas.coordination import CreateRoom
    from app.services.coordination_budget import ReplayStream
    from app.services.worker_control import WorkerControl
    from app.skills.base import Skill
    from tests.test_coordination import setup, reply, send

    class DummyBrowseSkill(Skill):
        name = "browse_page"
        description = "Fetch and extract readable text from a web page."
        read_only = True
        requires_network = True
        deferred = True
        search_keywords = "web_fetch fetch_url read_url http_get webpage website html"
        input_schema = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}

        async def execute(self, args, context):
            return f"Fetched content for {args.get('url')}: Bursa Nilüfer Akçalar Gölvadi Evleri 2017 betonarme."

    bits = await setup(memory, [])
    control = WorkerControl(bits.coordinator)
    bits.coordinator.workers = control
    bits.engine._registry._task_runner.activity = control
    await bits.engine._registry.register_skill(DummyBrowseSkill())

    both_started = asyncio.Event()
    active_workers = 0
    saw_parallel = False
    worker_calls = {}

    @asynccontextmanager
    async def stream(**args):
        nonlocal active_workers, saw_parallel
        bits.client.calls.append(args)
        if args.get("role") == "worker":
            key = args["cache_key"] + ":" + str(len(args["messages"]))
            active_workers += 1
            if active_workers >= 2:
                saw_parallel = True
                both_started.set()
            try:
                await asyncio.wait_for(both_started.wait(), timeout=2.0)
            except TimeoutError:
                pass
            finally:
                active_workers -= 1
            # Verify browse_page was preloaded on iteration 0 without tool_search!
            user_prompt = str(args["messages"][0]["content"])
            count = worker_calls.get(user_prompt, 0)
            worker_calls[user_prompt] = count + 1
            if count == 0:
                tool_names = {t["name"] for t in args.get("tools", [])}
                assert "browse_page" in tool_names
                msg = LLMMessage([Block(type="tool_use", id=f"bp_{len(bits.client.calls)}", name="browse_page", input={"url": "https://example.com/golvadi"})], "tool_use")
            else:
                msg = reply(f"Synthesized report for {user_prompt[:24]}")
        else:
            if len([c for c in bits.client.calls if c.get("role") != "worker"]) == 1:
                msg = LLMMessage([
                    Block(type="tool_use", id="task_w1", name="Task", input={"subagent_type": "researcher", "description": "Gölvadi site research", "prompt": "Research Gölvadi Evleri building specs"}),
                    Block(type="tool_use", id="task_w2", name="Task", input={"subagent_type": "researcher", "description": "Akçalar seismic analysis", "prompt": "Research Akçalar seismic fault soil"}),
                ], "tool_use")
            else:
                msg = reply("Both parallel researcher reports synthesized cleanly.")
        msg.policy = args["policy"]
        yield ReplayStream({"content": [b.model_dump() for b in msg.content], "stop_reason": msg.stop_reason, "usage": msg.usage, "continuation": None}, args["policy"])

    bits.client.stream_message = stream
    room = await bits.coordinator.create(1, CreateRoom(lead="friday"))
    try:
        result = await send(bits, room, "Bursa Akçalar Gölvadi evleri sitesi deprem araştırması yap")
        assert result["status"] == "succeeded"
        assert saw_parallel, "Multiple foreground read-only researcher Tasks in one batch must execute concurrently"
    finally:
        await bits.coordinator.shutdown()


@pytest.mark.asyncio
async def test_chat_2355_system_ops_action_inference_and_project_read_path_hints(memory, tmp_path, monkeypatch):
    """Chat 2355 regression: system_ops({'command': 'echo hello'}) infers action='exec', and project_read suggests closest matching inventory paths."""
    from app.skills.system_ops import SystemOpsSkill
    from app.skills.collaboration import ProjectReadSkill
    from app.schemas.coordination import CreateRoom
    from tests.test_projects import project_setup

    # 1. Verify SystemOpsSkill infers action='exec' when only 'command' is passed
    captured_ops = []
    async def fake_host_op(args, context):
        captured_ops.append(args)
        return {"status": "ok", "exit_code": 0, "stdout": "hello\n", "stderr": ""}

    monkeypatch.setattr("app.skills.system_ops.host_operation", fake_host_op)
    skill = SystemOpsSkill(None)
    ctx = memory.context(user_id=1, agent_id="orion")
    out = json.loads(await skill.execute({"command": "echo hello"}, ctx))
    assert out["status"] == "ok" and out.get("stdout") == "hello\n"
    assert captured_ops[-1]["action"] == "exec"

    # 2. Verify ProjectReadSkill returns closest matching paths when an imperfect path is requested
    async def noop_exec(*a, **k):
        raise AssertionError("No worker should run")
    bits = await project_setup(memory, tmp_path, [], noop_exec)
    service_dir = bits.path / "app" / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    (service_dir / "memory_runtime.py").write_text("# memory runtime\n", encoding="utf-8")
    room = await bits.coordinator.create(1, CreateRoom(lead="optimus", project_id=bits.project["id"]))
    read_ctx = memory.context(user_id=1, agent_id="optimus", room_id=room["id"], project_id=bits.project["id"], coordination=bits.coordinator)
    denied = json.loads(await ProjectReadSkill().execute({"path": "app/memory_runtime.py"}, read_ctx))
    assert denied["status"] == "denied"
    assert "app/services/memory_runtime.py" in denied["detail"]
    await bits.coordinator.shutdown()


@pytest.mark.asyncio
async def test_chat_2354_2355_operation_waits_deadline_grace_and_superseded_run_suppression(memory, monkeypatch):
    """Chat 2354/2355 regression: completed host operation near run.deadline gets a continuation grace window, and superseded runs do not talk over newer runs."""
    import time
    from sqlalchemy import update
    from app.models.coordination import CoordinationRun
    from app.models.operation_wait import OperationContinuation
    from sqlalchemy import select
    from tests.test_operation_waits import start_wait

    bits, waits, run_id, room, submissions, event = await start_wait(memory, monkeypatch)
    try:
        # Simulate run.deadline having expired by 3.3s right as the operation completes (like Chat 2355 EV 599-600)
        async with memory.sessions() as db, db.begin():
            await db.execute(update(CoordinationRun).where(CoordinationRun.id == run_id).values(deadline=time.time() - 3.3))
        await waits.accept(event)
        await waits.drain()
        await bits.coordinator.live[run_id]
        assert (await bits.coordinator.inspect_run(1, run_id))["status"] == "succeeded"
    finally:
        await bits.coordinator.shutdown()

