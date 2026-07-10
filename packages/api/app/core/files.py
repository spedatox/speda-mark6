"""
Produced-file helpers — the bridge from a skill that creates a file to a
downloadable card in the chat.

Any skill that produces a deliverable file (generate_document, the sandbox
file-saver, …) calls register_file(); the orchestrator then emits a `file` SSE
event per file so the frontend can render a download card. Files live in
settings.temp_outputs_dir and are served by GET /files/{name}.
"""

import os
from pathlib import Path

from app.config import settings
from app.core.context import AgentContext

# Friendly type label by extension (drives the card's "Document · PDF" subtitle).
_KIND = {
    ".pdf": "PDF", ".docx": "Word", ".pptx": "PowerPoint", ".xlsx": "Excel",
    ".csv": "CSV", ".txt": "Text", ".md": "Markdown", ".json": "JSON",
    ".png": "Image", ".jpg": "Image", ".jpeg": "Image", ".svg": "Image",
    ".zip": "Archive", ".html": "HTML",
}


def kind_for(name: str) -> str:
    return _KIND.get(Path(name).suffix.lower(), "File")


def register_file(context: AgentContext, path: str, title: str | None = None) -> dict:
    """
    Record a file (already written into temp_outputs_dir) for delivery this turn.
    Returns the metadata dict that will become a `file` SSE event.
    """
    p = Path(path)
    name = p.name
    size = p.stat().st_size if p.exists() else 0
    meta = {
        "name": name,
        "title": title or p.stem,
        "kind": kind_for(name),
        "size": size,
        "url": f"/files/{name}",
    }
    context.extra.setdefault("produced_files", []).append(meta)
    return meta


def safe_output_path(name: str) -> Path | None:
    """Resolve a filename to a path inside temp_outputs_dir, blocking traversal.

    When the exact name isn't found, falls back to a suffix match so that
    title-based lookups (e.g. ``"Gym B July 11.pdf"``) find the UUID-prefixed
    file on disk (e.g. ``"a1b2c3d4_Gym_B_July_11.pdf"``).
    """
    base = Path(settings.temp_outputs_dir).resolve()
    basename = os.path.basename(name)

    # ── Exact match ──────────────────────────────────────────────────────────
    target = (base / basename).resolve()
    if str(target).startswith(str(base)) and target.is_file():
        return target

    # ── Fuzzy suffix match ───────────────────────────────────────────────────
    # generate_document writes files as  {uuid8}_{safe_title}.{ext}
    # The caller often only knows the title + extension, so we normalise both
    # sides and look for a suffix match on the stem.
    import re

    stem = Path(basename).stem
    ext = Path(basename).suffix.lower()

    # Normalise the query the same way _safe_name does in documents.py:
    # non-word chars → underscore, collapse runs, strip edges.
    norm = re.sub(r"[^\w\-]", "_", stem)
    norm = re.sub(r"_+", "_", norm).strip("_").lower()

    if not norm:
        return None

    best: Path | None = None
    best_mtime: float = -1.0

    try:
        for entry in base.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ext:
                continue
            entry_norm = re.sub(r"[^\w\-]", "_", entry.stem)
            entry_norm = re.sub(r"_+", "_", entry_norm).strip("_").lower()
            if entry_norm.endswith(norm):
                mt = entry.stat().st_mtime
                if mt > best_mtime:
                    best = entry
                    best_mtime = mt
    except OSError:
        pass

    if best is not None and str(best.resolve()).startswith(str(base)):
        return best.resolve()

    return None
