# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Credit-card cycles use card booking dates, closing dates, and explicit due rules."""
import calendar
from datetime import date, timedelta


def day_in_month(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def shift_month(value: date, offset: int, day: int) -> date:
    year, month = divmod(value.year * 12 + value.month - 1 + offset, 12)
    return day_in_month(year, month + 1, day)


def cycle_for_booking(booked_on: date, closing_day: int, due_day: int | None, due_offset: int | None):
    close = day_in_month(booked_on.year, booked_on.month, closing_day)
    if booked_on > close:
        close = shift_month(close, 1, closing_day)
    return cycle_for_close(close, closing_day, due_day, due_offset)


def cycle_for_close(close: date, closing_day: int, due_day: int | None, due_offset: int | None):
    if not 1 <= closing_day <= 31 or (due_day is None) == (due_offset is None):
        raise ValueError("Card needs closing day and exactly one due-date rule.")
    previous = shift_month(close, -1, closing_day)
    if due_offset is not None:
        due = close + timedelta(days=due_offset)
    else:
        if not 1 <= due_day <= 31:
            raise ValueError("Due day must be 1..31.")
        due = day_in_month(close.year, close.month, due_day)
        if due <= close:
            due = shift_month(close, 1, due_day)
    return previous, close, due
