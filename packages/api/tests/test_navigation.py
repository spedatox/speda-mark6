"""Unit tests for the navigation skills (get_route / find_places).

Network is mocked — these assert the request shaping, origin defaulting, and the
graceful-degradation paths (no key, no location, upstream error), never a live
Google call.
"""

import json
from types import SimpleNamespace

import pytest

from app.config import settings
from app.skills.navigation import FindPlacesSkill, GetRouteSkill, _dur_min, _maps_dir_link


def _ctx(client_location=None):
    """Minimal AgentContext stand-in — the skills only read .extra."""
    return SimpleNamespace(extra={"client_location": client_location} if client_location else {})


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


# ── get_route ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_route_no_key(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "")
    out = await GetRouteSkill().execute({"destination": "Kadıköy"}, _ctx())
    assert "unavailable" in out.lower()


@pytest.mark.asyncio
async def test_get_route_defaults_origin_to_client_location(monkeypatch):
    # destination is a coordinate object (no geocode call); origin defaults to the
    # live client location → exactly one POST to computeRoutes.
    routes_payload = {
        "routes": [{
            "duration": "2040s", "staticDuration": "1320s", "distanceMeters": 18400,
            "polyline": {"encodedPolyline": "abcd"}, "description": "D-100",
            "routeLabels": ["DEFAULT_ROUTE"],
        }]
    }
    fake = _FakeClient([_FakeResponse(200, routes_payload)])
    _patch_client(monkeypatch, fake)

    ctx = _ctx(client_location={"lat": 41.00, "lng": 29.00, "place": "Kadıköy"})
    out = await GetRouteSkill().execute(
        {"destination": json.dumps({"lat": 41.11, "lng": 29.02})}, ctx
    )

    assert len(fake.calls) == 1
    method, url, body, _ = fake.calls[0]
    assert method == "POST" and "computeRoutes" in url
    assert body["origin"]["location"]["latLng"]["latitude"] == 41.00
    assert body["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"
    assert "34 min" in out.lower()          # 2040s → 34 min
    assert "+12 min vs no-traffic" in out   # 34 − 22
    assert "polyline=abcd" in out


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


# ── find_places ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_places_biases_to_client_location(monkeypatch):
    places_payload = {
        "places": [{
            "displayName": {"text": "Kronotrop"},
            "location": {"latitude": 41.05, "longitude": 29.01},
            "rating": 4.6, "userRatingCount": 320,
            "currentOpeningHours": {"openNow": True},
            "formattedAddress": "Cihangir, İstanbul",
        }]
    }
    fake = _FakeClient([_FakeResponse(200, places_payload)])
    _patch_client(monkeypatch, fake)

    ctx = _ctx(client_location={"lat": 41.03, "lng": 29.00})
    out = await FindPlacesSkill().execute({"query": "specialty coffee"}, ctx)

    method, url, body, _ = fake.calls[0]
    assert "searchText" in url
    assert body["textQuery"] == "specialty coffee"
    assert body["locationBias"]["circle"]["center"]["latitude"] == 41.03
    assert "Kronotrop" in out and "4.6★" in out and "open now" in out
