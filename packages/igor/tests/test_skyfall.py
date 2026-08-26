"""The Skyfall Protocol: the screen is the protocol.

Everything here defends one property — **nothing fires without a countdown the
owner could have aborted.** Strip that away and what is left is an agent that
will POST to any URL in the owner's config on being asked nicely, which is the
opposite of what this is for.

The three ways that property could be lost, each pinned below:

  * the agent reaching `fire` directly — it cannot; the tool only ARMS, and
    arming is a message to a client saying "draw a clock";
  * arming on a surface with no clock to draw (Telegram, an unnamed client) —
    refused, because a countdown nobody can see is not a countdown;
  * arming the WRONG project on a guess — refused, because the abort window is
    the only thing between a model's guess and a real request.

Plus the boundary that makes the countdown mean anything at all: the owner is
the sole author of a project. No tool writes one, and header values never leave
the service.
"""

import pytest

from app.services import skyfall
from app.skills.skyfall import SkyfallProtocolSkill

PROJECT = {
    "name": "Deploy Forge",
    "description": "Rebuilds the peer and restarts it",
    "url": "http://n8n:5678/webhook/deploy-forge",
    "method": "POST",
    "body": '{"target": "forge"}',
    "headers": {"Authorization": "Bearer sk-super-secret-value"},
    "countdown_seconds": 15,
}


@pytest.fixture
def store(monkeypatch):
    """Projects in memory, so nothing touches the real runtime_state file."""
    data: dict[str, dict] = {}
    monkeypatch.setattr(skyfall, "get_skyfall_projects", lambda: dict(data))
    monkeypatch.setattr(skyfall, "get_skyfall_project", lambda pid: dict(data.get(pid, {})))
    monkeypatch.setattr(skyfall, "save_skyfall_project",
                        lambda pid, rec: data.__setitem__(pid, dict(rec)) or dict(rec))
    monkeypatch.setattr(skyfall, "delete_skyfall_project",
                        lambda pid: data.pop(pid, None) is not None)
    return data


class _Ctx:
    request_id = "test-request"
    agent_id = "speda"
    user_id = 1
    triggered_by = "user"
    trigger_payload: dict = {}
    extra: dict = {}


def _ctx(triggered_by="user", platform="desktop"):
    c = _Ctx()
    c.triggered_by = triggered_by
    c.extra = {"client_platform": platform} if platform else {}
    c.trigger_payload = {}
    return c


def _add(store, **overrides):
    saved, err = skyfall.save({**PROJECT, **overrides})
    assert not err, err
    return saved


# ── The owner writes a project; nothing else does ────────────────────────────

def test_no_tool_can_create_edit_or_delete_a_project():
    """The countdown only means something because the owner wrote the target."""
    schema = SkyfallProtocolSkill().input_schema["properties"]
    assert set(schema) == {"project"}, (
        "a tool that could write the target AND pull the trigger could hit anything"
    )


async def test_the_agent_path_cannot_reach_fire(store, monkeypatch):
    _add(store)
    fired = []
    monkeypatch.setattr(skyfall, "fire", lambda pid: fired.append(pid))

    await SkyfallProtocolSkill().execute({"project": "Deploy Forge"}, _ctx())
    assert fired == [], "arming must never fire"


# ── Secrets stay behind the service ──────────────────────────────────────────

def test_header_values_never_leave_the_service(store):
    saved = _add(store)
    assert saved["headers"] == {"Authorization": skyfall.MASK}
    assert "sk-super-secret-value" not in str(saved)
    assert "sk-super-secret-value" not in str(skyfall.listing())


def test_the_countdown_payload_carries_no_secret_and_no_body(store):
    saved = _add(store)
    payload = skyfall.arming_payload(store[saved["id"]])

    assert "headers" not in payload and "body" not in payload
    assert "sk-super-secret-value" not in str(payload)
    assert payload["countdown_seconds"] == 15


async def test_the_armed_tool_output_carries_no_secret(store):
    _add(store)
    result = await SkyfallProtocolSkill().execute({"project": "Deploy Forge"}, _ctx())
    assert "sk-super-secret-value" not in result
    assert "n8n:5678" not in result, "the model does not need the target URL either"


