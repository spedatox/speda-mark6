# Telegram Channel Architecture — Mark VI

**Status:** Implemented — T1 (outbound) + T2 (inbound chat) live; T3 partial
(inbound photo/document attachments wired; inbound voice is a graceful stub until
Whisper STT lands; live edit-streaming and TTS voice replies remain parked).
**Date:** 2026-07-06 (designed) · 2026-07-07 (implemented)

Telegram becomes a **first-class conversation channel** and the **primary
notification surface** for Mark VI. Three capabilities, one architecture:

1. **Chat** — the owner talks to any agent from Telegram exactly as from the
   desktop app. Same orchestrator, same sessions, same memory.
2. **Delivery skill** — any agent can deliberately push a message or a
   generated file (PPTX, PDF, report) to the owner's Telegram mid-turn.
3. **Notifications** — `output_mode="push"` results and proactive alerts
   arrive on Telegram, **from the bot of the agent that produced them**.

**One bot per agent.** Sentinel's budget alert comes from @SentinelBot;
NightCrawler's OSINT hit comes from @NightCrawlerBot. The owner's Telegram
contact list becomes the agent roster.

---

## 1. Design Principles (inherited, non-negotiable)

- **The orchestrator does not change.** It already yields a transport-agnostic
  `SSEEvent` stream and lets the consumer decide what to do with it
  (CLAUDE.md, Output Modes). Telegram is simply a *third consumer* of that
  stream, next to the SSE router and the WS handler. No new `output_mode`,
  no new `triggered_by` value — a Telegram message from the owner is
  `triggered_by="user"`, `output_mode="respond"`.
- **Zero logic in routers.** The webhook router validates transport auth,
  acks Telegram, and hands the raw update to the gateway service. Nothing else.
- **Identity lives in profiles** (Rule 10). Which agents have a Telegram
  presence is declared on the profile. Bot **tokens are secrets** and live in
  `.env` / `config.py`, keyed by `agent_id` — same split as model policy
  (profile) vs API keys (config).
- **No internal scheduling.** n8n still triggers everything automated;
  Telegram is only ever a *delivery* and *ingress* surface.
- **Optimus stays external.** Its bot's webhook lands on this backend, but
  the turn is proxied through the existing `core/external_proxy.py` path —
  identical to how `/chat/optimus` already behaves.

---

## 2. Component Map

```
app/telegram/                        # new package (Tier: infrastructure service)
├── client.py       # TelegramBot — thin per-token Bot API wrapper
├── registry.py     # TelegramBotRegistry — agent_id → TelegramBot, owner linkage
├── gateway.py      # inbound update → AgentContext → orchestrator.run() → renderer
├── renderer.py     # SSEEvent stream → Telegram messages (chunking, typing, files)
└── linking.py      # one-time /start deep-link pairing per bot

app/routers/telegram.py              # POST /telegram/webhook/{agent_id}
                                     # GET  /telegram/link/{agent_id}

app/skills/telegram.py               # Tier-1 skill: send_telegram_message,
                                     #                send_telegram_file
```

The existing `app/services/telegram.py` (single-bot `TelegramClient`) is
**absorbed** into `app/telegram/client.py` + `registry.py`. The
`app.state.telegram` singleton is replaced by `app.state.telegram_bots`
(the registry). The trigger router's hardcoded `telegram.send_message(...)`
call is replaced by a registry call that resolves the firing agent's own bot.

### 2.1 `TelegramBot` (client.py)

One instance per configured token. Owns nothing about identity — it is a
dumb, reliable pipe:

- Persistent shared `httpx.AsyncClient` (today's client opens a new
  connection per call — fix that here).
- `send_message(text)` with Markdown→Telegram-HTML conversion and plain-text
  fallback; automatic chunking at 4096 chars, split on paragraph boundaries.
- `send_document(path, caption)` / `send_photo(...)` — multipart upload,
  50 MB bot API ceiling enforced with a clear error.
