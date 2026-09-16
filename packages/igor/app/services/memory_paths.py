# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Central path parsing/building module for the Monthly Memory Architecture (v4 §3).

This module provides the grammar for routing paths inside the monthly layout,
including strict validation, slugification of entity titles (Turkish-aware),
and representation of monthly periods.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import ClassVar, NamedTuple
from zoneinfo import ZoneInfo

# Turkish. `unicodedata` alone is not enough: `ı` and `ğ` have no compatibility
# decomposition, so NFKD leaves them intact and they end up percent-mangled or
# dropped. "Pınar Uzun" and "Doğan" are real entries — they have to round-trip.
_SLUG_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    "â": "a", "î": "i", "û": "u", "é": "e", "ñ": "n", "ß": "ss",
})


def slugify(title: str) -> str:
    """Entity title → filename stem. Deterministic and stable: the slug is the
    file's identity, so the same name must always produce the same path."""
    s = title.strip().translate(_SLUG_MAP).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "untitled"


@dataclass(frozen=True)
class MonthPeriod:
    """A frozen value object representing a specific month in time."""
    month: int
    year: int

    CALENDAR_CENTURY: ClassVar[int] = 2000

    def __post_init__(self) -> None:
        if not (1 <= self.month <= 12):
            raise ValueError(f"Invalid month: {self.month}")
        if not (self.CALENDAR_CENTURY <= self.year <= self.CALENDAR_CENTURY + 99):
            raise ValueError(f"Year out of century bounds: {self.year}")

    @property
    def folder_name(self) -> str:
        """The MM-YY format used in file system folders (e.g. 09-26)."""
        return f"{self.month:02d}-{self.year % 100:02d}"

    @property
    def canonical(self) -> str:
        """The YYYY-MM canonical format (e.g. 2026-09)."""
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def display_label(self) -> str:
        """A human-readable label (e.g. September 2026)."""
        return date(self.year, self.month, 1).strftime("%B %Y")

    @classmethod
    def from_folder(cls, mm_yy: str) -> "MonthPeriod":
        m = re.match(r"^(\d{2})-(\d{2})$", mm_yy)
        if not m:
            raise ValueError(f"Invalid folder name format: {mm_yy}")
        month = int(m.group(1))
        year = cls.CALENDAR_CENTURY + int(m.group(2))
        return cls(month=month, year=year)

    @classmethod
    def from_canonical(cls, yyyy_mm: str) -> "MonthPeriod":
        m = re.match(r"^(\d{4})-(\d{2})$", yyyy_mm)
        if not m:
            raise ValueError(f"Invalid canonical format: {yyyy_mm}")
        return cls(month=int(m.group(2)), year=int(m.group(1)))

    @classmethod
    def from_date(cls, d: date) -> "MonthPeriod":
        return cls(month=d.month, year=d.year)

    @classmethod
    def current(cls, tz_name: str = "Europe/Istanbul") -> "MonthPeriod":
        now = datetime.now(ZoneInfo(tz_name))
        return cls(month=now.month, year=now.year)

    def __lt__(self, other: "MonthPeriod") -> bool:
        if not isinstance(other, MonthPeriod):
            return NotImplemented
        return (self.year, self.month) < (other.year, other.month)

    def __le__(self, other: "MonthPeriod") -> bool:
        if not isinstance(other, MonthPeriod):
            return NotImplemented
        return (self.year, self.month) <= (other.year, other.month)

    def __gt__(self, other: "MonthPeriod") -> bool:
        if not isinstance(other, MonthPeriod):
            return NotImplemented
        return (self.year, self.month) > (other.year, other.month)

    def __ge__(self, other: "MonthPeriod") -> bool:
        if not isinstance(other, MonthPeriod):
            return NotImplemented
        return (self.year, self.month) >= (other.year, other.month)

    def next_month(self) -> "MonthPeriod":
        """Chronologically the next month."""
        if self.month == 12:
            return MonthPeriod(month=1, year=self.year + 1)
        return MonthPeriod(month=self.month + 1, year=self.year)

    def prev_month(self) -> "MonthPeriod":
        """Chronologically the previous month."""
        if self.month == 1:
            return MonthPeriod(month=12, year=self.year - 1)
        return MonthPeriod(month=self.month - 1, year=self.year)

    def contains_date(self, d: date) -> bool:
        """True if the given date falls in this period."""
        return d.year == self.year and d.month == self.month