def test_resaving_a_masked_form_keeps_the_secret(store):
    """The pane renders masked values; saving it back must not blank them."""
    saved = _add(store)
    again, err = skyfall.save({**saved, "description": "edited",
                               "headers": {"Authorization": skyfall.MASK}})

    assert not err
    assert store[saved["id"]]["headers"]["Authorization"] == "Bearer sk-super-secret-value"
    assert again["description"] == "edited"


def test_a_retyped_header_does_replace_the_stored_one(store):
    saved = _add(store)
    skyfall.save({**saved, "headers": {"Authorization": "Bearer brand-new"}})
    assert store[saved["id"]]["headers"]["Authorization"] == "Bearer brand-new"


# ── Validation happens at SAVE, not at zero ──────────────────────────────────

def test_a_broken_body_is_refused_when_saved_not_when_fired(store):
    _, err = skyfall.save({**PROJECT, "body": "{not json"})
    assert "not valid JSON" in err
    assert store == {}, "an unusable project must not be storable"


def test_a_countdown_too_short_to_abort_is_refused(store):
    _, err = skyfall.save({**PROJECT, "countdown_seconds": 1})
    assert "abort" in err
    assert store == {}


@pytest.mark.parametrize("bad,reason", [
    ({"name": ""}, "name"),
    ({"url": ""}, "URL"),
    ({"url": "ftp://somewhere/x"}, "http"),
    ({"method": "TRACE"}, "method"),
])
def test_unusable_projects_are_refused(store, bad, reason):
    _, err = skyfall.save({**PROJECT, **bad})
    assert reason in err
    assert store == {}


def test_an_internal_url_is_allowed_because_it_is_the_point(store):
    """`http://n8n:5678/...` is the likely target, not an attack."""
    saved = _add(store, url="http://app:8000/trigger/orion")
    assert saved["url"] == "http://app:8000/trigger/orion"


# ── Resolving what the owner said ────────────────────────────────────────────

def test_an_exact_name_resolves(store):
    _add(store)
    project, _ = skyfall.find("deploy forge")
    assert project and project["name"] == "Deploy Forge"


def test_a_unique_substring_resolves(store):
    _add(store)
    project, _ = skyfall.find("forge")
    assert project is not None


def test_an_exact_name_wins_even_when_it_prefixes_another(store):
    """Otherwise a project whose name starts another one's could never be armed."""
    _add(store, name="Deploy Forge")
    _add(store, name="Deploy Forge Staging")

    project, _ = skyfall.find("deploy forge")
    assert project is not None and project["name"] == "Deploy Forge"


def test_a_substring_matching_two_projects_resolves_to_nothing(store):
    _add(store, name="Deploy Forge")
    _add(store, name="Redeploy Forge Staging")

    project, candidates = skyfall.find("forge")
    assert project is None, "a guess here arms a countdown for the wrong endpoint"
    assert len(candidates) == 2


async def test_an_ambiguous_name_lists_the_candidates_instead_of_guessing(store):
    _add(store, name="Deploy Forge")
    _add(store, name="Redeploy Forge Staging")
    ctx = _ctx()

    result = await SkyfallProtocolSkill().execute({"project": "forge"}, ctx)
    assert "REFUSED" in result
    assert "Redeploy Forge Staging" in result
    assert "skyfall_arm" not in ctx.extra


async def test_no_project_named_asks_rather_than_choosing(store):
    _add(store)
    ctx = _ctx()
    result = await SkyfallProtocolSkill().execute({}, ctx)

    assert "Deploy Forge" in result
    assert "skyfall_arm" not in ctx.extra


async def test_no_projects_configured_says_so(store):
    result = await SkyfallProtocolSkill().execute({"project": "anything"}, _ctx())
    assert "No Skyfall projects are configured" in result


# ── The gates ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("trigger", ["n8n", "agent"])
async def test_nothing_automated_can_arm(store, trigger):
    _add(store)
    ctx = _ctx(triggered_by=trigger)
    result = await SkyfallProtocolSkill().execute({"project": "Deploy Forge"}, ctx)

    assert "REFUSED" in result
    assert "skyfall_arm" not in ctx.extra


