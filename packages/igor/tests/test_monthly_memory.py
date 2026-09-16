# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.services.memory_paths import (
    MonthPeriod,
    slugify,
    parse_monthly_path,
    build_monthly_path,
    is_monthly_path,
    validate_path,
    CATEGORIES,
)
from app.services.memory_calendar import (
    month_for_event,
    resolve_relative_date,
    overlaps_period,
    is_future_date,
    PeriodBasis,
    DatePrecision,
)
from app.services.memory_spec import (
    collection_for,
    spec_for,
    is_retired,
    COLLECTIONS,
)
from app.services.memory_policy import (
    ROUTES,
    routing_contract,
    protected_write,
    route_for_kind,
)


def test_month_period():
    p = MonthPeriod(9, 2026)
    assert p.folder_name == "09-26"
    assert p.canonical == "2026-09"
    assert p.display_label == "September 2026"

    # Comparison
    p_oct = MonthPeriod(10, 2026)
    p_prev_year = MonthPeriod(12, 2025)
    assert p < p_oct
    assert p > p_prev_year

    # Arithmetic
    assert p.next_month() == MonthPeriod(10, 2026)
    assert p.prev_month() == MonthPeriod(8, 2026)
    assert MonthPeriod(12, 2026).next_month() == MonthPeriod(1, 2027)
    assert MonthPeriod(1, 2026).prev_month() == MonthPeriod(12, 2025)

    # Parsing
    assert MonthPeriod.from_folder("09-26") == p
    assert MonthPeriod.from_canonical("2026-09") == p
    assert MonthPeriod.from_date(date(2026, 9, 16)) == p

    # Validation
    with pytest.raises(ValueError):
        MonthPeriod(13, 2026)
    with pytest.raises(ValueError):
        MonthPeriod(0, 2026)


def test_turkish_slugify():
    assert slugify("İstanbul Gezisi") == "istanbul-gezisi"
    assert slugify("Pınar Uzun") == "pinar-uzun"
    assert slugify("Doğan & Şahin") == "dogan-sahin"
    assert slugify("Öğrenci İşleri") == "ogrenci-isleri"
    assert slugify("Çalışma Odası") == "calisma-odasi"


def test_monthly_paths():
    p = MonthPeriod(9, 2026)
    path = build_monthly_path("general", p, "istanbul-trip")
    assert path == "/memories/general/09-26/istanbul-trip.md"
    assert is_monthly_path(path)

    parsed = parse_monthly_path(path)
    assert parsed is not None
    assert parsed.category == "general"
    assert parsed.period == p
    assert parsed.slug == "istanbul-trip"
    assert parsed.group is None

    # Social group
    social_path = build_monthly_path("social", p, "ali-yilmaz", group="personal")
    assert social_path == "/memories/social/09-26/personal/ali-yilmaz.md"
    parsed_soc = parse_monthly_path(social_path)
    assert parsed_soc is not None
    assert parsed_soc.category == "social"
    assert parsed_soc.group == "personal"
    assert parsed_soc.slug == "ali-yilmaz"

    # Validation
    assert len(validate_path("/memories/general/09-26/istanbul-trip.md")) == 0
    assert len(validate_path("/memories/general/../bad.md")) > 0
    assert len(validate_path("/memories/general/13-26/trip.md")) > 0
    assert len(validate_path("/memories/unknown_cat/09-26/trip.md")) > 0


def test_memory_calendar_rules():
    # Rule 1: Known occurrence date
    r1 = month_for_event("event", occurred_on=date(2026, 8, 29))
    assert r1.period == "2026-08"
    assert r1.period_basis == PeriodBasis.OCCURRENCE
    assert r1.date_precision == DatePrecision.DAY

    # Rule 3: Unknown occurrence -> recorded date
    now_utc = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    r3 = month_for_event("event", occurred_on=None, recorded_at=now_utc)
    assert r3.period == "2026-09"
    assert r3.period_basis == PeriodBasis.RECORDED
    assert r3.date_precision == DatePrecision.UNKNOWN

    # Relative date resolution
    msg_time = datetime(2026, 9, 16, 14, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
    d_today, prec = resolve_relative_date("bugün", msg_time, "Europe/Istanbul")
    assert d_today == date(2026, 9, 16)
    assert prec == DatePrecision.DAY

    d_yesterday, _ = resolve_relative_date("yesterday", msg_time, "Europe/Istanbul")
    assert d_yesterday == date(2026, 9, 15)


def test_memory_spec_collection_for():
    # Monthly path for general
    coll = collection_for("/memories/general/09-26/istanbul-trip.md")
    assert coll is not None
    assert coll.root == "/memories/general"
    assert coll.monthly is True

    # Check spec_for
    spec = spec_for("/memories/general/09-26/istanbul-trip.md")
    assert spec is not None
    assert spec.path == "/memories/general/09-26/istanbul-trip.md"

    # events collection is retired
    events_coll = collection_for("/memories/events/2026-09.md")
    assert events_coll is not None
    assert is_retired("/memories/life")


def test_memory_policy():
    exp_route = route_for_kind("experience")
    assert exp_route is not None
    assert "general" in exp_route.destination
    assert exp_route.tool == "memory_event"

    contract = routing_contract()
    assert "general/" in contract
    assert "events/ is retired" in contract

    # Protected writes
    assert protected_write("/memories/current.md", "speda") is not None
    assert protected_write("/memories/finance/09-26/ledger.md", "speda") is not None
    assert protected_write("/memories/.views/overview.md", "speda") is not None
    assert protected_write("/memories/general/09-26/istanbul-trip.md", "owner") is None
