"""Hisar — the owner's cloud filesystem, reachable by their agents.

Hisar is a system the owner designed and runs: a web desktop at
hisar.spedatox.systems over a vault of real folders (Documents, Media,
Projects, Desktop, plus SPEDA/ and Forge/). It is not SPEDA's storage — it is
the owner's, and the agents work in it alongside them.

Two boundaries, enforced by Hisar itself rather than here (server/auth.py):

- **Read is wide.** An agent may list and download anywhere in the vault. It
  has to be: an agent that can only write cannot find the report it filed last
  week, check whether a folder exists, or read a document the owner asked it to
  work from.
- **Write is narrow.** `/SPEDA` and `/Forge` only, via the deposit door, which
  creates parents and never overwrites. Delete and rename are owner-only and
  have no path from here at all — an agent cannot destroy the owner's files
  even by mistake.

Deposits are the durable half of file handling. `save_file` writes to
/tmp/speda_outputs for a download card in the chat, which is right for
"give me this now" and wrong for anything the owner will want next month —
that file is only reachable from the conversation that made it.
"""

import logging

import httpx

from app.config import settings
from app.core.context import AgentContext
from app.skills.base import Skill

logger = logging.getLogger(__name__)

_TIMEOUT = 20.0
_MAX_TEXT_CHARS = 40_000     # a read is context; a whole book is not
_MAX_ENTRIES = 200           # a listing the model can actually reason over


def _headers() -> dict:
    return {"X-Hisar-Token": settings.hisar_machine_token}


def _fmt_size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return ""


