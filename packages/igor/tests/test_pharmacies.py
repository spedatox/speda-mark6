# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the on-duty pharmacy skill.

Network is mocked — these assert request shaping, origin defaulting, record
mapping and the graceful-degradation paths (no key, no location, upstream
error), never a live NosyAPI call.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.services import places as place_store
from app.skills.pharmacies import OnDutyPharmacySkill, _duty_window, _pharmacy_record


def _ctx(client_location=None, db=None):
    """Minimal AgentContext stand-in — the skill reads .extra and .db (the place
    store it writes results to before handing back a placesId)."""
    return SimpleNamespace(
        extra={"client_location": client_location} if client_location else {},
        db=db,
    )


@pytest_asyncio.fixture
async def db():
    """A real in-memory DB so execute() exercises the place store rather than
    the 'could not store' fallback."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    """Records one GET and replays a queued response."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params, headers))
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "nosyapi_api_key", "test-nosy-key")
    monkeypatch.setattr(settings, "owner_home_lat", None)
    monkeypatch.setattr(settings, "owner_home_lng", None)


def _patch_client(monkeypatch, fake):
    monkeypatch.setattr("app.skills.pharmacies.httpx.AsyncClient", lambda *a, **k: fake)


_PHARMACY = {
    "pharmacyID": 15171,
    "pharmacyName": "Berrin Eczanesi",
    "address": "1456 Sokak No:98/A Alsancak Konak / İzmir",
    "city": "İzmir",
    "district": "Konak",
    "town": "Alsancak",
    "directions": "Alsancak meydanı karşısı",
    "phone": "0(232)463-40-86",
    "phone2": "",
    "pharmacyDutyStart": "2024-01-21 09:00:00",
    "pharmacyDutyEnd": "2024-01-22 09:00:00",
    "latitude": 38.435937,
    "longitude": 27.144759,
    "distanceKm": 0.391,
}


def _payload(pharmacies):
    return {
        "status": "success", "message": "ok", "messageTR": "ok",
        "endpoint": "pharmacies-on-duty/locations", "rowCount": len(pharmacies),
        "creditUsed": 1, "data": pharmacies,
    }


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_duty_window_trims_seconds():
    assert (_duty_window("2024-01-21 09:00:00", "2024-01-22 09:00:00")
            == "2024-01-21 09:00 → 2024-01-22 09:00")


def test_pharmacy_record_shape():
    rec = _pharmacy_record(_PHARMACY, {"lat": 38.43, "lng": 27.14})
    assert rec["name"] == "Berrin Eczanesi"
    assert rec["lat"] == 38.435937 and rec["lng"] == 27.144759
    assert rec["distanceKm"] == 0.4
    assert rec["openNow"] is True
    assert rec["hours"] == ["Nöbet: 2024-01-21 09:00 → 2024-01-22 09:00"]
    assert rec["pharmacyId"] == 15171
    assert rec["phone"] == "0(232)463-40-86"
    assert rec["mapsUri"].startswith("https://www.google.com/maps/search/")


# ── execute() ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_key_degrades(monkeypatch):
    monkeypatch.setattr(settings, "nosyapi_api_key", "")
    out = await OnDutyPharmacySkill().execute(
        {}, _ctx(client_location={"lat": 40.18, "lng": 28.74}))
    assert "NOSYAPI_API_KEY" in out


@pytest.mark.asyncio
async def test_live_location_shapes_request_and_stores(monkeypatch, db):
    fake = _FakeClient([_FakeResponse(200, _payload([_PHARMACY]))])
    _patch_client(monkeypatch, fake)

    out = await OnDutyPharmacySkill().execute(
        {}, _ctx(client_location={"lat": 40.18, "lng": 28.74}, db=db))

    method, url, params, headers = fake.calls[0]
    assert "pharmacies-on-duty/locations" in url
    assert params["latitude"] == 40.18
    assert params["longitude"] == 28.74
    assert headers["X-NSYP"] == "test-nosy-key"

    assert "Berrin Eczanesi" in out
    assert "0.4 km" in out and "nöbet" in out
    # The digest is what the model reasons over; the record is what the card
    # draws. Contact details and coordinates belong to the card.
    assert "0(232)463-40-86" not in out

    set_id = out.split('"places": "')[1].split('"')[0]
    stored = await place_store.fetch(db, set_id)
    assert stored["places"][0]["phone"] == "0(232)463-40-86"
    assert stored["places"][0]["distanceKm"] == 0.4


@pytest.mark.asyncio
async def test_home_fallback_when_no_live_location(monkeypatch):
    monkeypatch.setattr(settings, "owner_home_lat", 40.2)
    monkeypatch.setattr(settings, "owner_home_lng", 28.9)
    fake = _FakeClient([_FakeResponse(200, _payload([_PHARMACY]))])
    _patch_client(monkeypatch, fake)

    await OnDutyPharmacySkill().execute({}, _ctx())

    _, _, params, _ = fake.calls[0]
    assert params["latitude"] == 40.2
    assert params["longitude"] == 28.9


@pytest.mark.asyncio
async def test_missing_location(monkeypatch):
    out = await OnDutyPharmacySkill().execute({}, _ctx())
    assert "no location to search from" in out


@pytest.mark.asyncio
async def test_near_must_be_coordinates(monkeypatch):
    out = await OnDutyPharmacySkill().execute(
        {"near": "Bursa"}, _ctx(client_location={"lat": 40.18, "lng": 28.74}))
    assert "'near' must be coordinates" in out


@pytest.mark.asyncio
async def test_upstream_error_surfaces_message(monkeypatch):
    fake = _FakeClient([_FakeResponse(200, {"status": "error", "message": "invalid api key"})])
    _patch_client(monkeypatch, fake)
    out = await OnDutyPharmacySkill().execute(
        {}, _ctx(client_location={"lat": 40.18, "lng": 28.74}))
    assert "invalid api key" in out
    assert "rephrase" in out


@pytest.mark.asyncio
async def test_no_results(monkeypatch):
    fake = _FakeClient([_FakeResponse(200, _payload([]))])
    _patch_client(monkeypatch, fake)
    out = await OnDutyPharmacySkill().execute(
        {}, _ctx(client_location={"lat": 40.18, "lng": 28.74}))
    assert "no on-duty pharmacies" in out


@pytest.mark.asyncio
async def test_without_store_prints_coordinates(monkeypatch):
    """No DB session → no placesId, so the model is handed the coordinates it
    needs to write markers by hand. A map with pins beats no map."""
    fake = _FakeClient([_FakeResponse(200, _payload([_PHARMACY]))])
    _patch_client(monkeypatch, fake)

    out = await OnDutyPharmacySkill().execute(
        {}, _ctx(client_location={"lat": 40.18, "lng": 28.74}))
    assert "could NOT be stored" in out
    assert "38.435937" in out