- `send_chat_action("typing")` for the live-turn indicator.
- Honors `429 retry_after` with a single bounded retry; per-chat sends are
  sequential (Telegram allows ~1 msg/sec per chat).
- `set_webhook(url, secret_token)` / `delete_webhook()` / `get_updates(...)`.

### 2.2 `TelegramBotRegistry` (registry.py)

The only entity that knows which bot belongs to which agent:

- Built in the lifespan handler from config tokens: `{agent_id: TelegramBot}`.
- `bot_for(agent_id) -> TelegramBot` with a **fallback chain**: the agent's
  own bot → SPEDA's bot (message prefixed `[Sentinel]` so attribution
  survives) → `None` (caller logs and stores the notification row instead).
  A missing token degrades a single agent, never the channel.
- Owner linkage: the owner's private-chat id with a bot equals their Telegram
  user id, so it is **the same number for every bot**. It is captured once
  (existing deep-link flow) and stored in `runtime_state` as
  `telegram_owner_id`. Per-bot, the registry tracks a `started` flag —
  Telegram forbids a bot from messaging a user who never tapped Start on
  *that* bot, so each unstarted bot falls back to SPEDA's until the owner
  pairs it.
- Startup mode switch (config `telegram_mode`):
  - `webhook` — production (Contabo, public HTTPS): calls `setWebhook` per
    bot with `{telegram_webhook_base}/telegram/webhook/{agent_id}` and the
    shared `secret_token`.
  - `polling` — dev (no public URL): spawns one `getUpdates` long-poll
    asyncio task per bot in the lifespan handler, feeding the same gateway.
  - `off` — channel disabled; skill and push routing report unconfigured.

### 2.3 Gateway (gateway.py) — the only place with logic

`handle_update(agent_id, update)` runs as a background task (the webhook
router acks `200` immediately — Telegram retries aggressively on slow
responses, and Rule 7 forbids blocking work in request handlers anyway):

1. **Dedupe** by `update_id` (last-seen watermark per bot, in
   `runtime_state`) — webhook retries and polling overlaps must not double-run
   a turn.
2. **Authorize**: sender id must equal `telegram_owner_id`
   (constant-time compare). Anyone else is silently dropped and logged.
   Single-user system — there is no guest path.
3. **Route commands**: `/start <nonce>` → linking flow; `/new` → close the
   sticky session, start fresh. Everything else is a normal turn.
4. **Resolve the session**: sticky per `(user_id, agent_id, channel="telegram")`
   — reuse the open Telegram session for that agent or create one via
   `SessionManager`. Telegram history and app history never mix mid-session,
   but both live in the same tables and feed the same memory extraction.
5. **Build `AgentContext`** exactly as `routers/chat.py` does:
   `triggered_by="user"`, `output_mode="respond"`,
   `model=profile.allocate_model("user")`, task-owned `AsyncSessionLocal`
   (the pattern `_run_trigger` already uses), owner timezone from
   runtime state.
6. **Inbound media** (phase T3): voice note → download → existing STT skill →
   text; photo/document → existing `services/attachments.py` path.
7. **Consume** `orchestrator.run(context)` through the renderer.
8. **Optimus**: if `profile.external_backend` and the peer is connected, the
   turn goes through `core/external_proxy.py` — same branch `/chat/{agent_id}`
   already takes. The gateway does not special-case Optimus beyond that.

### 2.4 Renderer (renderer.py) — SSEEvent → Telegram

A peer of the SSE serializer and the WS `to_json()` path:

