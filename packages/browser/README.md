# Browser

The Playwright sidecar. Renders pages the backend's plain HTTP fetch can't, and holds the owner's saved logins so a password never becomes text a model produces. Runs in its own container, internal network only.

---

## Contents

- [Endpoints](#endpoints)
- [What a snapshot carries](#what-a-snapshot-carries)
- [Session and cookie persistence](#session-and-cookie-persistence)
- [CAPTCHA handling](#captcha-handling)
- [Authentication](#authentication)
- [Container](#container)
- [Local development](#local-development)

---

## Endpoints

| Method & path | Does |
|---|---|
| `GET /health` | Liveness check. Unauthenticated; includes the list of saved portal profiles only if a valid token is supplied. |
| `POST /read` | Renders one URL. Reuses a profile's live tab if one is open (stays signed in); otherwise opens a throwaway context, loading saved cookies if a profile is given. |
| `POST /act` | Runs a bounded sequence of steps (max 25) against a live or new session and returns a snapshot. Supports file uploads, dialog handling, and closing the session on completion. |
| `POST /login` | The only endpoint that accepts a password. Signs a profile into a portal; keeps the tab open on success. |
| `GET /artifact` | Fetches a captured download or screenshot by token. |
| `POST /forget` | Deletes a profile's saved cookies and closes any live sessions on it. |
| `DELETE /session/{id}` | Closes one live session. |

---

## What a snapshot carries

Every read returns the page's URL, title, readable text, its links, and its
**images** — read from every frame, not just the top document.

Images are extracted because text extraction alone cannot produce a picture:
`innerText` has never carried one, so an agent asked to put a face on a
presentation window had nothing to work from and either left the window bare or
invented an address that rendered as a hole. Two rules decide what comes back:

- The page's own share image (`og:image` / `twitter:image`) leads, because a
  publisher chose it to represent the story — that beats anything picked by
  measuring.
- Then content images, **largest first**, above `MIN_IMAGE_PX` in rendered
  pixels. On a page with a photograph the photograph is the biggest thing on it,
  and the icons, logos, share buttons and tracking pixels are not.

`data:` URIs are skipped rather than returned: one inlined photo is tens of
kilobytes of base64 through a model's context, and the proxy that ultimately
fetches these speaks only http(s). Results are de-duplicated across frames — an
embedded view often re-serves the parent's hero image.

---

## Session and cookie persistence

Cookies persist per **profile**, not per session — one JSON file per profile, saved after every profile-bound call, not just after login, since a portal can rotate its session cookie mid-visit. A live half also exists in memory: a signed-in tab stays open and gets reused across `/read` and `/act` calls instead of re-authenticating on every request.

---

## CAPTCHA handling

Triggered automatically during login when a captcha image and input field are both detected on the page.

1. **Vision solving** (primary) — sends the cropped captcha image to a vision-capable model. Requires an API key; returns nothing if unset or the request fails.
2. **Arithmetic OCR** (fallback) — local OCR via Tesseract, for the simple `N op M` arithmetic captchas common on smaller portals.

If both fail, login proceeds without filling the captcha field and the failure is recorded in the step log rather than raised as an error — the caller finds out the login likely failed, not why the process crashed.

---

## Authentication

A shared token, checked against every endpoint except `/health` (which still gates the profile list behind it).

---

## Container

Built on the official Playwright Python image, pinned to a specific version. Installs Tesseract for OCR alongside the Python dependencies. Runs as a non-root user. Exposed only on the internal Docker network — never a published host port, per the same CVE-2025-9611 rule that applies to every Playwright surface in this repo.

---

## Local development

No dedicated dev script — the server runs directly:

```bash
python server.py
```

Needs `BROWSER_STATE_DIR` (where per-profile cookies persist), `BROWSER_TOKEN` (the auth token), and the vision-solving API key set in the environment.
