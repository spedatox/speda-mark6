"""Unit tests for the navigation skills (get_route / find_places).

Network is mocked — these assert the request shaping, origin defaulting, and the
graceful-degradation paths (no key, no location, upstream error), never a live
Google call.
"""

import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.services import places as place_store
from app.skills.navigation import (
    FindPlacesSkill,
    GetRouteSkill,
    _congestion_summary,
    _dur_min,
    _maps_dir_link,
    _place_record,
    _steps,
    _traffic_bands,
)


def _ctx(client_location=None, db=None):
    """Minimal AgentContext stand-in — the skills read .extra and .db (the route
    store, which get_route writes the geometry to before handing back an id)."""
    return SimpleNamespace(
        extra={"client_location": client_location} if client_location else {},
        db=db,
    )


@pytest_asyncio.fixture
async def db():
    """A real in-memory DB so get_route exercises the route store rather than
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
    """Records requests and replays a queued response per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        return self._responses.pop(0)

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params, headers))
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
    monkeypatch.setattr(settings, "owner_home_lat", None)
    monkeypatch.setattr(settings, "owner_home_lng", None)


def _patch_client(monkeypatch, fake):
    monkeypatch.setattr("app.skills.navigation.httpx.AsyncClient", lambda *a, **k: fake)


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_dur_min_parses_seconds():
    assert _dur_min("1234s") == 21
    assert _dur_min("59s") == 1
    assert _dur_min(None) is None
    assert _dur_min("banana") is None


def test_maps_dir_link_shape():
    link = _maps_dir_link({"lat": 41.0, "lng": 29.0}, {"lat": 41.1, "lng": 29.1}, "drive")
    assert "origin=41.0,29.0" in link
    assert "destination=41.1,29.1" in link
    assert "travelmode=driving" in link


def test_traffic_bands_default_missing_start_to_zero():
    # The Routes API omits startPolylinePointIndex when it is 0. Defaulting it
    # wrong shifts every colour band along the line.
    route = {"travelAdvisory": {"speedReadingIntervals": [
        {"endPolylinePointIndex": 12, "speed": "NORMAL"},
        {"startPolylinePointIndex": 12, "endPolylinePointIndex": 20, "speed": "TRAFFIC_JAM"},
        {"startPolylinePointIndex": 20, "speed": "SLOW"},          # no end → unusable
        {"startPolylinePointIndex": 21, "endPolylinePointIndex": 30, "speed": "WAT"},
    ]}}
    bands = _traffic_bands(route)
    assert bands == [
        {"start": 0, "end": 12, "speed": "NORMAL"},
        {"start": 12, "end": 20, "speed": "TRAFFIC_JAM"},
    ]


def test_traffic_bands_absent_is_empty_not_clear():
    # No data and free-flowing are different answers; the clients rely on the
    # distinction to decide between a plain line and a green one.
    assert _traffic_bands({}) == []


def test_congestion_summary_weights_by_span():
    bands = [
        {"start": 0, "end": 60, "speed": "NORMAL"},
        {"start": 60, "end": 90, "speed": "SLOW"},
        {"start": 90, "end": 100, "speed": "TRAFFIC_JAM"},
    ]
    assert _congestion_summary(bands) == "60% clear · 30% slow · 10% jammed"
    assert _congestion_summary([]) == ""


def test_steps_flatten_legs_and_drop_instructionless():
    route = {"legs": [
        {"steps": [
            {"navigationInstruction": {"instructions": "Head north", "maneuver": "DEPART"},
             "distanceMeters": 240, "staticDuration": "60s"},
            {"distanceMeters": 90},                       # no instruction → nothing to show
        ]},
        {"steps": [{"navigationInstruction": {"instructions": "Turn right"}}]},
    ]}
    steps = _steps(route)
    assert [s["instruction"] for s in steps] == ["Head north", "Turn right"]
    assert steps[0]["distanceM"] == 240 and steps[0]["durationMin"] == 1


