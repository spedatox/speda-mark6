# Heartbreaker and SPEDA Go

The owner requires one product across desktop and phone. The backend remains the
authority for conversations, membership, model policy, memory, task execution and
presentation settings. The Android layout stays native to the phone.

Implemented locally on 3 November 2026:

- Chat, attachment replay and history negotiate `X-Chat-Format: room-v1`.
  Server step IDs and sequences preserve distinct named speakers, tool/file
  attribution and reply references across reconnects and history reloads.
  Repeated or older frames cannot concatenate an answer twice or erase newer
  history. Membership notices are excluded from edit/regeneration row counts.
- The header's ellipsis opens participant controls. Summon, dismiss and handoff
  use server version fences. Membership is displayed after receipts, with a
  compact member strip for group conversations. Explicit recipients, mentions
  and reply-to use the same backend intake fields as Heartbreaker. Raw room
  polling displays membership only; private consultations stay out of the chat.
- One authenticated worker feed per selected session recovers a bounded snapshot
  before cursor replay. It continues after the public reply finishes, performs
  reads only, reconnects with bounded backoff and rejects terminal permission or
  malformed-protocol responses. Late results are fenced to the selected view.
  Worker cards merge by execution and parent-step identity, including the case
  where work outlives a stopped reply. Preview text and replay IDs are bounded.
- The composer can steer an active reply while retaining its separate stop
  control. Input IDs survive ambiguous acknowledgments, concurrent identical
  submissions share one request, and the expected speaking step fences delivery.
  Acknowledgments cannot echo into another view or clear an edited draft. Initial
  session assignment does not reset the draft typed while a turn is starting.
- Voice turns send `canvas` and `canvas_max_panels` from the shared owner settings.
  Disabled canvas settings suppress panels. Public participants use their own
  voice, queue playback in speaking order and retain per-answer recordings.
  Stop and view changes cancel the whole queued playback chain.
- An authoritative empty server history remains empty. Cached transcripts cover
  transport failures; they do not replace a successful empty response or a
  terminal authorization failure.

The shared `room_v1.json` trace lives in Android's test resources and is consumed
by both Kotlin's `RoomParityTest` and Heartbreaker's `rooms.test.cjs`.
`SessionRecoveryTest`, `LiveSteeringTest`, `RoomVoiceQueueTest` and real-loopback
`ClientTransportTest` exercise recovery, acknowledgment races, ordered playback,
fragmented UTF-8/SSE, native headers, target/reply/canvas fields, membership
conflicts and read-only worker recovery. Backend room, worker, canvas and surface
contracts are exercised with fixture providers, without paid calls.

Run Android checks from a staging copy outside the checkout, with the documented
JDK 21 environment and installed Android SDK:

```text
gradlew.bat :app:testDebugUnitTest :designsystem:testDebugUnitTest :app:assembleDebug
node --test packages/heartbreaker/tests/*.test.cjs
python -m pytest tests/test_heartbreaker_rooms.py tests/test_worker_control.py tests/test_canvas_presentation.py tests/test_client_surface.py -q -p no:cacheprovider --basetemp <external-scratch>
```

Builds, reports and the test APK are retained outside the checkout under
`C:/Users/ahmet/.codex/tmp/speda-go-parity-20261103`.
The final run passed 93 Android checks (85 app and 8 design-system), all 49
Heartbreaker checks and 26 backend contract checks, with zero failures or skips.
The debug APK assembled successfully; every staged Kotlin/build/resource source
matched the checkout by SHA-256. See `speda-go-parity-validation.json` for the
artifact identity and retained report digests.

This is local implementation and verification. No production service or owner
dataset was changed. No Android device was connected for visual/install testing.
Signed release installation and live cross-device acceptance remain release
checks. The existing desktop/passphrase gate for House Party, publication
approvals and separately tracked continuous-voice limitations remain in force.
