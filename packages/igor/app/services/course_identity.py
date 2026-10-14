# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic course identity guards; no generated or translated names."""

import re
import unicodedata

from app.services.memory_spec import is_course_path


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def course_heading(text: str) -> tuple[str, str]:
    heading = next((line.strip() for line in text.splitlines() if line.startswith("# ")), "")
    match = re.fullmatch(r"# ([A-Z][A-Z0-9]*\d[A-Z0-9]*)(?:\s+[—–-]\s+(.+))?", heading)
    if not match:
        raise ValueError("Course record requires its exact course code in the first heading. Read the record; do not guess its identity.")
    return match.group(1), (match.group(2) or "")


def check_course_identity(path: str, before: str | None, after: str | None, evidence: list,
                          *, allow_name_correction: bool = False) -> None:
    """Protect every agent write path, including generic memory_edit.

    An owner correction may change an old heading. Agent notes cannot rename
    the course or silently translate its name. A new optional name must appear
    literally beside the code in supporting evidence; otherwise use a neutral
    code-only record. Filling that neutral name preserves the code and notes.
    The admission reviewer still checks semantic support.
    """
    if not is_course_path(path):
        return
    if after is None:
        raise ValueError("Course records cannot be deleted by an agent; preserve the term's history.")
    code, name = course_heading(after)
    expected = path.rsplit("/", 1)[-1][:-3]
    if code != expected:
        raise ValueError("Course heading and path code disagree. Nothing saved; resolve the code from owner evidence.")
    previous = course_heading(before) if before else None
    naming_neutral_record = previous is None or previous[1] in ("", "Course record")
    if allow_name_correction and not naming_neutral_record:
        owner_quotes = [item for item in evidence if item.get("source_authority") == "owner_statement"]
        if not name or not any(
            re.search(rf"(?<!\w){re.escape(code)}(?!\w)", str(item.get("quote", "")), re.IGNORECASE)
            and normalized_name(name) in normalized_name(str(item.get("quote", "")))
            for item in owner_quotes
        ):
            raise ValueError("A name correction requires the code and exact new name in a literal owner statement; legacy course documents cannot authorize it.")
    if before:
        if (code, name) != previous:
            filling_name = previous[0] == code and previous[1] in ("", "Course record") and bool(name)
            if previous[0] != code or (not allow_name_correction and not filling_name):
                raise ValueError("Course identity is fixed for notes. Use record_course_memory operation=confirm_identity with explicit owner evidence; do not rename or translate it while appending notes.")
    if name and name != "Course record" and naming_neutral_record:
        supported = any(
            re.search(rf"(?<!\w){re.escape(code)}(?!\w)", str(item.get("quote", "")), re.IGNORECASE)
            and normalized_name(name) in normalized_name(str(item.get("quote", "")))
            for item in evidence
        )
        if not supported:
            raise ValueError("course_name is absent from the code's literal evidence. Omit course_name to create a neutral code-only record; do not invent or translate a course name.")
