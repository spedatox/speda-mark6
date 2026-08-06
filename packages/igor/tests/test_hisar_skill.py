"""The vault, as an agent sees it.

Hisar is the owner's own cloud filesystem and his agents share it. The skill is
a thin client over three of its endpoints, so what is worth testing is not the
HTTP — it is the behaviour at the edges, where an agent otherwise reports work
it did not do:

- a deposit that was refused must not read as filed
- an unreachable Hisar must not read as an empty vault
- a name Hisar changed (it never overwrites) must be reported as it landed
"""

import httpx
import pytest

from app.config import settings
from app.skills.hisar import HisarSkill


class _Ctx:
    request_id = "req-1"


def _skill(monkeypatch, handler):
    """A skill wired to a scripted Hisar."""
    monkeypatch.setattr(settings, "hisar_machine_token", "t0ken", raising=False)
    monkeypatch.setattr(settings, "hisar_base_url", "http://hisar:8600", raising=False)

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw): return handler("GET", url, kw)
        async def post(self, url, **kw): return handler("POST", url, kw)

    monkeypatch.setattr("app.skills.hisar.httpx.AsyncClient", _Client)
    return HisarSkill()


def _resp(status=200, json=None, content=b"", url="http://hisar:8600/x"):
    request = httpx.Request("GET", url)
    return httpx.Response(status, json=json, content=None if json else content,
                          request=request)


# ── listing ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_listing_names_what_is_there(monkeypatch):
    skill = _skill(monkeypatch, lambda *a: _resp(json={"path": "/", "entries": [
        {"name": "Documents", "is_dir": True},
        {"name": "notes.md", "is_dir": False, "size": 2048},
    ]}))
    out = await skill.execute({"action": "list", "path": "/"}, _Ctx())

    assert "Documents/" in out          # trailing slash marks a folder
    assert "notes.md" in out and "2.0KB" in out


@pytest.mark.asyncio
async def test_an_empty_folder_says_so(monkeypatch):
    skill = _skill(monkeypatch, lambda *a: _resp(json={"entries": []}))
    out = await skill.execute({"action": "list", "path": "/SPEDA"}, _Ctx())
    assert "empty" in out.lower()


@pytest.mark.asyncio
async def test_an_unreachable_vault_does_not_read_as_an_empty_one(monkeypatch):
    """The failure that would have an agent tell the owner his folder is empty
    when in fact nobody looked."""
    def _boom(*a, **k):
        raise httpx.ConnectError("no route to host")

    skill = _skill(monkeypatch, _boom)
    out = await skill.execute({"action": "list", "path": "/"}, _Ctx())

    assert "unreachable" in out.lower()
    assert "empty" not in out.lower()


# ── depositing ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_deposit_reports_where_it_actually_landed(monkeypatch):
    """Hisar never overwrites, so the name it used may differ from the one
    asked for. Reporting the requested name would send the owner to a file
    that is not there."""
    skill = _skill(monkeypatch, lambda *a: _resp(json={"path": "/SPEDA/brief-1.md"}))
    out = await skill.execute({
        "action": "deposit", "path": "/SPEDA",
        "filename": "brief.md", "content": "hello",
    }, _Ctx())

    assert "/SPEDA/brief-1.md" in out


@pytest.mark.asyncio
async def test_a_refused_deposit_does_not_read_as_filed(monkeypatch):
    """Machines may write only under /SPEDA and /Forge. A 403 reported as
    success is how an agent tells the owner his report is filed when it is
    not."""
    def _403(*a, **k):
        request = httpx.Request("POST", "http://hisar:8600/deposit")
        raise httpx.HTTPStatusError(
            "forbidden", request=request,
            response=httpx.Response(403, request=request))

    skill = _skill(monkeypatch, _403)
    out = await skill.execute({
        "action": "deposit", "path": "/Documents",
        "filename": "x.md", "content": "hi",
    }, _Ctx())

    assert "Refused" in out
    assert "/SPEDA" in out          # says where it COULD go
    assert "Filed at" not in out


@pytest.mark.asyncio
async def test_an_empty_deposit_is_refused_before_the_call(monkeypatch):
    calls = []
    skill = _skill(monkeypatch, lambda *a: calls.append(a) or _resp(json={}))

    out = await skill.execute({"action": "deposit", "filename": "x.md",
                               "content": ""}, _Ctx())
    assert "Refused" in out and not calls


