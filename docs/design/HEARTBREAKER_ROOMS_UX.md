# Heartbreaker room UX study

Status: implemented locally, updated 29 October 2026. Browser validation uses
the real Heartbreaker renderer, authenticated API and Coordinator with an
isolated database and a fixture provider. No production deployment or paid
provider evaluation is included.

The conversation is the primary surface. Start with the owner and Speda; an
invitation adds a named participant and a “Ultron joined the chat” event in the
same history. Each participant uses their existing Heartbreaker sigil and name.
The room title and conversation history remain stable as membership changes.
Speda continues to lead until an explicit handoff is recorded.

Owner direction: both experiences must exist. Regular collaboration uses the
compact Chat deck: Speda summons selected agents into the conversation as needed.
House Party is the high-stakes escalation: deliberately engage the protocol,
assemble the full roster and expand into the Party lobby with a persistent
roster. These are complementary modes, not competing layout alternatives.
Summoning an individual agent never engages House Party. Standing down returns
to the previous regular conversation and its participants. House Party retains
its own transcript; the two histories are not combined or automatically copied.

Owner correction, 29 October: there is no permanent collaboration toolbar.
New and one-to-one chats retain the normal conversation surface. A compact
membership strip appears only after a second participant joins. Summon opens
on demand through the conversation header's ellipsis control, rather than
occupying a permanent row. House Party uses its existing rail entry, deliberate
authorization and separate protocol controls. The address selector is contextual
to a group conversation or a deliberately chosen reply target.

Both modes provide summon, dismissal, reply-to and explicit addressing. Joining does
not cause every member to answer every message. Ordinary messages go to the
lead; mentions and reply-to target a participant. Private consultations do not
fabricate public join events.

House Party retains its name and purpose: summon the full roster into a
Speda-led group conversation, including Orion, matching the existing backend.
Membership does not invoke every participant on every message. Speda's turn
decisions, persona permissions, model policy and shared spending limits remain
in force. A network failure cannot silently turn the displayed protocol off,
and a requested stand-down waits for a successful server receipt.

Implemented behavior:

- Replaced the legacy comms-polling transcript with room events ordered by the
  server sequence and attributed by server speaker IDs. Do not merge unrelated
  `/agents/comms` traffic into the room.
- Bound membership, lead changes and replay deduplication to Coordinator
  contracts. Membership events carry the confirmed room snapshot, updating the
  roster immediately. Joins follow server receipts; failures remain visible.
- Kept separate per-speaker messages and streaming attempts. Preserved reply-to,
  attachments, tools, rich blocks and existing owner controls.
- Dismissal changes future participation and retains delivered history; it does
  not implicitly cancel ongoing tasks. Reply, task and run cancellation remain
  distinct controls. Preserve House Party's established engagement policy.

Integration details:

- Heartbreaker negotiates `X-Chat-Format: room-v1` on chat, attach and history
  requests. Existing clients retain their default response format.
- `/sessions/{id}/room` resolves the authenticated session's room; membership
  writes use the Coordinator's existing version-fenced endpoint.
- `addressed_agent_id` and `reply_to` are optional intake fields. Reply references
  must identify a public, visible message in the same room. The provider sees
  attributed quoted context, while the owner's original text stays unchanged.
- Private consultations are excluded from the public native transcript and its
  history. Interrupted public contributions can be recovered from room events.
- Replay uses stable step IDs and server sequences; late acknowledgments and
  history responses are fenced to the selected conversation view.
- The standard rich message renderer, attachments, tools, worker cards and
  scoped stop controls remain in use. No production database cutover is needed
  for these additive presentation and request-contract changes.

Validation commands:

```powershell
node --test packages/heartbreaker/tests/*.test.cjs
npm run test:workers
npm run heartbreaker:typecheck
npm run heartbreaker:build
```

Backend contract coverage lives in `tests/test_heartbreaker_rooms.py`, alongside
the existing client compatibility, Coordinator, room queue and agent settings
tests. Use the documented external Python environment and an external pytest
scratch directory, as described in the repository README.
