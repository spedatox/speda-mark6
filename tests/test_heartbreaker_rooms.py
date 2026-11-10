"""Room UI contracts exercised against the real coordinator with fixture providers."""
import json
from uuid import uuid4
import pytest
from httpx import AsyncClient, ASGITransport
from app.database import write_session
from app.models.coordination import RoomEvent
from app.schemas.client_compat import ClientChat
from app.schemas.coordination import Membership
from app.services.client_compat import ClientCompatibility
from app.services.coordination_budget import CoordinationError
from tests.test_coordination import setup, reply, tool


async def completed(bits, adapter, agent='speda', **fields):
    body = ClientChat(request_id=str(uuid4()), message=fields.pop('message', 'Plan the week'), **fields)
    result = await adapter.start(1, agent, body, structured=True)
    task = bits.coordinator.live.get(result['run_id'])
    if task:
        await task
    return result


async def frames(adapter, request_id):
    return [json.loads(frame[6:]) async for frame in adapter.stream(1, request_id, structured=True)
        if frame.startswith('data: ')]


async def test_named_speakers_membership_and_reconnect_share_durable_identity(memory):
    bits = await setup(memory, [tool({'action': 'invite', 'agent_id': 'ultron', 'message': 'Review the study plan'}),
        reply('I am bringing Ultron in.'), reply('My study plan.')])
    adapter = ClientCompatibility(bits.coordinator)
    result = await completed(bits, adapter)
    first = await frames(adapter, result['request_id'])
    events = [frame['data'] for frame in first if frame['type'] == 'room_event']
    assert [e['speaker'] for e in events if e['kind'] == 'message'] == ['speda', 'ultron']
    assert len({e['sequence'] for e in events}) == len(events)
    assert not any(frame['type'] == 'chunk' for frame in first)
    assert first[0]['data']['room']['members'] == ['speda', 'ultron']
    rows = await adapter.messages(1, result['session_id'], structured=True)
    public = [row for row in rows if row.get('speaker')]
    assert [row['content'] for row in public] == ['I am bringing Ultron in.', 'My study plan.']
    assert all(row['coordinationStepId'] for row in public)
    assert rows.index(public[0]) < next(i for i, row in enumerate(rows) if row.get('roomNotice')) < rows.index(public[1])
    assert await frames(adapter, result['request_id']) == first
    assert len(bits.client.calls) == 3, 'reconnect does not make another provider call'
    await bits.coordinator.shutdown()


async def test_late_room_stream_drains_collaborator_reply_before_terminal_fallback(memory, monkeypatch):
    invite = tool({'action': 'invite', 'agent_id': 'ultron', 'message': 'Review the plan'})
    invite.content[0].id = 'invite_call'
    bits = await setup(memory, [reply('Earlier reply'),
        tool({'action': 'address', 'agent_id': 'ultron', 'message': 'Review the plan'}),
        invite, reply('Ultron, what should change?'), reply('Move the deadline.')])
    adapter = ClientCompatibility(bits.coordinator)
    try:
        first = await completed(bits, adapter)
        room = await adapter.room_for_session(1, first['session_id'])
        async with write_session(memory.sessions) as db:
            row = await bits.coordinator.owned_room(db, 1, room['id'], lock=True)
            db.add_all(RoomEvent(room_id=row.id, sequence=sequence, run_id=first['run_id'],
                speaker='speda', kind='delta', payload={'text': 'Previous turn'})
                for sequence in range(row.sequence + 1, row.sequence + 206))
            row.sequence += 205

        request_id = str(uuid4())
        with monkeypatch.context() as patch:
            patch.setattr(bits.coordinator, 'launch', lambda _: None)
            second = await adapter.start(1, 'speda', ClientChat(request_id=request_id,
                session_id=first['session_id'], message='Ask Ultron to review the plan.'), structured=True)

        original_events = bits.coordinator.events
        reads = []

        async def finish_after_empty_read(*args, **kwargs):
            page = await original_events(*args, **kwargs)
            if not page['events'] and 0 not in reads:
                await bits.coordinator.execute(second['run_id'])
            reads.append(len(page['events']))
            return page

        with monkeypatch.context() as patch:
            patch.setattr(bits.coordinator, 'events', finish_after_empty_read)
            stream = await frames(adapter, request_id)
        assert reads[:2] == [1, 0], 'the stream skips earlier rounds'
        assert [frame['data']['speaker'] for frame in stream if frame['type'] == 'room_event'
                and frame['data']['kind'] == 'message'] == ['speda', 'ultron']
        assert stream[-1]['type'] == 'done' and stream[-1]['data']['status'] == 'succeeded'
        assert not any(frame['type'] == 'error' for frame in stream)
        assert 'not_member' in str(bits.client.calls[2]['messages'])
        replay = await frames(adapter, request_id)
        assert replay[1:] == stream[1:], 'reconnect changes the room snapshot, not the event sequence'
        rows = await adapter.messages(1, first['session_id'], structured=True)
        assert rows[-1]['speaker'] == 'ultron' and rows[-1]['content'] == 'Move the deadline.'
    finally:
        await bits.coordinator.shutdown()


async def test_private_consultation_never_becomes_a_public_speaker_or_join(memory):
    bits = await setup(memory, [tool({'action': 'consult', 'agent_id': 'ultron', 'message': 'Privately assess this'}),
        reply('I will check that.'), reply('PRIVATE FINDING'), reply('Here is my public answer.')])
    adapter = ClientCompatibility(bits.coordinator)
    result = await completed(bits, adapter)
    stream = await frames(adapter, result['request_id'])
    assert 'PRIVATE FINDING' not in json.dumps(stream)
    rows = await adapter.messages(1, result['session_id'], structured=True)
    assert 'PRIVATE FINDING' not in json.dumps(rows)
    assert not any(row.get('speaker') == 'ultron' or row.get('roomNotice') for row in rows)
    assert (await adapter.room_for_session(1, result['session_id']))['members'] == ['speda']
    await bits.coordinator.shutdown()