def test_place_record_drops_empties_and_measures_distance():
    raw = {
        "id": "ChIJabc",
        "displayName": {"text": "Kuaför Emre"},
        "location": {"latitude": 40.2200, "longitude": 28.9900},
        "rating": 4.6, "userRatingCount": 231,
        "currentOpeningHours": {"openNow": True, "weekdayDescriptions": ["Monday: 09:00–20:00"]},
        "priceLevel": "PRICE_LEVEL_MODERATE",
        "businessStatus": "OPERATIONAL",
        "primaryTypeDisplayName": {"text": "Barber shop"},
    }
    rec = _place_record(raw, {"lat": 40.2100, "lng": 28.9900})
    assert rec["name"] == "Kuaför Emre" and rec["placeId"] == "ChIJabc"
    assert rec["priceLevel"] == "moderate"          # the shouted wire enum is unshouted here
    assert rec["category"] == "Barber shop"
    assert rec["openNow"] is True and rec["hours"] == ["Monday: 09:00–20:00"]
    assert 1.0 < rec["distanceKm"] < 1.3            # ~1.11 km per 0.01° of latitude
    # An operational business is the unremarkable case — no status field to show.
    assert "status" not in rec and "website" not in rec


def test_place_record_without_coordinates_is_dropped():
    # A place we cannot put on the map is not a map result.
    assert _place_record({"displayName": {"text": "Nowhere"}}, None) is None


# ── get_route ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_route_no_key(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "")
    out = await GetRouteSkill().execute({"destination": "Kadıköy"}, _ctx())
    assert "unavailable" in out.lower()


