"""
Atomix's daily training-program document — a fixed, designed HTML/CSS template
(app/templates/reports/atomix_daily_program_template.html) filled with the
session's content and rendered straight to PDF for delivery as a downloadable
(and Telegram-sendable) file.

The design (Barlow Condensed masthead, the Atomix mark, the dossier grid) is
locked. Claude never authors HTML for this document — it only supplies the
session fields below; this module does plain marker-based substitution
(no Jinja2 — mirrors the literal-token approach already used in
prompts/loader.py::load_section) so the template can never be mangled by a
generation turn. If the template's markers are ever edited incompatibly,
rendering fails loudly instead of silently emitting broken markup.

This is the ONLY path that produces the daily training-program document.
Do not reach for generate_document (generic reports) or the sandbox (ad hoc
scripts) for this — both bypass the locked design and are exactly what caused
the same session to yield three different-looking, sometimes-logo-less PDFs
before this module rendered straight to PDF itself.
"""

import base64
import html
import logging
import re
import uuid
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.core.context import AgentContext
from app.core.files import register_file
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = (
    Path(__file__).parent.parent / "templates" / "reports" / "atomix_daily_program_template.html"
)
_FONTS_DIR = Path(__file__).parent / "fonts"

# Every @font-face src the template references, relative exactly as it appears
# in the template's <style> block. The rendered output is written to a
# different directory (settings.temp_outputs_dir), so that relative path would
# 404 there — each one gets swapped for a base64 data: URI at render time,
# making the delivered file fully self-contained (openable from disk, no
# server round-trip needed to see the right fonts).
_FONT_FILES = [
    "DejaVuSans.ttf",
    "DejaVuSans-Bold.ttf",
    "DejaVuSans-Oblique.ttf",
    "DejaVuSansMono.ttf",
    "BarlowCondensed-Medium.ttf",
    "BarlowCondensed-Bold.ttf",
    "BarlowCondensed-ExtraBold.ttf",
]


@lru_cache(maxsize=1)
def _font_data_uris() -> dict[str, str]:
    """Read each bundled font once per process and return {filename: data URI}.
    A missing file is skipped (logged) rather than raising — the document still
    renders, just falls back to whatever font the viewer substitutes."""
    uris: dict[str, str] = {}
    for fname in _FONT_FILES:
        path = _FONTS_DIR / fname
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            uris[fname] = f"data:font/ttf;base64,{b64}"
        except OSError as e:
            logger.warning("atomix_daily_program_font_missing", extra={"file": fname, "error": str(e)})
    return uris


@lru_cache(maxsize=1)
def _template_source() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _extract_block(source: str, marker: str) -> tuple[str, str]:
    """Pull the row template out of a <!--MARKER-->...<!--/MARKER--> region.
    Returns (row_template, full_matched_region_including_markers). Raises if the
    marker pair isn't found — a broken template must fail the render, not
    silently drop a whole section."""
    m = re.search(rf"<!--{marker}-->(.*?)<!--/{marker}-->", source, re.S)
    if not m:
        raise ValueError(f"Template marker '{marker}' not found — atomix_daily_program_template.html was edited incompatibly.")
    return m.group(1), m.group(0)


class ExerciseRow(BaseModel):
    name: str
    load: str
    note: str = ""


class DailyProgramInput(BaseModel):
    day_name: str
    week_label: str
    date: str
    date_human: str
    goal: str
    warmup: list[str]
    exercises: list[ExerciseRow]
    finisher: str
    rules: list[str]


def _render_html(data: DailyProgramInput) -> str:
    out = _template_source()

    warm_row, warm_block = _extract_block(out, "WARMUP_ROW")
    out = out.replace(warm_block, "".join(warm_row.replace("[[ITEM]]", _esc(item)) for item in data.warmup), 1)

    ex_row, ex_block = _extract_block(out, "EXERCISE_ROW")
    ex_rows = "".join(
        ex_row.replace("[[N]]", str(i)).replace("[[NAME]]", _esc(ex.name))
        .replace("[[LOAD]]", _esc(ex.load)).replace("[[NOTE]]", _esc(ex.note))
        for i, ex in enumerate(data.exercises, start=1)
    )
    out = out.replace(ex_block, ex_rows, 1)

    rule_row, rule_block = _extract_block(out, "RULE_ROW")
    out = out.replace(rule_block, "".join(rule_row.replace("[[RULE]]", _esc(r)) for r in data.rules), 1)

    out = (
        out.replace("[[DATE_HUMAN]]", _esc(data.date_human))
        .replace("[[DAY_NAME]]", _esc(data.day_name))
        .replace("[[WEEK_LABEL]]", _esc(data.week_label))
        .replace("[[GOAL]]", _esc(data.goal))
        .replace("[[FINISHER]]", _esc(data.finisher))
        .replace("[[DATE]]", _esc(data.date))  # after DATE_HUMAN — DATE is a substring-safe token, order doesn't matter here but keeps intent clear
    )

    for fname, uri in _font_data_uris().items():
        out = out.replace(f'url("../../skills/fonts/{fname}")', f'url("{uri}")')

    if "[[" in out:
        logger.warning("atomix_daily_program_stray_token", extra={"sample": out[out.index("[["):out.index("[[") + 40]})

    return out


