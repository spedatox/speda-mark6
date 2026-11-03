import json

from starlette.websockets import WebSocketDisconnect

from tests.test_chat_integration import components


async def test_real_startup_registers_memory_and_shuts_down_cleanly(memory, monkeypatch, caplog):
    from app import database
    from app.main import create_app
    caplog.set_level("INFO")
    monkeypatch.setattr(database, "engine", memory.engine)
    monkeypatch.setattr(database, "AsyncSessionLocal", memory.sessions)
    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.memory_runtime
        tools = app.state.registry.list_tools()
        assert tools[0]["name"] == "Task"
        assert "memory_lookup" in {t["name"] for t in tools}


class Socket:
    def __init__(self, authorized=True):
        self.headers = {"x-api-key":"test-only-key"} if authorized else {}
        self.incoming = [json.dumps({"message":"Nora cancelled her visit."})]
        self.sent, self.accepted, self.closed = [], False, None
    async def accept(self):
        self.accepted = True
    async def close(self, code):
        self.closed = code
    async def receive_text(self):
        if not self.incoming:
            raise WebSocketDisconnect()
        return self.incoming.pop(0)
    async def send_text(self, value):
        self.sent.append(json.loads(value))
    async def send_json(self, value):
        self.sent.append(value)


async def test_websocket_uses_authenticated_real_chat_path(memory):
    bits = await components(memory)
    socket = Socket()
    await bits.turns.websocket(socket)
    assert socket.accepted
    assert [item["type"] for item in socket.sent] == ["start","chunk","done"]
    assert len(bits.client.calls) == 1
    await bits.turns.shutdown()


async def test_websocket_rejects_missing_auth(memory):
    bits = await components(memory)
    socket = Socket(authorized=False)
    await bits.turns.websocket(socket)
    assert socket.closed == 1008 and not socket.accepted
    assert not bits.client.calls