CATEGORIES = frozenset({
    'general', 'owner', 'dossier', 'patterns', 'projects', 'social', 'wellness',
    'academic', 'finance', 'ops', 'cybersec', 'states', 'history'
})

SOCIAL_GROUPS = frozenset({'personal', 'professional'})

SYSTEM_PREFIXES = ('.views/', '.archive/', '.audit/')


class MonthlyPath(NamedTuple):
    category: str
    period: MonthPeriod
    group: str | None
    slug: str


def is_system_path(path: str) -> bool:
    """True if path is under system prefixes."""
    if not path.startswith("/memories/"):
        return False
    p = path[len("/memories/"):]
    return any(p.startswith(prefix) for prefix in SYSTEM_PREFIXES)


def parse_monthly_path(path: str) -> MonthlyPath | None:
    """Parse a monthly path into components. Returns None for non-monthly paths."""
    if not path.startswith("/memories/"):
        return None
        
    if is_system_path(path):
        return None

    p = path[len("/memories/"):]
    parts = p.split("/")
    
    if len(parts) < 3 or len(parts) > 4:
        return None
        
    category = parts[0]
    if category not in CATEGORIES:
        return None
        
    try:
        period = MonthPeriod.from_folder(parts[1])
    except ValueError:
        return None
        
    if len(parts) == 4:
        if category != 'social':
            return None
        group = parts[2]
        if group not in SOCIAL_GROUPS:
            return None
        filename = parts[3]
    else:
        if category == 'social':
            return None
        group = None
        filename = parts[2]
        
    if not filename.endswith(".md"):
        return None
        
    slug = filename[:-3]
    if not slug:
        return None
        
    return MonthlyPath(category=category, period=period, group=group, slug=slug)


def build_monthly_path(category: str, period: MonthPeriod, slug: str, group: str | None = None) -> str:
    """Construct the canonical path. Validates category, group, and slug."""
    if category not in CATEGORIES:
        raise ValueError(f"Invalid category: {category}")
    if category == 'social' and group not in SOCIAL_GROUPS:
        raise ValueError(f"Social path requires valid group, got: {group}")
    if category != 'social' and group is not None:
        raise ValueError(f"Only social paths have groups, got group for: {category}")
    if not slug:
        raise ValueError("Slug cannot be empty")
        
    slugified = slugify(slug)
    if group:
        return f"/memories/{category}/{period.folder_name}/{group}/{slugified}.md"
    return f"/memories/{category}/{period.folder_name}/{slugified}.md"


def is_monthly_path(path: str) -> bool:
    """True if path matches the monthly grammar."""
    return parse_monthly_path(path) is not None


def validate_path(path: str) -> list[str]:
    """Return a list of validation errors."""
    errors = []
    
    if ".." in path:
        errors.append("Path traversal '..' is not allowed")
    if "\\" in path:
        errors.append("Backslashes are not allowed in paths")
    if "%2F" in path or "%2f" in path:
        errors.append("URL encoded separators '%2F' are not allowed")
    if "//" in path:
        errors.append("Empty segments '//' are not allowed")
        
    if not path.startswith("/memories/"):
        errors.append("Path must start with /memories/")
        
    if not path.endswith(".md"):
        errors.append("Path must end with .md")
        
    if is_system_path(path):
        return errors
        
    if path.startswith("/memories/"):
        p = path[len("/memories/"):]
        parts = [part for part in p.split("/") if part and part != ".."]
        
        if len(parts) >= 2:
            cat = parts[0]
            if cat not in CATEGORIES and not any(p.startswith(prefix) for prefix in SYSTEM_PREFIXES):
                errors.append(f"Unsupported category: {cat}")
                
            mmyy = parts[1]
            m = re.match(r"^(\d{2})-(\d{2})$", mmyy)
            if m:
                month = int(m.group(1))
                if not (1 <= month <= 12):
                    errors.append(f"Invalid month: {month}")
            else:
                errors.append("Invalid month format, expected MM-YY")
                
            if cat == 'social':
                if len(parts) == 4:
                    group = parts[2]
                    if group not in SOCIAL_GROUPS:
                        errors.append(f"Invalid social group: {group}")
                elif len(parts) == 3:
                    errors.append("Social paths must include a group")
            else:
                if len(parts) == 4:
                    errors.append(f"Category {cat} does not support groups")
                
    return errors
