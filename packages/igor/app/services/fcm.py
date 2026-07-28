"""
FCM HTTP v1 delivery — Igor's channel to the owner's devices.

── Why this file exists ────────────────────────────────────────────────────
`app/skills/notifications.py` shipped as a stub that returned "Push notification
delivery not yet configured." for every call. This is the real implementation
behind it.

── The two API decisions, both forced ──────────────────────────────────────
1. **HTTP v1, not legacy.** The legacy `fcm.googleapis.com/fcm/send` endpoint
   with a server key was decommissioned in June 2024. v1 requires a
   service-account OAuth2 bearer token scoped to
   `https://www.googleapis.com/auth/firebase.messaging`.

2. **`fid`, not `token`.** firebase-messaging 25.1.0 deprecated the whole
   registration-token API on the client (`getToken`/`deleteToken`/`onNewToken`),
   and the Admin SDKs deprecated `Message(token=…)` in favour of
   `Message(fid=…)`. Ultron Wear registers by Firebase Installation ID, so that
   is what goes in the target field.

── Data-only messages ──────────────────────────────────────────────────────
[send_attendance_ask] never sets a `notification` block. A message carrying one
is rendered by the system tray when the app is backgrounded and the app's
`onMessageReceived` is never called — which on the watch would mean no action
buttons, no ledger write, and no way to answer. Data-only + `android.priority
= "high"` guarantees the handler runs, even in Doze.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"

# google-auth's refresh is blocking (it does an RSA sign + an HTTPS token call),
# so it is pushed to a thread rather than stalling the event loop.
_credentials = None
_cred_lock = asyncio.Lock()


class FcmNotConfigured(RuntimeError):
    """Raised when no service-account credentials are available. Callers treat
    this as 'push is off', never as an outage."""


def _load_credentials():
    """Build service-account credentials from the configured JSON file."""
    try:
        from google.oauth2 import service_account
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise FcmNotConfigured("google-auth is not installed") from e

    path = (settings.fcm_credentials_file or "").strip()
    if not path:
        raise FcmNotConfigured("FCM_CREDENTIALS_FILE is not set")
    p = Path(path)
    if not p.exists():
        raise FcmNotConfigured(f"FCM credentials file not found: {p}")

    return service_account.Credentials.from_service_account_file(str(p), scopes=[_SCOPE])


async def _bearer_token() -> str:
    """A valid access token, refreshed on demand. google-auth caches internally
    and only hits the network when the current token is near expiry."""
    global _credentials
    async with _cred_lock:
        if _credentials is None:
            _credentials = await asyncio.to_thread(_load_credentials)

        creds = _credentials
        if not creds.valid:
            from google.auth.transport.requests import Request

            await asyncio.to_thread(creds.refresh, Request())
        return creds.token


def _project_id() -> str:
    project = (settings.fcm_project_id or "").strip()
    if project:
        return project
    # Fall back to the project embedded in the service-account file, so the
    # common case needs one setting instead of two.
    path = (settings.fcm_credentials_file or "").strip()
    if path and Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("project_id"):
            return data["project_id"]
    raise FcmNotConfigured("FCM_PROJECT_ID is not set and not derivable")


async def send_data_message(
    fid: str,
    data: dict[str, str],
    priority: str = "high",
    ttl_seconds: int = 3600,
) -> tuple[bool, str]:
    """
    Deliver a data-only message to one installation.

    Returns `(ok, detail)`. Never raises for an ordinary delivery failure — the
    caller decides whether a dead device is worth surfacing, and a push that
    fails must not take down the turn that triggered it.

    A 404/`UNREGISTERED` means the app was uninstalled or its data cleared; the
    caller should deactivate that device rather than retrying forever.
    """
    try:
        token = await _bearer_token()
        project = _project_id()
    except FcmNotConfigured as e:
        return False, str(e)
    except Exception as e:  # credentials file present but malformed
        logger.error("fcm_credentials_error", extra={"error": str(e)})
        return False, f"FCM credentials unusable: {e}"

    payload = {
        "message": {
            "fid": fid,
            # Every value must be a string — FCM rejects non-string data values.
            "data": {k: str(v) for k, v in data.items()},
            "android": {
                "priority": priority,
                "ttl": f"{ttl_seconds}s",
            },
        }
    }

    url = _ENDPOINT.format(project=project)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=payload,
            )
    except Exception as e:
        logger.warning("fcm_transport_error", extra={"error": str(e)})
        return False, f"FCM transport error: {e}"

    if resp.status_code == 200:
        return True, "delivered"

    body = resp.text[:400]
    logger.warning(
        "fcm_send_failed",
        extra={"status": resp.status_code, "body": body},
    )
    if resp.status_code == 404 or "UNREGISTERED" in body:
        return False, "unregistered"
    return False, f"FCM {resp.status_code}: {body}"


async def send_attendance_ask(fid: str, occurrence: dict) -> tuple[bool, str]:
    """
    Push one "derse girdin mi?" question.

    The `type` key is the discriminator the watch's AttendanceMessagingService
    switches on; the rest is exactly the field set AttendanceAsk.from() expects.
    Keep the two in step — a renamed key here produces a push the watch silently
    ignores, with nothing in either log to say why.
    """
    return await send_data_message(
        fid=fid,
        data={
            "type": "attendance_ask",
            "slot_id": occurrence["slot_id"],
            "course_code": occurrence.get("course_code", ""),
            "course_name": occurrence.get("course_name", ""),
            "date": occurrence["date"],
            "time": occurrence.get("time", ""),
            "room": occurrence.get("room", ""),
        },
        # One hour: past that the question is stale and the watch's local
        # fallback will already have asked.
        ttl_seconds=3600,
    )