@pytest.mark.parametrize("platform", ["telegram", None, "smartwatch"])
async def test_a_surface_with_no_screen_cannot_arm(store, platform):
    """A countdown nobody can see is not a countdown."""
    _add(store)
    ctx = _ctx(platform=platform)
    result = await SkyfallProtocolSkill().execute({"project": "Deploy Forge"}, ctx)

    assert "skyfall_arm" not in ctx.extra
    assert "cannot be armed from this channel" in result


@pytest.mark.parametrize("platform", ["desktop", "web", "android", "ios"])
async def test_every_surface_that_draws_the_clock_can_arm(store, platform):
    _add(store)
    ctx = _ctx(platform=platform)
    await SkyfallProtocolSkill().execute({"project": "Deploy Forge"}, ctx)
    assert ctx.extra["skyfall_arm"]["name"] == "Deploy Forge"


# ── What the agent is told to say ────────────────────────────────────────────

async def test_arming_tells_the_agent_nothing_was_sent(store):
    _add(store)
    result = await SkyfallProtocolSkill().execute({"project": "Deploy Forge"}, _ctx())

    assert "NOTHING HAS BEEN SENT" in result
    assert "abort" in result
    # The failure this guards: a model reporting a launch that has not happened.
    assert "do not say the project has been triggered" in result.lower()


async def test_arming_stamps_the_countdown_payload_for_the_client(store):
    saved = _add(store)
    ctx = _ctx()
    await SkyfallProtocolSkill().execute({"project": "Deploy Forge"}, ctx)

    arm = ctx.extra["skyfall_arm"]
    assert arm["project_id"] == saved["id"]
    assert arm["countdown_seconds"] == 15
    assert arm["method"] == "POST"


# ── Firing, when the clock actually ran out ──────────────────────────────────

async def test_firing_sends_the_configured_request(store, monkeypatch):
    saved = _add(store)
    sent = {}

    class _Resp:
        status_code = 200
        is_success = True
        text = "queued"

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, content=None, headers=None):
            sent.update(method=method, url=url, content=content, headers=headers)
            return _Resp()

    monkeypatch.setattr(skyfall.httpx, "AsyncClient", _Client)
    ok, result = await skyfall.fire(saved["id"])

    assert ok and result["fired"] and result["status"] == 200
    assert sent["method"] == "POST"
    assert sent["headers"]["Authorization"] == "Bearer sk-super-secret-value"
    assert sent["headers"]["Content-Type"] == "application/json"
    assert sent["content"] == b'{"target": "forge"}'


async def test_a_deleted_project_fires_nothing(store):
    ok, result = await skyfall.fire("gone")
    assert not ok and result["fired"] is False


async def test_a_transport_failure_is_reported_as_fired_but_not_ok(store, monkeypatch):
    """It left. Whether it arrived is a different question, and the screen must
    not render 'never sent' over 'sent and the network died'."""
    saved = _add(store)

    class _Boom:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **kw): raise ConnectionError("refused")

    monkeypatch.setattr(skyfall.httpx, "AsyncClient", _Boom)
    ok, result = await skyfall.fire(saved["id"])

    assert not ok
    assert result["fired"] is True
    assert "refused" in result["error"]


async def test_a_500_is_fired_and_not_ok(store, monkeypatch):
    saved = _add(store)

    class _Resp:
        status_code = 500
        is_success = False
        text = "boom"

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **kw): return _Resp()

    monkeypatch.setattr(skyfall.httpx, "AsyncClient", _Client)
    ok, result = await skyfall.fire(saved["id"])

    assert not ok
    assert result["fired"] is True and result["status"] == 500


async def test_firing_records_the_outcome_on_the_project(store, monkeypatch):
    saved = _add(store)

    class _Resp:
        status_code = 202
        is_success = True
        text = "ok"

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **kw): return _Resp()

    monkeypatch.setattr(skyfall.httpx, "AsyncClient", _Client)
    await skyfall.fire(saved["id"])

    assert "202" in store[saved["id"]]["last_result"]
    assert store[saved["id"]]["last_fired_at"]
    # The secret survived the write-back.
    assert store[saved["id"]]["headers"]["Authorization"] == "Bearer sk-super-secret-value"


def test_an_abort_is_recorded_and_sends_nothing(store):
    saved = _add(store)
    result = skyfall.abort(saved["id"], remaining=4.2)

    assert result["aborted"] and result["fired"] is False
    assert not store[saved["id"]]["last_fired_at"]
