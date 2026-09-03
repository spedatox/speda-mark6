# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Image proxy for the voice canvas.

The canvas puts pictures on the board — a photo on a dossier card, the lead
image on an article cutting — from URLs the agent found while researching. Those
are third-party URLs, and fetching them has to go through here rather than
straight from the client, for two independent reasons:

  PRIVACY. A client that loads `https://target.example/photo.jpg` directly tells
  that server the owner's IP, his user agent, and the moment he looked. For a
  research agent that is the whole problem: the subject of an OSINT board must
  not be handed a log line saying he is being looked into. The fetch belongs on
  the server, where it is one more request from a host that is already making
  them.

  THE CLIENTS CANNOT LOAD REMOTE IMAGES ANYWAY. The desktop renderer ships
  `img-src 'self' data: blob:` (packages/heartbreaker/src/renderer/index.html),
  so a remote `<img src>` is refused before a request is made. Rather than widen
  that — the CSP is doing exactly its job — the client fetches through here with
  its normal X-API-Key and hands the tag a `blob:` URL, which the existing policy
  already allows. No header-less image request ever has to be authenticated,
  which is why this needs no signed URL or query-string key.

Everything here is read-only and returns bytes or an error; nothing is cached to
disk. The guards below are the interesting part.
"""

import ipaddress
import logging
import socket
import urllib.parse

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/media", tags=["media"])

_UA = "Speda-Mark-VI/1.0 (canvas image proxy)"

#: Content types we will hand back. An image proxy that will return anything is
#: an open proxy, and the canvas has no use for a page of HTML.
_ALLOWED = ("image/",)


def _is_public(host: str) -> bool:
    """Whether every address `host` resolves to is publicly routable.

    This is the SSRF guard, and it is deliberately strict: without it the proxy
    is an authenticated request-forger pointed at everything the SERVER can
    reach but the owner cannot — the Docker network, the metadata endpoint on a
    cloud host, other services on the LAN. A hostname is checked by RESOLVING it
    rather than by pattern-matching the string, because `localtest.me` and a
    thousand hostnames like it resolve to 127.0.0.1 while looking public.

    Every resolved address must pass. A name that returns one public and one
    private address is rejected rather than partially trusted — that split is
    the shape a DNS-rebinding attempt takes, not the shape a photo host does.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return False
    return True


@router.get("/proxy")
async def proxy_image(url: str = Query(..., max_length=2048)):
    """Fetch one third-party image and return its bytes.

    The client calls this with its normal X-API-Key and turns the response into
    a blob URL; see the module docstring for why that indirection is the point
    rather than an inconvenience.
    """
    if not settings.canvas_image_proxy:
        raise HTTPException(status_code=404, detail="Image proxy disabled")

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Only http(s) URLs")
    if not _is_public(parsed.hostname):
        raise HTTPException(status_code=400, detail="Host is not publicly routable")

    limit = settings.canvas_image_max_bytes
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.canvas_image_timeout_s, connect=6.0),
            # Redirects are NOT followed: a redirect is how a public hostname
            # becomes a private one after the check above has already passed.
            follow_redirects=False,
            headers={"User-Agent": _UA},
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise HTTPException(status_code=502, detail=f"Upstream {resp.status_code}")
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if not ctype.startswith(_ALLOWED):
                    raise HTTPException(status_code=415, detail=f"Not an image ({ctype or 'unknown'})")

                # Read against the cap as it streams. Trusting Content-Length
                # would let a server that lies about it, or omits it, stream
                # until memory runs out.
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        raise HTTPException(status_code=413, detail="Image too large")
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        logger.info("image_proxy_failed", extra={"url": url, "error": str(exc)})
        raise HTTPException(status_code=502, detail="Could not fetch image") from exc

    return Response(
        content=bytes(body),
        media_type=ctype,
        # The client holds the blob for the life of the window; a short cache
        # keeps a repack or a re-render from re-fetching from the origin.
        headers={"Cache-Control": "private, max-age=600"},
    )
