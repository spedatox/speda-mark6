# Native delivery and speech

`app.state.deliveries` owns a durable outbox. Successful visible push outputs
enqueue alongside the original assistant message in the same transaction.
An immediate post-run drain handles ready deliveries; n8n can drain remaining
pending work with `/deliveries/pending/drain`. No local recurring scheduler was
added. Transport acceptance is not proof the owner read or heard a message.

Telegram sends are chunked to a conservative UTF-16 limit. A linked agent bot is
preferred; a linked Speda bot is an explicitly attributed fallback. FCM is chosen
when Telegram is unavailable before dispatch. There is no second-channel fallback
after an uncertain send. `pending -> sending` commits before the request. Lost
responses or interrupted sends are held, never put back into pending. The owner
can inspect `/deliveries`; unresolved `sending` after a crash is also uncertain.

`send_telegram_message` and `send_telegram_file` use that same outbox. Automatic
push replies must not additionally call the text tool. Files must have been
produced in the current turn, remain inside the outputs folder, fit 10 MiB, and
match their queued hash. Queued manual sends are fenced by their originating
run, step, executor, lease and deadline. A file cannot be regenerated or changed
under an existing delivery identity.

## Telegram ingress and reminders

Configure `TELEGRAM_BOT_TOKEN` for Speda and/or `TELEGRAM_BOT_TOKENS` as an exact
agent-id/token JSON mapping, plus `TELEGRAM_WEBHOOK_SECRET`. Optionally pin the
numeric `TELEGRAM_OWNER_ID`. Register each bot's HTTPS webhook at
`/telegram/webhook/{agent_id}` with Telegram's secret header, `message` and
`callback_query` updates. Token provisioning and live `setWebhook` are operator
activation steps; startup performs neither. Tokens are not returned by status.

Authenticated Settings obtains a cryptographically random single-use pairing
link valid for ten minutes. Bare `/start` and the old shared fixed nonce cannot
claim ownership. Pairing binds the private sender/chat and agent. A later sender
cannot replace the owner. Text intake persists before Coordinator admission and
uses deterministic update identities. Input retries and paused-budget recovery
share the same run. Telegram model pins use the revisioned native policy and
are retained with the admitted input; changing a later channel default cannot
reroute an exact update retry. Forwarded material is declined rather than stored as an
owner assertion. Media ingress and live polling are not implemented here.

Reminders have versioned definitions and one open question per stable reminder
identity. `/reminders/tick` requires both scheduler secrets; it computes due
times in the owner's timezone. Re-asks reuse exact stored text and options and
respect explicit attempt/interval limits. Unknown deliveries block later asks,
including across a day boundary. An answer stops pending sends. Buttons bind
owner, bot, chat, question version and delivery; stale callbacks cannot answer a
new question. Ambiguous chat answers require selecting a cycle. Reminder history
is attributed operational state, not an alternate personal memory writer.

## Speech

`app.state.voice` serves the original `/voice/status`, voices, agents, speak and
listen surfaces and the text/audio tools. Owner pins are transactional and
versioned, with shared and TR/EN overrides. Original persona voice identities
are retained; explicitly selected models are not silently upgraded.

Azure, OpenAI and ElevenLabs HTTP synthesis have bounded input/output and no
retries. Text receives deterministic markdown cleanup only; there is no language
rewrite, sanitizer LLM or helper model. XML text/attributes are escaped on Azure.
Microphone input must be complete 16 kHz mono 16-bit PCM WAV, at most 60 seconds
and 2 MiB; headers alone cannot establish its duration. Silence is a successful
empty transcript. Transcription does not itself make a canonical owner assertion.

Every paid speech attempt reserves the **same global** owner call/money ceiling.
Set explicit positive `external_prices` through the global spending policy;
credentials alone cannot authorize spending. Routes are
`speech:tts:{provider}:{model}` (conservative input-byte units; OpenAI reserves an
additional 8x ceiling) and `speech:stt:azure:seconds` (ceil of actual WAV duration).
These are operator-set upper bounds, not price quotations or actual billed usage.
Speech reserves zero model tokens. All reservations, including uncertain calls,
still count toward lifetime and daily allowances.

Exact completed input can reuse its saved audio. The same uncertain input is
held even if a caller changes its request id. Deleted artifacts do not silently
trigger a second paid generation. Continuous-input ElevenLabs streaming is not
yet adapted to this paid-attempt protocol: status reports `streaming: false` and
the authenticated socket declines to the working HTTP path. Do not describe this
as full live voice/streaming parity.

Desktop/web turns now explicitly send `client_context.canvas` independently of
`voice`, plus their renderer panel capacity. Only active desktop/web canvas turns
receive the profile-owned visual-first brief; Ultron additionally receives a
visual tutoring workflow. Normal chat, voice-only turns and anonymous workers do
not receive that active brief. Telemetry stays transient and never becomes owner
memory or a tool/spending grant. The server's `VOICE_CANVAS_ENABLED` and bounded
`VOICE_CANVAS_MAX_PANELS` (default 24, maximum 32) are exposed in configuration
and voice status. A smaller client limit is respected. Existing model output and
budget limits remain enforced; more panels do not authorize additional paid work.
Canvas and its scrollable native renderers have local validation. Production
installation and live provider teaching quality still require release validation.

Local tests use fixture network responses and real SQLite/PostgreSQL records.
No live message, speech request, account connection or push was sent.

References: [Telegram API](https://core.telegram.org/bots/api),
[Telegram deep links](https://core.telegram.org/bots/features#deep-linking),
[OpenAI speech](https://developers.openai.com/api/reference/typescript/resources/audio/subresources/speech/methods/create),
[Azure short-audio recognition](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text-short),
[ElevenLabs synthesis](https://elevenlabs.io/docs/api-reference/text-to-speech/convert).