| SSEEvent | Telegram behaviour (v1) |
|---|---|
| `START` | `sendChatAction("typing")`, re-sent every ~5 s while the turn runs |
| `CHUNK` | Buffered. **No live message-editing in v1** — final text sent at `DONE`, chunked at 4096. (Live `editMessageText` streaming is a v2 option; it fights Telegram's edit rate limits for marginal UX gain on mobile.) |
| `TOOL` / `TOOL_RESULT` | Ignored in v1. v2: one status message edited in place ("🔧 searching arXiv…"). |
| `FILE` | `sendDocument` (or `sendPhoto` for images) straight from `temp_outputs_dir`, caption = filename. Delivered *before* the final text so the file lands with its explanation following it. |
| `DONE` | Flush buffered text (Markdown→HTML, plain fallback). |
| `ERROR` | Short apologetic message with the `request_id` for tracing. |

### 2.5 Delivery skill (skills/telegram.py) — Tier 1

Registered in `CapabilityRegistry` like every other skill; **not** read-only.
Two tools, each with the mandatory 3–4 sentence description (Rule 11):

- **`send_telegram_message`** `(text, silent: bool = false)` — sends from
  **the calling agent's own bot**, resolved from `context.agent_id` via the
  registry. For deliberate mid-turn pushes ("also ping me on Telegram when
  the export finishes"). Not for `output_mode="silent"` results.
- **`send_telegram_file`** `(path, caption?)` — path must resolve inside
  `temp_outputs_dir` (reuse the `save_file` jail check); uploads via
  `sendDocument`. This is the "generate a deck, send it to my phone" path:
  `generate_document` → `send_telegram_file`, two tool calls, no new plumbing.

Both tools are in every profile's allowlist. The skill degrades to a clear
"Telegram not configured/linked" tool result — never an exception.

### 2.6 Notification routing (`output_mode="push"`)

`routers/trigger.py::_run_trigger` currently hardcodes the singleton client.
It changes to:

```
push → registry.bot_for(context.agent_id).send_message(final_text)
       (fallback chain applies; total failure → Notification row in DB,
        surfaced next time the app opens)
```

So the *identity of the sender bot is derived from the AgentContext*, never
passed by n8n. The n8n payload may optionally carry `"channel": "telegram" |
"flutter"` for when FCM lands; default is Telegram. `send_push_notification`
(Flutter/FCM skill) remains a separate, parallel skill — Telegram does not
replace it in the registry, it replaces it as the *default* push transport.

---

## 3. Transport & Security

New row in the channel table (CLAUDE.md "Transport Channels"):

| Channel | Protocol | Used For |
|---|---|---|
| `POST /telegram/webhook/{agent_id}` | HTTPS webhook (or dev long-poll) | Owner ↔ agent chat + inbound media from Telegram |

- **Webhook auth**: the path is exempt from `X-API-Key` in `AuthMiddleware`
  (same exemption class as `/oauth/google/callback`) and is instead protected
  by Telegram's `X-Telegram-Bot-Api-Secret-Token` header — a random secret
  set at `setWebhook` time, validated with a constant-time compare. Wrong or
  missing secret → 403, body never parsed.
- **Sender allowlist**: even with a valid webhook secret, only
  `telegram_owner_id` is processed (defense in depth — bot usernames are
  public and anyone can message a bot).
- **Token blast radius**: one token per agent means a leaked token burns one
  bot, not the fleet. Tokens only ever appear in `.env`.
- **No inbound content trust**: Telegram message text enters the same path as
  app chat input — it is user content in `conversation_history`, never
  interpolated into the system prompt.
- Polling mode needs no inbound port at all (dev default).

### Config additions (`config.py`)

```python
telegram_mode: str = "off"              # off | polling | webhook
telegram_webhook_base: str = ""         # https://speda.example.com (webhook mode)
telegram_webhook_secret: str = ""       # secret_token for all bots' webhooks
telegram_bot_token_speda: str = ""      # keep telegram_bot_token as legacy alias
telegram_bot_token_sentinel: str = ""
telegram_bot_token_nightcrawler: str = ""
telegram_bot_token_ultron: str = ""
telegram_bot_token_centurion: str = ""
telegram_bot_token_atomix: str = ""
telegram_bot_token_optimus: str = ""
```

### Profile addition (`profiles/base.py`)

```python
telegram_enabled: bool = True   # profile opts out of a Telegram presence
                                # (e.g. the war-room alias profile sets False)
```

Nothing else on the profile — the bot's *voice* is already the agent's system
prompt; Telegram adds no second identity layer.

### Data model

- `Session.channel: str = "app"` — new column, values `"app" | "telegram"`.
  Scopes the sticky-session lookup and lets the desktop UI badge
  Telegram-originated sessions. No other schema change: messages, tool
  calls, memory extraction all work unchanged because a Telegram turn *is*
  a normal orchestrator turn.
- `runtime_state`: `telegram_owner_id`, per-bot `started` flags, per-bot
  `update_id` watermarks.

---

## 4. Linking Flow (one-time, per bot)

1. Desktop app (or SPEDA in chat) surfaces `GET /telegram/link/{agent_id}` →
   `https://t.me/<botusername>?start=<nonce>`.
2. Owner taps Start. The `/start <nonce>` update arrives via the normal
   ingress (webhook or poll) — no separate capture loop needed once ingress
   exists; the existing `capture_chat_id` polling hack is retired.
3. Gateway validates the nonce, stores `telegram_owner_id` (first link) and
   the per-bot `started` flag, and the bot replies with its own greeting.
4. Owner repeats per bot — or just links SPEDA and lets other agents ride the
   fallback chain until they're paired.

---

## 5. Build Phases

| Phase | Scope | Delivers | Status |
|---|---|---|---|
| **T1 — Outbound** | `client.py`, `registry.py`, config, linking, skill, trigger-router rewire | Per-agent-bot notifications + "send this file to Telegram" skill. | ✅ done |
| **T2 — Inbound chat** | webhook router + polling mode, `gateway.py`, `renderer.py`, `Session.channel`, `/new` command | Full two-way chat with every agent from Telegram. | ✅ done |
| **T3 — Media & polish** | inbound voice (STT) and attachments, tool-status message, optional live edit-streaming, optional TTS voice replies | Feature parity with the desktop chat surface. | 🟡 partial — photo/document attachments wired; voice is a graceful stub pending Whisper STT; tool-status message, live edit-streaming and TTS replies still parked |

Deliberately parked (revisit later, do not build now):
- **Group-chat war room** — all agent bots in one Telegram group. Collides
  with House Party Protocol semantics (OQ6/OQ9); park with it.
- **Live edit-streaming of CHUNKs** — rate-limit fight, marginal gain.
- **Multi-user** — never. Single owner by construction.

---

## 6. Decision Log

| # | Decision | Why |
|---|---|---|
| TG-1 | One bot per agent, tokens in config keyed by agent_id, presence flag on profile | Attribution at the notification surface; Rule 10 split (identity=profile, secret=config); per-token blast radius |
| TG-2 | Telegram is a *consumer of the SSEEvent stream*, not a new output_mode | Orchestrator untouched; CLAUDE.md already defines the router-decides pattern |
| TG-3 | Inbound turns are `triggered_by="user"`, `output_mode="respond"` | It *is* the user; the fourth-value prohibition stands |
| TG-4 | Webhook in prod, long-poll in dev, switch in config | Contabo has public HTTPS; dev machines don't; same gateway either way |
| TG-5 | Webhook exempt from X-API-Key, guarded by Telegram secret_token + owner-id allowlist | Telegram can't send custom headers we mint; secret_token is the platform mechanism |
| TG-6 | Sticky session per (agent, channel), `/new` to reset | Matches messaging UX; keeps app and Telegram transcripts coherent but separate |
| TG-7 | Buffer-and-send in v1, no live message editing | Telegram edit rate limits; DONE-flush is reliable and simple |
| TG-8 | Fallback chain: own bot → SPEDA bot (tagged) → DB notification row | A missing token or unpaired bot degrades one agent's voice, never drops the message |
| TG-9 | Files go out via `sendDocument` from `temp_outputs_dir` only, jail-checked | Reuses the existing file jail + 24 h n8n cleanup; nothing stored on Telegram's behalf |
| TG-10 | Existing single-bot `services/telegram.py` absorbed, not kept in parallel | Two Telegram clients = split-brain send paths; trigger router rewires to the registry |
