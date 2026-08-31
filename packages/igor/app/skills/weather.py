# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Weather desk (Tier 1) — current conditions + short forecast, keyless.

Backed by Open-Meteo: no signup, no quota, and for Turkey it resolves to the
ECMWF-blended "best_match" model — the same forecast family national
meteorological services across Europe build on, and consistently the closest
free, keyless match to what MGM's own site shows. A paid provider (OpenWeather,
WeatherAPI.com) would add a config key and a quota for no accuracy gain here,
so this mirrors news_headlines' Tier-1 pattern: always on, zero cost, zero
setup. Geocoding (`location` given as free text) uses Open-Meteo's own
keyless geocoding endpoint, tried in Turkish first since that's the owner's
locale, English second, unmodified third — a plain city/place name in any
language should resolve. `owner_home_lat`/`owner_home_lng` (already exposed on
the Configuration tab under Maps & Navigation) is the fallback origin when no
`location` is given, mirroring app/skills/navigation.py's "from home" pattern
exactly rather than inventing a second home-location setting.
"""

import logging

import httpx

from app.config import settings
from app.core.context import AgentContext
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = httpx.Timeout(15.0, connect=6.0)

# WMO weather interpretation codes (open-meteo.com/en/docs — "WMO Weather
# interpretation codes" table). Every code the API can return is listed; an
# unknown one falls back to "code N" rather than raising.
_WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def _describe(code) -> str:
    try:
        return _WMO_CODES.get(int(code), f"code {code}")
    except (TypeError, ValueError):
        return "unknown"


def _home_coords() -> dict | None:
    lat, lng = settings.owner_home_lat, settings.owner_home_lng
    if lat in (None, "") or lng in (None, ""):
        return None
    try:
        return {"lat": float(lat), "lng": float(lng), "label": "home"}
    except (TypeError, ValueError):
        return None


async def _geocode(place: str) -> dict | None:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for lang in ("tr", "en"):
            try:
                resp = await client.get(_GEOCODE_URL, params={
                    "name": place, "count": 1, "language": lang, "format": "json",
                })
                resp.raise_for_status()
            except Exception:  # noqa: BLE001 — try the next language
                continue
            results = resp.json().get("results") or []
            if results:
                r = results[0]
                label = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
                return {"lat": r["latitude"], "lng": r["longitude"], "label": label}
    return None


class WeatherSkill(Skill):
    name = "weather"
    deferred = True
    search_keywords = (
        "weather forecast temperature rain snow wind hava durumu "
        "sıcaklık yağmur kar rüzgar"
    )
    description = (
        "Returns current conditions and a short daily forecast for a place, via "
        "Open-Meteo's free, keyless forecast API (no quota, always available). "
        "Use it whenever the owner asks about weather, or a briefing needs "
        "today's conditions before he leaves the house. Pass 'location' as a "
        "plain place name (city, district, or landmark, in Turkish or English — "
        "'Ankara', 'Kadıköy', 'OSTİM') when the owner named one; leave it empty "
        "to use his configured home coordinates instead, which is the right "
        "default for a morning briefing. Do NOT use this for historical "
        "weather, air-quality data, or marine/aviation forecasts — it only "
        "covers current + near-term surface conditions. Returns current "
        "temperature, feels-like, humidity, wind and condition, plus a "
        "day-by-day high/low, rain-probability and condition outlook for the "
        "requested number of days; if the place can't be resolved it says so "
        "rather than guessing a location."
    )
    read_only = True
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": (
                    "Place name to look up, e.g. 'Ankara' or 'Kadıköy, İstanbul'. "
                    "Omit to use the owner's configured home location."
                ),
            },
            "days": {
                "type": "integer",
                "description": "How many days of forecast to include, 1-7 (default 3). Use 1 for 'just today'.",
                "default": 3,
            },
        },
        "required": [],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        location = (args.get("location") or "").strip()
        days = min(max(int(args.get("days", 3) or 3), 1), 7)

        if location:
            place = await _geocode(location)
            if place is None:
                return (
                    f"weather: could not resolve '{location}' to a place. Try a "
                    "different spelling, a nearby larger city, or the province name."
                )
        else:
            place = _home_coords()
            if place is None:
                return (
                    "weather: no 'location' was given and no home coordinates are "
                    "configured (owner_home_lat/owner_home_lng, Configuration → "
                    "Maps & Navigation). Ask the owner which place to check, or "
                    "have him set his home location."
                )

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(_FORECAST_URL, params={
                    "latitude": place["lat"],
                    "longitude": place["lng"],
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                               "precipitation,weather_code,wind_speed_10m,wind_gusts_10m",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                             "precipitation_sum,precipitation_probability_max,"
                             "wind_speed_10m_max,sunrise,sunset",
                    "timezone": "auto",
                    "forecast_days": days,
                })
                resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return f"weather: could not reach Open-Meteo ({type(e).__name__}: {e})."

        data = resp.json()
        cur = data.get("current") or {}
        daily = data.get("daily") or {}

        lines = [f"WEATHER — {place['label']}:"]
        if cur:
            lines.append(
                f"- Now: {cur.get('temperature_2m')}°C (feels {cur.get('apparent_temperature')}°C), "
                f"{_describe(cur.get('weather_code'))}, humidity {cur.get('relative_humidity_2m')}%, "
                f"wind {cur.get('wind_speed_10m')} km/h (gusts {cur.get('wind_gusts_10m')} km/h)"
                + (f", precipitation {cur.get('precipitation')} mm" if cur.get("precipitation") else "")
            )

        dates = daily.get("time") or []
        for i, date in enumerate(dates):
            lines.append(
                f"- {date}: {_describe(daily['weather_code'][i])}, "
                f"{daily['temperature_2m_min'][i]}–{daily['temperature_2m_max'][i]}°C, "
                f"rain chance {daily['precipitation_probability_max'][i]}%"
                + (f", {daily['precipitation_sum'][i]} mm" if daily.get("precipitation_sum", [0])[i] else "")
                + f"  ·  wind up to {daily['wind_speed_10m_max'][i]} km/h"
            )

        return "\n".join(lines)
