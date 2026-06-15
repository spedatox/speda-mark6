"""
Authentication primitives — stdlib only.

Deliberately no third-party crypto/JWT dependency. For a public repo whose auth
gates an internet-facing server, every added dependency is supply-chain surface;
Python's stdlib gives us everything needed:

  - Passwords  : hashlib.scrypt (memory-hard KDF) + hmac.compare_digest.
  - Tokens     : HMAC-SHA256-signed JWT (HS256), alg pinned, signature compared
                 in constant time, expiry enforced.

The owner's credential is NOT stored in the database — the username and the
password HASH live in the environment (OWNER_USERNAME / OWNER_PASSWORD_HASH),
so a DB compromise never yields the login. Generate the hash with
scripts/hash_password.py.
"""

import base64
import hashlib
import hmac
import json
import os
import time

# scrypt parameters. N=2**15 is comfortably above interactive-login cost on a
# modern server while staying well under a second. Encoded into the hash string
# so the cost can be raised later without breaking existing hashes.
_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16
# scrypt needs maxmem >= ~128 * N * r * p bytes; the default (32 MiB) is too low
# for N=2**15. Give it headroom.
_SCRYPT_MAXMEM = 128 * _SCRYPT_N * _SCRYPT_R * _SCRYPT_P * 2


def _b64e(raw: bytes) -> str:
    """URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


# ── Passwords ──────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password with scrypt. Returns a self-describing string:
    ``scrypt$N$r$p$<salt_b64>$<hash_b64>``."""
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN, maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify of a password against a stored scrypt hash.
    Returns False (never raises) on any malformed input."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = _b64d(salt_b64)
        expected = _b64d(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n), r=int(r), p=int(p),
            dklen=len(expected), maxmem=_SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ── Tokens (HS256 JWT) ─────────────────────────────────────────────────────

_HEADER = {"alg": "HS256", "typ": "JWT"}


def _sign(secret: str, signing_input: bytes) -> str:
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return _b64e(sig)


def create_token(secret: str, subject: str, ttl_seconds: int) -> tuple[str, int]:
    """Mint an HS256 JWT for `subject`. Returns (token, exp_epoch_seconds)."""
    now = int(time.time())
    exp = now + int(ttl_seconds)
    payload = {"sub": subject, "iat": now, "exp": exp}
    header_b64 = _b64e(json.dumps(_HEADER, separators=(",", ":")).encode())
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    return f"{header_b64}.{payload_b64}.{_sign(secret, signing_input)}", exp


def verify_token(secret: str, token: str) -> dict | None:
    """Verify an HS256 JWT. Returns the payload dict, or None if the token is
    malformed, unsigned-as-expected, tampered, the wrong algorithm, or expired.

    Hardening notes:
      - The algorithm is pinned to HS256 from the trusted header constant; a
        token claiming alg=none or RS256 is rejected (no alg-confusion).
      - The signature is recomputed and compared in constant time.
      - exp is enforced. An empty secret disables verification (returns None),
        so a misconfigured server fails closed, never open.
    """
    if not secret:
        return None
    try:
        header_b64, payload_b64, sig = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected = _sign(secret, signing_input)
        if not hmac.compare_digest(sig, expected):
            return None
        header = json.loads(_b64d(header_b64))
        if header.get("alg") != "HS256":
            return None
        payload = json.loads(_b64d(payload_b64))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def constant_time_equals(a: str, b: str) -> bool:
    """Constant-time string compare (for API-key / secret checks)."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