class GenerateDailyTrainingProgramSkill(Skill):
    name = "generate_daily_training_program"
    deferred = True
    search_keywords = "workout print pdf training program daily gym session document antrenman program yazdır cikti"
    restricted_to = frozenset({"atomix"})
    read_only = False
    description = (
        "Renders today's already-decided training session — the goal, warm-up, main-work "
        "table, finisher, and safety rules — into Atomix's branded daily program PDF and "
        "delivers it as a downloadable file (and, if the owner also wants Telegram, the "
        "file to hand send_telegram_file). Use this ONLY after you have actually worked out "
        "today's session per the Training Protocol (read the session ledger, checked "
        "recovery, chosen the exercises against his record) and he wants it as a document — "
        "not for describing the plan in chat, which stays in the conversation as normal "
        "text. This is the ONLY tool that produces this document: do NOT use generate_document "
        "(generic reports, unbranded) or write an ad hoc script in the sandbox to build a PDF "
        "yourself — both bypass the locked design and produce a different-looking, "
        "sometimes logo-less file every time, which is exactly the inconsistency this tool "
        "exists to prevent. The visual design (fonts, layout, the Atomix mark) is locked; "
        "you only ever supply the session's content fields below — never hand-write or paste "
        "raw HTML/CSS yourself. Returns confirmation that the PDF was generated and "
        "delivered as a download card; do not paste its path or contents back to him."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "day_name": {"type": "string", "description": "Turkish day name, e.g. 'Pazartesi'."},
            "week_label": {"type": "string", "description": "Short block/week label, e.g. 'Hafta 3'."},
            "date": {"type": "string", "description": "ISO date of the session, YYYY-MM-DD."},
            "date_human": {"type": "string", "description": "Human-readable Turkish date, e.g. '10 Ağustos 2026'."},
            "goal": {"type": "string", "description": "One-sentence focus of today's session."},
            "warmup": {
                "type": "array", "items": {"type": "string"},
                "description": "Warm-up steps, in the order they should be performed.",
            },
            "exercises": {
                "type": "array",
                "description": "Main-work rows, in order, top to bottom.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Exercise name."},
                        "load": {"type": "string", "description": "Sets × reps @ load, e.g. '3 × 10 @ 60 kg'."},
                        "note": {"type": "string", "description": "Short coaching cue or caution specific to this exercise."},
                    },
                    "required": ["name", "load", "note"],
                },
            },
            "finisher": {"type": "string", "description": "Conditioning/finisher line. Empty string if none today."},
            "rules": {
                "type": "array", "items": {"type": "string"},
                "description": "Session-specific safety rules and stop conditions (injury routing, hard caps).",
            },
        },
        "required": ["day_name", "week_label", "date", "date_human", "goal", "warmup", "exercises", "finisher", "rules"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        try:
            data = DailyProgramInput(**args)
        except ValidationError as e:
            return f"Invalid session data — fix and retry: {e}"

        try:
            rendered = _render_html(data)
        except Exception as e:  # noqa: BLE001
            logger.error(
                "atomix_daily_program_render_failed",
                extra={"request_id": context.request_id, "error": str(e)},
            )
            return f"Couldn't render the daily program document: {e}"

        from weasyprint import HTML  # type: ignore[import]

        out_dir = Path(settings.temp_outputs_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{uuid.uuid4().hex[:8]}_gunluk-program-{data.date}.pdf"
        try:
            HTML(string=rendered).write_pdf(str(path))
        except Exception as e:  # noqa: BLE001
            logger.error(
                "atomix_daily_program_pdf_failed",
                extra={"request_id": context.request_id, "error": str(e)},
            )
            return f"Couldn't render the daily program PDF: {e}"

        meta = register_file(context, str(path), title=f"Günlük Antrenman Programı — {data.date_human}")
        logger.info(
            "atomix_daily_program_generated",
            extra={"request_id": context.request_id, "file_name": meta["name"], "size": meta["size"]},
        )
        return (
            f"Generated the daily training program PDF ({meta['name']}, {meta['kind']}, "
            f"{meta['size']} bytes) and delivered it to the owner as a downloadable file. "
            f"Do NOT paste its path or contents — just tell him it's ready. If he also wants "
            f"it on Telegram, pass this same file to send_telegram_file — do not regenerate it."
        )
