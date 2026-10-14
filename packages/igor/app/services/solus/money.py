# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact currency amounts and anchored monthly schedules. No float conversions."""
import calendar
import re
from datetime import date


def currency_scale(currency: str) -> int:
    # Deliberate supported set, not a guess that every currency has two places.
    scales = {**dict.fromkeys(("TRY", "USD", "EUR", "GBP", "CHF", "CAD", "AUD", "CNY", "SEK", "NOK", "PLN"), 2),
              "JPY": 0, "KRW": 0, "KWD": 3, "BHD": 3, "OMR": 3}
    if currency not in scales:
        raise ValueError("Unsupported currency; configure its precision before recording money.")
    return scales[currency]


def minor(value: str, currency: str, *, allow_zero: bool = False) -> int:
    scale = currency_scale(currency)
    if not isinstance(value, str) or len(value) > 32 or not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d+)?", value):
        raise ValueError("Amount must be a nonnegative decimal string without separators or exponent.")
    whole, _, fraction = value.partition(".")
    if len(fraction) > scale:
        raise ValueError(f"{currency} allows at most {scale} decimal places.")
    result = int(whole) * 10 ** scale + int(fraction.ljust(scale, "0") or "0")
    if result > 10 ** 15 or (result == 0 and not allow_zero):
        raise ValueError("Amount must be positive and no larger than 10^15 minor units.")
    return result


def money(value: int, scale: int) -> str:
    sign = "-" if value < 0 else ""
    whole, fraction = divmod(abs(value), 10 ** scale)
    return f"{sign}{whole}.{fraction:0{scale}d}" if scale else f"{sign}{whole}"


def allocate(total: int, count: int) -> list[int]:
    if type(total) is not int or total < 0 or type(count) is not int or not 1 <= count <= 600:
        raise ValueError("A schedule needs a nonnegative integer total and 1..600 installments.")
    quotient, remainder = divmod(total, count)
    return [quotient + (index < remainder) for index in range(count)]


def monthly_dates(first: date, count: int) -> list[date]:
    if type(count) is not int or not 1 <= count <= 600:
        raise ValueError("Schedule length must be 1..600.")
    result = []
    for offset in range(count):
        year, month = divmod(first.year * 12 + first.month - 1 + offset, 12)
        result.append(date(year, month + 1, min(first.day, calendar.monthrange(year, month + 1)[1])))
    return result
