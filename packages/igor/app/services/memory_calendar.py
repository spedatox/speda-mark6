# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Temporal model helpers for the Monthly Memory Architecture (§4).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.clock import owner_tz, owner_today

KINDS = {
    'event', 
    'reference', 
    'edition', 
    'state', 
    'biography', 
    'preference', 
    'pattern', 
    'projection', 
    'system'
}


class PeriodBasis(str, Enum):
    OCCURRENCE = 'occurrence'
    EFFECTIVE = 'effective'
    RECORDED = 'recorded'
    LEGACY_RECORDED = 'legacy_recorded'


class DatePrecision(str, Enum):
    DAY = 'day'
    MONTH = 'month'
    RANGE = 'range'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class TemporalResolution:
    period: str              # 'YYYY-MM' canonical
    period_basis: PeriodBasis
    occurred_on: date | None
    occurred_until: date | None
    effective_from: date | None
    effective_until: date | None
    date_precision: DatePrecision
    source_timezone: str | None


def _get_recorded_date(recorded_at: datetime | None, source_timezone: str | None) -> date:
    """Helper to convert recorded_at instant to owner's wall clock or source timezone."""
    if not recorded_at:
        return owner_today()
        
    tz_name = source_timezone or owner_tz().key
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
        
    if recorded_at.tzinfo is None:
        aware = recorded_at.replace(tzinfo=timezone.utc)
    else:
        aware = recorded_at
        
    return aware.astimezone(tz).date()


def month_for_event(
    kind: str,
    occurred_on: date | None = None,
    occurred_until: date | None = None,
    effective_from: date | None = None,
    recorded_at: datetime | None = None,
    source_timezone: str | None = None,
) -> TemporalResolution:
    """
    Deterministic month selection following §4.2 rules.
    """
    period_basis = PeriodBasis.RECORDED
    ref_date = _get_recorded_date(recorded_at, source_timezone)
    precision = DatePrecision.UNKNOWN

    if occurred_on:
        precision = DatePrecision.DAY
    elif effective_from:
        precision = DatePrecision.DAY

    if kind == 'event':
        # Rule 1: Completed event with known date → occurrence month
        # Rule 2: Event known only to a month is handled inherently by occurred_on (using 1st of month)
        if occurred_on:
            period_basis = PeriodBasis.OCCURRENCE
            ref_date = occurred_on
        else:
            # Rule 3: Event with no known occurrence → recording month
            period_basis = PeriodBasis.RECORDED
    elif kind in ('preference', 'reference'):
        # Rule 4: Preference/reference → effective month or first-recorded month
        if effective_from:
            period_basis = PeriodBasis.EFFECTIVE
            ref_date = effective_from
        else:
            period_basis = PeriodBasis.RECORDED
    elif kind == 'state':
        # Rule 5: State transition → evidenced transition date
        if effective_from:
            period_basis = PeriodBasis.EFFECTIVE
            ref_date = effective_from
        else:
            period_basis = PeriodBasis.RECORDED
    elif kind == 'biography':
        # Rule 6: Identity/biography without historical chronology
        if recorded_at:
            period_basis = PeriodBasis.RECORDED
        else:
            period_basis = PeriodBasis.LEGACY_RECORDED

    period = ref_date.strftime('%Y-%m')

    # Determine precision based on input presence
    if occurred_until and occurred_on and occurred_on != occurred_until:
        precision = DatePrecision.RANGE

    return TemporalResolution(
        period=period,
        period_basis=period_basis,
        occurred_on=occurred_on,
        occurred_until=occurred_until,
        effective_from=effective_from,
        effective_until=None,
        date_precision=precision,
        source_timezone=source_timezone
    )


def resolve_relative_date(text: str, source_timestamp: datetime, owner_tz_name: str) -> tuple[date | None, DatePrecision]:
    """
    Resolve simple relative dates based on the SOURCE MESSAGE timestamp in the owner's timezone.
    Returns (resolved_date, precision).
    """
    text_lower = text.lower().strip()
    
    try:
        tz = ZoneInfo(owner_tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo('UTC')
        
    if source_timestamp.tzinfo is None:
        aware_src = source_timestamp.replace(tzinfo=timezone.utc)
    else:
        aware_src = source_timestamp
        
    ref_date = aware_src.astimezone(tz).date()
    
    # Exact days
    if text_lower in ('today', 'bugün', 'bugun'):
        return ref_date, DatePrecision.DAY
    elif text_lower in ('yesterday', 'dün', 'dun'):
        return ref_date - timedelta(days=1), DatePrecision.DAY
        
    # YYYY-MM-DD
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text_lower)
    if match:
        try:
            d = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return d, DatePrecision.DAY
        except ValueError:
            pass
            
    # Month precision
    if 'this month' in text_lower or 'bu ay' in text_lower:
        return None, DatePrecision.MONTH
        
    months = [
        'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december',
        'ocak', 'şubat', 'subat', 'mart', 'nisan', 'mayıs', 'mayis', 'haziran', 'temmuz', 'ağustos', 'agustos', 'eylül', 'eylul', 'ekim', 'kasım', 'kasim', 'aralık', 'aralik'
    ]
    if any(m in text_lower for m in months):
        return None, DatePrecision.MONTH
        
    # Last Friday (simple relative weekday offset)
    days = [
        ('monday', 'pazartesi'),
        ('tuesday', 'salı', 'sali'),
        ('wednesday', 'çarşamba', 'carsamba'),
        ('thursday', 'perşembe', 'persembe'),
        ('friday', 'cuma'),
        ('saturday', 'cumartesi'),
        ('sunday', 'pazar')
    ]
    
    for i, d_aliases in enumerate(days):
        for alias in d_aliases:
            if f"last {alias}" in text_lower or f"geçen {alias}" in text_lower or f"gecen {alias}" in text_lower:
                target_weekday = i
                current_weekday = ref_date.weekday()
                diff = current_weekday - target_weekday
                if diff <= 0:
                    diff += 7
                return ref_date - timedelta(days=diff), DatePrecision.DAY
                
    return None, DatePrecision.UNKNOWN


def overlaps_period(period_canonical: str, start: date | None, end: date | None) -> bool:
    """
    Does a date range overlap a given month? For cross-month experiences (§4.3)
    """
    try:
        period_year = int(period_canonical[:4])
        period_month = int(period_canonical[5:7])
    except ValueError:
        return False
        
    period_start = date(period_year, period_month, 1)
    if period_month == 12:
        period_end = date(period_year + 1, 1, 1) - timedelta(days=1)
    else:
        period_end = date(period_year, period_month + 1, 1) - timedelta(days=1)
        
    range_start = start or date.min
    range_end = end or date.max
    
    return range_start <= period_end and range_end >= period_start


def is_future_date(d: date, owner_tz_name: str = 'Europe/Istanbul') -> bool:
    """
    Is the date in the future relative to the owner's current date?
    """
    try:
        tz = ZoneInfo(owner_tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo('UTC')
        
    today = datetime.now(tz).date()
    return d > today
