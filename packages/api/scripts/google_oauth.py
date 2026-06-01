"""
One-time Google OAuth flow to obtain a refresh token for SPEDA's Google Workspace MCP.

Run once:
    python scripts/google_oauth.py

Then copy the printed GOOGLE_REFRESH_TOKEN value into packages/api/.env.

Requirements:
- Google Cloud project with Gmail, Calendar, Drive, Chat, People APIs enabled
- OAuth 2.0 credentials (Desktop app type) created in Cloud Console
- Your Google account added as a test user on the OAuth consent screen

No extra packages needed — uses only httpx (already in SPEDA's deps).
"""

import asyncio
import urllib.parse
import webbrowser

import httpx

# ── OAuth scopes for all 5 Google Workspace MCP servers ─────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/contacts.readonly",
]


def build_auth_url(client_id: str) -> str:
    params = {
        "client_id":     client_id,
        "redirect_uri":  "http://localhost",
        "response_type": "code",
        "scope":         " ".join(SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",   # force refresh_token even if already authed
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


async def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "code":          code,
                "grant_type":    "authorization_code",
                "redirect_uri":  "http://localhost",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()


async def main() -> None:
    print("=" * 60)
    print("  SPEDA — Google Workspace OAuth setup")
    print("=" * 60)
    print()
    print("Paste your credentials from Google Cloud Console:")
    print("  (APIs & Services → Credentials → OAuth 2.0 Client IDs)")
    print()

    client_id     = input("Client ID:     ").strip()
    client_secret = input("Client Secret: ").strip()

    if not client_id or not client_secret:
        print("\nError: both Client ID and Client Secret are required.")
        return

    auth_url = build_auth_url(client_id)
    print("\nOpening browser for Google sign-in...")
    print(f"\nIf the browser doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("After signing in, Google will redirect to http://localhost/...")
    print("The page will fail to load — that's expected.")
    print("Copy the full URL from the browser address bar and paste it here.\n")

    redirect_url = input("Redirect URL (or just the 'code' param value): ").strip()

    # Accept either the full URL or just the code value
    if redirect_url.startswith("http"):
        parsed = urllib.parse.urlparse(redirect_url)
        code = urllib.parse.parse_qs(parsed.query).get("code", [""])[0]
    else:
        code = redirect_url

    if not code:
        print("\nError: could not extract authorization code from the URL.")
        return

    print("\nExchanging code for tokens...")
    try:
        tokens = await exchange_code(client_id, client_secret, code)
    except httpx.HTTPStatusError as e:
        print(f"\nError: {e.response.status_code} — {e.response.text}")
        return

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("\nError: no refresh_token in response.")
        print(f"Full response: {tokens}")
        print("\nTip: make sure you selected 'prompt=consent' and the app is in test mode")
        print("with your account added as a test user.")
        return

    print("\n" + "=" * 60)
    print("  SUCCESS — add these to packages/api/.env:")
    print("=" * 60)
    print(f"\nGOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={refresh_token}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
