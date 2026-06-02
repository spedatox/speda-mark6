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
    """Resolve a filename to a path inside temp_outputs_dir, blocking traversal."""
    base = Path(settings.temp_outputs_dir).resolve()
    target = (base / os.path.basename(name)).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        return None
    return target