@pytest.mark.asyncio
async def test_get_route_defaults_origin_to_client_location(monkeypatch, db):
    # destination is a coordinate object (no geocode call); origin defaults to the
    # live client location → exactly one POST to computeRoutes.
    routes_payload = {
        "routes": [{
            "duration": "2040s", "staticDuration": "1320s", "distanceMeters": 18400,
            "polyline": {"encodedPolyline": "abcd"}, "description": "D-100",
            "routeLabels": ["DEFAULT_ROUTE"],
            "travelAdvisory": {"speedReadingIntervals": [
                {"endPolylinePointIndex": 8, "speed": "NORMAL"},
                {"startPolylinePointIndex": 8, "endPolylinePointIndex": 10, "speed": "TRAFFIC_JAM"},
            ]},
            "legs": [{"steps": [
                {"navigationInstruction": {"instructions": "Head north on D-100"},
                 "distanceMeters": 1200},
            ]}],
        }]
    }
    fake = _FakeClient([_FakeResponse(200, routes_payload)])
    _patch_client(monkeypatch, fake)

    ctx = _ctx(client_location={"lat": 41.00, "lng": 29.00, "place": "Kadıköy"}, db=db)
    out = await GetRouteSkill().execute(
        {"destination": json.dumps({"lat": 41.11, "lng": 29.02})}, ctx
    )

    assert len(fake.calls) == 1
    method, url, body, headers = fake.calls[0]
    assert method == "POST" and "computeRoutes" in url
    assert body["origin"]["location"]["latLng"]["latitude"] == 41.00
    assert body["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"
    # Per-segment congestion — what colours the drawn line — is refused by the
    # API without a traffic-aware preference, so the two always ship together.
    assert body["extraComputations"] == ["TRAFFIC_ON_POLYLINE"]
    assert "speedReadingIntervals" in headers["X-Goog-FieldMask"]
    assert "34 min" in out.lower()          # 2040s → 34 min
    assert "+12 min vs no-traffic" in out   # 34 − 22
    assert "80% clear · 20% jammed" in out  # the shape of it, not just the delay
    assert "1 turn-by-turn steps" in out
    # The polyline must NOT reach the model — that is the whole fix. It is
    # stored server-side and referenced by id, because a hand-copied polyline
    # that loses one character still parses and still looks like a road.
    assert "abcd" not in out, "the raw geometry must never be handed to the model"
    assert "routeId=r_" in out
    # …and neither do the turn instructions or the band indices: bulky, purely
    # mechanical text that only loses accuracy by passing through a model.
    assert "Head north on D-100" not in out


@pytest.mark.asyncio
async def test_get_route_no_origin_no_home(monkeypatch):
    out = await GetRouteSkill().execute(
        {"destination": json.dumps({"lat": 41.1, "lng": 29.0})}, _ctx()
    )
    assert "no origin" in out.lower()


@pytest.mark.asyncio
async def test_get_route_upstream_error(monkeypatch):
    fake = _FakeClient([_FakeResponse(403, {}, text="permission denied")])
    _patch_client(monkeypatch, fake)
    ctx = _ctx(client_location={"lat": 41.0, "lng": 29.0})
    out = await GetRouteSkill().execute(
        {"destination": json.dumps({"lat": 41.1, "lng": 29.0})}, ctx
    )
    assert "HTTP 403" in out


# ── origin/destination text resolution ────────────────────────────────────────
#
# Regression cover for the Bursa routing failure: a text origin the owner DID
# supply must be resolved via Places (not Geocoding, which returns nothing for
# station/mall names), and each way it can fail must report itself as itself.

@pytest.mark.asyncio
async def test_text_origin_resolves_via_places_first(monkeypatch, db):
    places_payload = {
        "places": [{
            "displayName": {"text": "Bursa Uludağ Üniversitesi Metro İstasyonu"},
            "location": {"latitude": 40.2265, "longitude": 28.8720},
            "formattedAddress": "Nilüfer/Bursa",
        }]
    }
    routes_payload = {"routes": [{
        "duration": "1500s", "staticDuration": "1200s", "distanceMeters": 12000,
        "polyline": {"encodedPolyline": "xyz"}, "description": "Bursa Ring",
    }]}
    fake = _FakeClient([_FakeResponse(200, places_payload), _FakeResponse(200, routes_payload)])
    _patch_client(monkeypatch, fake)

    out = await GetRouteSkill().execute({
        "origin": "Bursa Uludağ Üniversitesi Metro İstasyonu",
        "destination": json.dumps({"lat": 40.2100, "lng": 29.0100}),
    }, _ctx(db=db))

    # Places is consulted first, and Geocoding is never reached on a hit.
    assert "searchText" in fake.calls[0][1]
    assert len(fake.calls) == 2
    body = fake.calls[1][2]
    assert body["origin"]["location"]["latLng"]["latitude"] == 40.2265
    # The geometry is stored server-side now; the model gets an id.
    assert "routeId=r_" in out


@pytest.mark.asyncio
async def test_text_origin_falls_back_to_geocoding(monkeypatch, db):
    geo_payload = {"status": "OK", "results": [{
        "geometry": {"location": {"lat": 40.19, "lng": 29.06}},
        "formatted_address": "Bursa, Türkiye",
    }]}
    routes_payload = {"routes": [{"duration": "600s", "distanceMeters": 5000,
                                  "polyline": {"encodedPolyline": "q"}}]}
    fake = _FakeClient([
        _FakeResponse(200, {"places": []}),      # Places misses
        _FakeResponse(200, geo_payload),          # Geocoding catches it
        _FakeResponse(200, routes_payload),
    ])
    _patch_client(monkeypatch, fake)

    out = await GetRouteSkill().execute({
        "origin": "Bursa", "destination": json.dumps({"lat": 40.21, "lng": 29.01}),
    }, _ctx(db=db))

    assert "searchText" in fake.calls[0][1]
    assert "geocode" in fake.calls[1][1]
    # The geometry is stored server-side now; the model gets an id.
    assert "routeId=r_" in out


@pytest.mark.asyncio
async def test_unresolvable_text_origin_does_not_blame_missing_home(monkeypatch):
    fake = _FakeClient([
        _FakeResponse(200, {"places": []}),
        _FakeResponse(200, {"status": "ZERO_RESULTS", "results": []}),
    ])
    _patch_client(monkeypatch, fake)

    out = await GetRouteSkill().execute({
        "origin": "asdkjhasd nowhere",
        "destination": json.dumps({"lat": 41.1, "lng": 29.0}),
    }, _ctx())

    assert "couldn't resolve the origin" in out
    # The bug: an origin the owner DID give reporting itself as no origin at all.
    assert "no origin" not in out.lower()
    assert "home" not in out.lower()


@pytest.mark.asyncio
async def test_request_denied_reports_as_api_fault_not_bad_place(monkeypatch):
    """The actual Bursa root cause: billing off → REQUEST_DENIED on every call."""
    denied = {"status": "REQUEST_DENIED", "results": [],
              "error_message": "You must enable Billing on the Google Cloud Project"}
    fake = _FakeClient([
        _FakeResponse(403, {}, text="PERMISSION_DENIED"),  # Places
        _FakeResponse(200, denied),                        # Geocoding
    ])
    _patch_client(monkeypatch, fake)

    out = await GetRouteSkill().execute({
        "origin": "Bursa Uludağ Üniversitesi Metro İstasyonu",
        "destination": json.dumps({"lat": 40.21, "lng": 29.01}),
    }, _ctx())

    assert "refused" in out.lower()
    assert "billing" in out.lower() or "403" in out
    # Must not send the owner chasing their own phrasing or a missing home.
    assert "no origin" not in out.lower()
    assert "rephrase" in out.lower()  # explicitly tells the model NOT to


# ── find_places ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_places_biases_to_client_location(monkeypatch, db):
    places_payload = {
        "places": [{
            "id": "ChIJkronotrop",
            "displayName": {"text": "Kronotrop"},
            "location": {"latitude": 41.05, "longitude": 29.01},
            "rating": 4.6, "userRatingCount": 320,
            "currentOpeningHours": {"openNow": True},
            "formattedAddress": "Cihangir, İstanbul",
            "nationalPhoneNumber": "0212 000 00 00",
        }]
    }
    fake = _FakeClient([_FakeResponse(200, places_payload)])
    _patch_client(monkeypatch, fake)

    ctx = _ctx(client_location={"lat": 41.03, "lng": 29.00}, db=db)
    out = await FindPlacesSkill().execute({"query": "specialty coffee"}, ctx)

    method, url, body, _ = fake.calls[0]
    assert "searchText" in url
    assert body["textQuery"] == "specialty coffee"
    assert body["locationBias"]["circle"]["center"]["latitude"] == 41.03
    assert "Kronotrop" in out and "4.6★" in out and "open now" in out

    # The digest is what the model reasons over; the record is what the card
    # draws. Contact details and coordinates belong to the card — retyping them
    # into the answer spends context on something already in hand, and loses a
    # digit doing it.
    assert "0212 000 00 00" not in out
    assert "41.05" not in out

    set_id = out.split('"places": "')[1].split('"')[0]
    stored = await place_store.fetch(db, set_id)
    assert stored["places"][0]["phone"] == "0212 000 00 00"
    assert stored["places"][0]["placeId"] == "ChIJkronotrop"
    assert stored["places"][0]["distanceKm"] == 2.4


@pytest.mark.asyncio
async def test_find_places_ranking_and_filters(monkeypatch, db):
    fake = _FakeClient([_FakeResponse(200, {"places": []})])
    _patch_client(monkeypatch, fake)

    ctx = _ctx(client_location={"lat": 41.03, "lng": 29.00}, db=db)
    await FindPlacesSkill().execute({
        "query": "eczane", "rank_by": "distance", "radius_m": 900,
        "min_rating": 9.0,            # out of range — the API rejects it outright
        "open_now": True,
    }, ctx)

    body = fake.calls[0][2]
    assert body["rankPreference"] == "DISTANCE"
    assert body["locationBias"]["circle"]["radius"] == 900.0
    assert body["minRating"] == 5.0
    assert body["openNow"] is True


@pytest.mark.asyncio
async def test_find_places_without_store_prints_coordinates(monkeypatch):
    """No DB session → no placesId, so the model must be handed the coordinates
    it needs to write markers by hand. A map with pins beats no map."""
    places_payload = {"places": [{
        "displayName": {"text": "Kronotrop"},
        "location": {"latitude": 41.05, "longitude": 29.01},
    }]}
    fake = _FakeClient([_FakeResponse(200, places_payload)])
    _patch_client(monkeypatch, fake)

    out = await FindPlacesSkill().execute(
        {"query": "coffee"}, _ctx(client_location={"lat": 41.03, "lng": 29.00})
    )
    assert "placesId" in out and "could NOT be stored" in out
    assert "41.050000" in out
