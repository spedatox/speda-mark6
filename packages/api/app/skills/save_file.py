import logging
import os
import re
from pathlib import Path

from app.config import settings
from app.core.context import AgentContext
from app.core.files import register_file
from app.skills.base import Skill

logger = logging.getLogger(__name__)

# Hard ceiling on a single saved file — these are hand-written text/code files,
# not datasets. Keeps a runaway generation from filling the outputs dir.
_MAX_BYTES = 5 * 1024 * 1024

# Extension used when the model gives a filename with no extension at all.
_DEFAULT_EXT = "txt"


def _clean_filename(raw: str) -> str:
    """
    Reduce an arbitrary model-supplied name to a safe, flat filename that keeps
    its extension. Strips any directory components (traversal-safe) and any
    character that isn't word/dot/dash, collapses repeats, guarantees a stem and
    an extension.
    """
    name = os.path.basename((raw or "").strip().replace("\\", "/").split("/")[-1])
    name = re.sub(r"[^\w.\-]", "_", name)
    name = re.sub(r"_+", "_", name).strip("._")
    if not name:
        name = "file"
    stem, dot, ext = name.rpartition(".")
    if not dot:                      # no extension at all → default
        return f"{name}.{_DEFAULT_EXT}"
    stem = stem.strip("._")
    if not stem:                     # leading-dot name like ".env" → treat whole as stem
        return f"{name}.{_DEFAULT_EXT}"
    return f"{stem}.{ext.lower()}"


def _unique_path(name: str) -> Path:
    """A path in temp_outputs_dir that doesn't collide — keeps the clean name in
    the common case, appends -1/-2/... only if that exact name already exists."""
    out_dir = Path(settings.temp_outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / name
    if not dest.exists():
        return dest
    stem, _, ext = name.rpartition(".")
    i = 1
    while True:
        cand = out_dir / f"{stem}-{i}.{ext}"
        if not cand.exists():
            return cand
        i += 1


class SaveFileSkill(Skill):
    name = "save_file"
    description = (
        "Writes text content you generate to a real downloadable file and delivers it "
        "to the user as a download card in the chat. THIS is how you hand over any "
        "code or text file the user wants to save, open, or run — an HTML page or "
        "landing page (.html), a script (.py, .js, .sh), styles (.css), config or data "
        "(.json, .yaml, .csv, .xml, .env, .toml), Markdown (.md), or plain text (.txt). "
        "Use it whenever the user says things like 'as an HTML file', 'give me the .py', "
        "'create a file', 'so I can download/save/run it', or 'export as'. Do NOT use it "
        "for PDF, Word, or PowerPoint — those are generate_document — and do NOT use it "
        "for inline charts/diagrams the app renders (those stay as chart/svg fenced "
        "blocks). Pass the desired filename WITH its extension plus the full file "
        "content; it returns confirmation that the download is ready (don't also paste "
        "the code or a path)."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "Desired filename including extension, e.g. 'index.html', "
                    "'app.py', 'styles.css', 'data.json'. The extension decides the "
                    "file type shown to the user."
                ),
            },
            "content": {
                "type": "string",
                "description": "The complete text content of the file, exactly as it should be saved.",
            },
            "title": {
                "type": "string",
                "description": "Optional friendly label for the download card. Defaults to the filename.",
            },
        },
        "required": ["filename", "content"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        filename = _clean_filename(args.get("filename", ""))
        content = args.get("content", "")
        title = (args.get("title") or "").strip() or filename

        if not content:
            return "No content provided — nothing to save."

        data = content.encode("utf-8")
        if len(data) > _MAX_BYTES:
            return (
                f"File is too large ({len(data)} bytes; limit {_MAX_BYTES}). "
                f"Split it or generate it via run_command in the sandbox instead."
            )

        try:
            dest = _unique_path(filename)
            dest.write_bytes(data)
            meta = register_file(context, str(dest), title=title)
        except Exception as e:  # noqa: BLE001
            logger.error(
                "save_file_error",
                extra={"request_id": context.request_id, "filename": filename, "error": str(e)},
            )
            return f"Couldn't save the file: {e}"

        logger.info(
            "save_file",
            extra={
                "request_id": context.request_id,
                "file_name": meta["name"],
                "size": meta["size"],
            },
        )
        return (
            f"Saved '{meta['title']}' ({meta['kind']}, {meta['size']} bytes) and delivered "
            f"it to the user as a downloadable file. Just tell them it's ready - do NOT "
            f"paste the file contents, a path, or a link."
        )