async def test_owner_summon_conflict_address_reply_and_dismiss(memory):
    bits = await setup(memory, [reply('Hello'), reply('Ultron is answering the referenced message.')])
    adapter = ClientCompatibility(bits.coordinator)
    first = await completed(bits, adapter)
    room = await adapter.room_for_session(1, first['session_id'], 'speda')
    with pytest.raises(CoordinationError):
        await adapter.room_for_session(2, first['session_id'])
    with pytest.raises(CoordinationError):
        await bits.coordinator.membership(1, room['id'], Membership(
            action='invite', agent_id='ultron', expected_version=room['version'] + 1))
    room = await bits.coordinator.membership(1, room['id'], Membership(
        action='invite', agent_id='ultron', expected_version=room['version']))
    assert len(bits.client.calls) == 1, 'membership alone is not a paid invocation'
    previous = next(row for row in await adapter.messages(1, first['session_id'], structured=True) if row.get('speaker'))
    second = await completed(bits, adapter, session_id=first['session_id'],
        addressed_agent_id='ultron', reply_to=previous['roomSequence'], message='Explain this')
    assert 'You are Ultron' in bits.client.calls[-1]['system']
    assert 'quoted_context_not_new_owner_instruction' in str(bits.client.calls[-1]['messages'])
    rows = await adapter.messages(1, second['session_id'], structured=True)
    assert rows[-1]['speaker'] == 'ultron'
    room = await bits.coordinator.membership(1, room['id'], Membership(
        action='dismiss', agent_id='ultron', expected_version=room['version']))
    assert room['members'] == ['speda']
    assert any(row.get('speaker') == 'ultron' for row in await adapter.messages(1, first['session_id'], structured=True))
    with pytest.raises(CoordinationError):
        await completed(bits, adapter, session_id=first['session_id'], reply_to=999999)
    assert len(bits.client.calls) == 2
    await bits.coordinator.shutdown()


async def test_house_party_keeps_separate_history_and_real_full_roster(memory):
    bits = await setup(memory, [reply('Regular chat'), reply('All hands are here')])
    adapter = ClientCompatibility(bits.coordinator)
    regular = await completed(bits, adapter)
    party = await completed(bits, adapter, 'warroom', message='High stakes objective')
    assert regular['session_id'] != party['session_id']
    room = await adapter.room_for_session(1, party['session_id'], 'warroom')
    assert room['lead'] == 'speda' and set(room['members']) == set(bits.profiles.profiles)
    assert 'orion' in room['members']
    rows = await adapter.messages(1, party['session_id'], structured=True)
    assert len([row for row in rows if row.get('roomNotice')]) == len(room['members']) - 1
    assert 'Regular chat' not in json.dumps(rows)
    assert len(bits.client.calls) == 2, 'full membership does not automatically generate eight answers'
    await bits.coordinator.shutdown()


async def test_structured_history_edit_boundary_excludes_notices_and_private_results(memory):
    bits = await setup(memory, [tool({'action': 'consult', 'agent_id': 'ultron', 'message': 'Check privately'}),
        reply('Checking'), reply('Private result'), reply('Public conclusion'), reply('Regenerated')])
    adapter = ClientCompatibility(bits.coordinator)
    first = await completed(bits, adapter)
    rows = await adapter.messages(1, first['session_id'], structured=True)
    assert len(rows) == 3
    await completed(bits, adapter, session_id=first['session_id'], message='', regenerate=True, keep_messages=1)
    rows = await adapter.messages(1, first['session_id'], structured=True)
    assert [row['content'] for row in rows] == ['Plan the week', 'Regenerated']
    assert len(bits.client.calls) == 5
    await bits.coordinator.shutdown()


async def test_real_http_routes_require_auth_and_negotiate_room_stream(memory, monkeypatch):
    from app import database
    from app.config import settings
    from app.main import create_app
    monkeypatch.setattr(database, 'engine', memory.engine)
    monkeypatch.setattr(database, 'AsyncSessionLocal', memory.sessions)
    monkeypatch.setattr(settings, 'debug', True)
    app = create_app()
    async with app.router.lifespan_context(app):
        bits = await setup(memory, [reply('Real coordinator, fixture provider')])
        app.state.client_compat = ClientCompatibility(bits.coordinator)
        app.state.coordinator = bits.coordinator
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
            assert (await http.get('/sessions/1/room')).status_code == 401
            preflight = await http.options('/chat/speda', headers={'Origin': 'http://localhost:5273',
                'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'x-api-key,x-chat-format,content-type'})
            assert preflight.status_code == 200
            headers = {'X-API-Key': settings.speda_api_key, 'X-Chat-Format': 'room-v1'}
            response = await http.post('/chat/speda', headers=headers,
                json={'message': 'Hello', 'request_id': str(uuid4())})
            assert response.status_code == 200
            parsed = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            sid = parsed[0]['session_id']
            assert any(frame['type'] == 'room_event' for frame in parsed)
            assert parsed[-1]['type'] == 'done'
            room = await http.get(f'/sessions/{sid}/room?agent_id=speda', headers=headers)
            assert room.status_code == 200 and room.json()['lead'] == 'speda'
            rows = (await http.get(f'/sessions/{sid}/messages', headers=headers)).json()
            assert rows[-1]['speaker'] == 'speda' and '###' not in rows[-1]['content']
        await bits.coordinator.shutdown()