class HisarSkill(Skill):
    name = "hisar"
    deferred = True
    search_keywords = ("hisar vault cloud drive files folder storage desktop "
                       "documents projects media deposit save browse list")
    read_only = False           # `deposit` writes
    requires_network = True

    description = (
        "Browses and files documents in Hisar, the owner's own cloud filesystem "
        "— a web desktop over folders like Documents, Projects, Media and "
        "Desktop, which they use and their agents share. Use `list` to see what "
        "is in a folder, `read` to pull a text document into context, and "
        "`deposit` to file something durably where the owner can find it later "
        "in their file manager. Deposit is the right home for anything they "
        "will want again — a report, a briefing, a generated document — whereas "
        "save_file is for handing a file over in the conversation right now and "
        "leaves nothing they can browse to afterwards. You may read anywhere in "
        "the vault but write ONLY under /SPEDA and /Forge, and you cannot delete "
        "or rename anything, so treat the owner's own folders as reference "
        "material. Returns a directory listing, the document's text, or the "
        "path a deposit landed at."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "deposit"],
                "description": (
                    "list: what is in a folder. read: the text of one document. "
                    "deposit: file new content under /SPEDA or /Forge."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "Vault path, always absolute from the vault root and using "
                    "forward slashes: '/', '/Documents', '/Projects/site'. For "
                    "`read` this is the file. For `deposit` it is the FOLDER to "
                    "file into (default '/SPEDA'). Never a path from the "
                    "server's disk — the vault root is not '/opt/...'."
                ),
            },
            "filename": {
                "type": "string",
                "description": "deposit only: the name to file it under, e.g. 'q3-briefing.md'.",
            },
            "content": {
                "type": "string",
                "description": "deposit only: the text to write.",
            },
        },
        "required": ["action"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        if not settings.hisar_machine_token:
            return ("Hisar is not configured on this deployment "
                    "(no machine token), so the vault cannot be reached. "
                    "Tell the owner rather than retrying.")

        action = str(args.get("action") or "").strip().lower()
        path = (args.get("path") or "").strip()

        try:
            if action == "list":
                return await self._list(path or "/")
            if action == "read":
                if not path:
                    return "Refused: `read` needs the path of a file in the vault."
                return await self._read(path)
            if action == "deposit":
                return await self._deposit(
                    folder=path or "/SPEDA",
                    filename=(args.get("filename") or "").strip(),
                    content=args.get("content") or "",
                )
            return f"Unknown action {action!r}. Use list, read or deposit."
        except httpx.HTTPStatusError as e:
            return self._explain(e)
        except httpx.RequestError as e:
            logger.warning("hisar_unreachable", extra={
                "request_id": context.request_id, "error": str(e)})
            return ("Hisar did not answer, so the vault is unreachable right "
                    "now. Say so rather than pretending the file was filed.")

    # ── actions ─────────────────────────────────────────────────────────────

    async def entries(self, path: str) -> list[dict]:
        """The raw listing, as data.

        Public and structured because more than the model consumes it: the
        desktop's vault picker needs directories, and recovering those by
        parsing the rendered text below is how a picker ends up showing a blank
        row named "" — the header line `/` also ends in a slash. Formatting
        belongs at the edge; everything upstream of it works on the data.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{settings.hisar_base_url}/files/list",
                                 params={"path": path}, headers=_headers())
            r.raise_for_status()
            body = r.json()
        return body.get("entries") or []

    @staticmethod
    def is_dir(entry: dict) -> bool:
        """Hisar has spelled this three ways across versions; ask once, here."""
        return bool(entry.get("is_dir") or entry.get("kind") == "dir"
                    or entry.get("type") == "dir")

    async def _list(self, path: str) -> str:
        entries = await self.entries(path)
        if not entries:
            return f"{path} is empty."

        lines = []
        for e in entries[:_MAX_ENTRIES]:
            name = e.get("name", "?")
            if self.is_dir(e):
                lines.append(f"  {name}/")
            else:
                size = _fmt_size(e.get("size"))
                lines.append(f"  {name}{f'  ({size})' if size else ''}")

        more = ""
        if len(entries) > _MAX_ENTRIES:
            more = f"\n  … and {len(entries) - _MAX_ENTRIES} more"
        return f"{path}\n" + "\n".join(lines) + more

    async def _read(self, path: str) -> str:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{settings.hisar_base_url}/files/download",
                                 params={"path": path, "inline": True},
                                 headers=_headers())
            r.raise_for_status()
            raw = r.content

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return (f"{path} is not a text file ({_fmt_size(len(raw))}), so there "
                    "is nothing to read into context. Reference it by path "
                    "instead of guessing at its contents.")

        if len(text) > _MAX_TEXT_CHARS:
            return (f"{path} (first {_MAX_TEXT_CHARS} of {len(text)} chars)\n\n"
                    + text[:_MAX_TEXT_CHARS])
        return f"{path}\n\n{text}"

    async def _deposit(self, folder: str, filename: str, content: str) -> str:
        if not filename:
            return "Refused: a deposit needs a filename, e.g. 'q3-briefing.md'."
        if not content:
            return "Refused: a deposit with no content would file an empty file."

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{settings.hisar_base_url}/deposit",
                headers=_headers(),
                data={"folder": folder, "filename": filename},
                files={"file": (filename, content.encode("utf-8"),
                                "text/plain; charset=utf-8")},
            )
            r.raise_for_status()
            body = r.json()

        # Hisar never overwrites, so the name it actually used may differ from
        # the one asked for. Report what landed, not what was requested.
        landed = body.get("path") or body.get("saved") or f"{folder}/{filename}"
        return (f"Filed at {landed} in Hisar. The owner can open it from their "
                "file manager; it is not tied to this conversation.")

    # ── failure, in terms the model can act on ──────────────────────────────

    def _explain(self, e: httpx.HTTPStatusError) -> str:
        code = e.response.status_code
        if code == 403:
            return ("Refused by Hisar: agents may write only under /SPEDA and "
                    "/Forge, and may not delete or rename anything. Deposit "
                    "under /SPEDA instead, or ask the owner to move it.")
        if code == 401:
            return ("Hisar rejected the machine token. This is a deployment "
                    "problem, not something to retry — tell the owner.")
        if code == 404:
            return "No such path in the vault. Use `list` to see what is there."
        if code == 409:
            return "Hisar reports a conflict — that name is taken and it never overwrites."
        return f"Hisar returned HTTP {code}: {e.response.text[:200]}"
