"""
Security response headers + server-identity scrubbing.

Applied to every response. These are cheap, defence-in-depth hardening for an
internet-facing API:

  - HSTS                       force HTTPS for a year (safe — Caddy terminates TLS).
  - X-Content-Type-Options     stop MIME sniffing.
  - X-Frame-Options / frame-ancestors  block clickjacking (this is an API; deny framing).
  - Referrer-Policy            never leak URLs to third parties.
  - Content-Security-Policy    default-src 'none' — a JSON API needs no resources;
                               kills any reflected-content execution surface (XSS).
  - Permissions-Policy         disable powerful browser features wholesale.
  - Server                     overwrite uvicorn's banner so we don't advertise the stack.
"""

from starlette.middleware.base import BaseHTTPMiddleware

_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Server": "SPEDA",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for key, value in _HEADERS.items():
            response.headers[key] = value
        return response
