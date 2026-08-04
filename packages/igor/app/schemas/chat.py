import re

from pydantic import BaseModel, field_validator

# A drive-letter path ("C:\…", "D:/…") or a UNC share ("\\server\share"). Both
# are meaningless to the server-side peer and, worse, do not fail on POSIX —
# see ChatRequest._cwd_must_be_posix for what that cost.
_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


class ImageAttachment(BaseModel):
    media_type: str   # image/jpeg | image/png | image/gif | image/webp
    data: str         # base64-encoded image bytes (no data: URI prefix)


class DocumentAttachment(BaseModel):
    """A non-image file upload (PDF, DOCX, XLSX, PPTX, CSV, TXT, code, …). The
    backend extracts its text server-side and embeds it in the user turn as a
    text block, so it reaches the model identically on every provider."""
    name: str         # original filename, e.g. "q3_report.pdf"
    media_type: str   # MIME type as reported by the browser (may be "")
    data: str         # base64-encoded file bytes (no data: URI prefix)
    size: int = 0     # byte size, for the display chip


class ClientLocation(BaseModel):
    """The device's current position, sent only when the owner has enabled
    location sharing on that client. Never persisted — see the router, which
    stamps it onto the LIVE turn only (like the message timestamp), so it reaches
    the model this turn without churning the cached prompt prefix or the DB."""
    lat: float
    lng: float
    accuracy_m: float | None = None   # horizontal accuracy radius, metres
    place: str | None = None          # reverse-geocoded label, e.g. "Kadıköy, İstanbul"


class ClientContext(BaseModel):
    """Ambient facts about the client the owner is speaking from — which device,
    OS and app, and (opt-in) where. Lets SPEDA answer platform/location-aware
    questions ("what's near me", "is this the phone or the desktop") without a
    tool round-trip. Assembled fresh by the client each turn; optional throughout."""
    platform: str | None = None       # "android" | "desktop" | "web" | …
    device: str | None = None         # "Google Pixel 7"
    os_version: str | None = None     # "Android 14"
    app_version: str | None = None    # client build, e.g. "0.1.0-m0"
    locale: str | None = None         # BCP-47, e.g. "en-US"
    location: ClientLocation | None = None
    # The reply will be SPOKEN, not read. A per-turn fact, not a session one —
    # voice mode is toggled mid-conversation — which is exactly why it rides
    # here rather than in the cached system prefix.
    voice: bool = False


class ChatRequest(BaseModel):
    message: str
    session_id: int | None = None
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    attachments: list[ImageAttachment] = []
    documents: list[DocumentAttachment] = []

    # Ambient client/platform/location context for THIS turn. Stamped onto the
    # live user message only (not stored), so SPEDA is platform- and location-aware
    # without the churn a clock/location in the cached system prefix would cause.
    client_context: ClientContext | None = None

    # Regenerate / edit support. The DB is the source of truth for history, so a
    # client editing or regenerating must tell the backend to truncate first —
    # otherwise the model is re-fed its own prior answer and can't see the turn
    # afresh. Both are position-based (count of leading messages to keep),
    # which maps 1:1 onto the stored user/assistant rows in created_at order.
    keep_messages: int | None = None  # delete all but the first N messages, then proceed
    regenerate: bool = False          # re-run on existing history; do NOT add a new user message

    # Working directory for an external-backend agent (the Forge / Optimus). It
    # lands in context.extra["cwd"] → the peer's chat_request.cwd → the Cell
    # workspace + Graphify root. Ignored by in-process agents. None = the peer's
    # own default workspace.
    cwd: str | None = None

    @field_validator("cwd")
    @classmethod
    def _cwd_must_be_posix(cls, value: str | None) -> str | None:
        """Refuse a Windows path — the peer is always server-side POSIX.

        On 2026-08-04 the owner picked a folder in the desktop file dialog and
        asked Optimus to build a site "here". The literal string
        `C:\\Users\\AREL TARIM\\Downloads\\Yeni klasör` was forwarded to a Linux
        process, where both `\\` and `:` are legal filename characters — so
        nothing errored. mkdir created ONE directory with that entire path as
        its name under /opt/forge-mk1, the site built cleanly inside it, and the
        owner was told it was done. Silent success on the wrong machine is the
        worst available outcome, so the path is rejected here at the edge where
        every client (desktop, SPEDA GO, Telegram) goes through it.
        """
        if value is None:
            return None
        path = value.strip()
        if not path:
            return None
        if _WINDOWS_PATH.match(path) or "\\" in path:
            raise ValueError(
                "That looks like a Windows path from your own PC "
                f"({path!r}), but Optimus runs on the server — it has no access "
                "to your machine's filesystem. Give it an absolute POSIX path on "
                "the server (e.g. /opt/forge-mk1/workspaces/my-site), or leave "
                "the directory unset to use the peer's own workspace and collect "
                "the result from there."
            )
        return path


class ChatResponse(BaseModel):
    session_id: int
    request_id: str
