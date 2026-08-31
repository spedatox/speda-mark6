# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The clock policy: instants in UTC, wall clocks in the owner's zone.

These exist because the failure they guard against is invisible — nothing
crashes when a timestamp is three hours out, it just quietly asks about the
wrong lecture and stamps the wrong time on every message the agents read.
"""

from datetime import datetime, timezone

from app.config import settings
from app.core import clock


def test_default_timezone_is_the_owner_not_utc():
    # The whole bug was a "UTC" default nobody overrode in .env.
    assert settings.owner_timezone == "Europe/Istanbul"


def test_owner_now_is_ahead_of_utc_and_naive():
    utc = clock.utc_now()
    local = clock.owner_now()
    assert local.tzinfo is None, "must be naive — wall-clock columns are naive"
    # Istanbul is UTC+3 year-round; allow a second of execution drift.
    delta = (local - utc.replace(tzinfo=None)).total_seconds()
    assert 3 * 3600 - 5 < delta < 3 * 3600 + 5


def test_to_owner_treats_a_naive_input_as_utc():
    stored = datetime(2026, 7, 28, 11, 0, 0)          # what utcnow() columns hold
    assert clock.to_owner(stored) == datetime(2026, 7, 28, 14, 0, 0)


def test_to_owner_respects_an_aware_input():
    aware = datetime(2026, 7, 28, 11, 0, 0, tzinfo=timezone.utc)
    assert clock.to_owner(aware) == datetime(2026, 7, 28, 14, 0, 0)


def test_owner_today_crosses_midnight_before_utc_does():
    # 22:30 Istanbul on the 28th is still 19:30 UTC on the 28th, but 01:30
    # Istanbul on the 29th is 22:30 UTC on the 28th — the case that put health
    # samples on the wrong day.
    late = datetime(2026, 7, 28, 22, 30, tzinfo=timezone.utc)
    assert clock.to_owner(late).date().isoformat() == "2026-07-29"


def test_a_broken_timezone_name_falls_back_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(settings, "owner_timezone", "Mars/Olympus_Mons")
    clock._warned = False
    assert clock.owner_tz().key == "UTC"
    monkeypatch.setattr(settings, "owner_timezone", "Europe/Istanbul")