@pytest.mark.asyncio
async def test_a_deposit_without_a_name_is_refused(monkeypatch):
    skill = _skill(monkeypatch, lambda *a: _resp(json={}))
    out = await skill.execute({"action": "deposit", "content": "hi"}, _Ctx())
    assert "Refused" in out and "filename" in out


# ── reading ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reading_returns_the_text(monkeypatch):
    skill = _skill(monkeypatch, lambda *a: _resp(content=b"# Notes\nhello"))
    out = await skill.execute({"action": "read", "path": "/Documents/n.md"}, _Ctx())
    assert "hello" in out


@pytest.mark.asyncio
async def test_a_binary_file_is_not_guessed_at(monkeypatch):
    """An agent that 'reads' a PDF as mojibake will summarise the mojibake."""
    skill = _skill(monkeypatch, lambda *a: _resp(content=b"\x89PNG\r\n\x1a\n\xff\xfe"))
    out = await skill.execute({"action": "read", "path": "/Media/x.png"}, _Ctx())

    assert "not a text file" in out


@pytest.mark.asyncio
async def test_a_missing_path_points_at_list(monkeypatch):
    def _404(*a, **k):
        request = httpx.Request("GET", "http://hisar:8600/files/download")
        raise httpx.HTTPStatusError(
            "nf", request=request, response=httpx.Response(404, request=request))

    skill = _skill(monkeypatch, _404)
    out = await skill.execute({"action": "read", "path": "/nope"}, _Ctx())
    assert "list" in out.lower()


# ── configuration ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_token_is_reported_not_retried(monkeypatch):
    """A deployment problem the model cannot fix by trying again."""
    monkeypatch.setattr(settings, "hisar_machine_token", "", raising=False)
    out = await HisarSkill().execute({"action": "list"}, _Ctx())

    assert "not configured" in out
    assert "Tell the owner" in out


def test_the_description_says_when_not_to_use_it():
    """Rule 11 — and the save_file/deposit distinction is the one an agent
    actually gets wrong."""
    d = HisarSkill().description
    assert "save_file" in d
    assert "/SPEDA" in d and "/Forge" in d
    assert "delete" in d.lower()


# ── the desktop's vault picker ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_root_listing_has_no_nameless_directory(monkeypatch):
    """The picker's first screen showed a blank row.

    It was recovering directories by parsing the skill's RENDERED listing and
    taking every line ending in "/" — but the header line of a root listing is
    "/", which also ends in one. Structure now comes from the data.
    """
    from app.routers.hisar import HISAR

    skill = _skill(monkeypatch, lambda *a: _resp(json={"entries": [
        {"name": "Desktop", "kind": "dir"},
        {"name": "SPEDA", "kind": "dir"},
        {"name": "Timeline 1.mov", "kind": "file", "size": 22681894},
    ]}))
    monkeypatch.setattr(HISAR, "_client_marker", None, raising=False)

    entries = await skill.entries("/")
    dirs = [e["name"] for e in entries if skill.is_dir(e) and e.get("name")]

    assert dirs == ["Desktop", "SPEDA"]
    assert "" not in dirs


@pytest.mark.asyncio
async def test_files_are_not_offered_as_directories(monkeypatch):
    """It is a directory picker; a .mov is not somewhere work can happen."""
    skill = _skill(monkeypatch, lambda *a: _resp(json={"entries": [
        {"name": "notes.md", "kind": "file", "size": 12},
    ]}))
    entries = await skill.entries("/Documents")
    assert [e for e in entries if skill.is_dir(e)] == []


@pytest.mark.parametrize("entry,expected", [
    ({"kind": "dir"}, True),
    ({"is_dir": True}, True),
    ({"type": "dir"}, True),
    ({"kind": "file"}, False),
    ({}, False),
])
def test_directoriness_is_asked_in_one_place(entry, expected):
    """Hisar has spelled this three ways across versions. Every caller that
    re-derives it is a place one spelling gets forgotten."""
    from app.skills.hisar import HisarSkill
    assert HisarSkill.is_dir(entry) is expected
