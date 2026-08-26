# DOORMAT PROTOCOL — moving the server to a new domain

The owner changes the domain rarely and needs it to go right every time. Half the
work is on this host and you can do it. The other half is in three consoles you
cannot reach, and that half is where the move actually breaks. **Your job is both
halves: run the server side, and walk him through the rest without letting him
skip a step.**

Tool: `doormat_protocol`. Every phase except `status` needs him in the
conversation — there is no urgent domain change.

## The rule

**The old door stays open until the new one is proven.** Caddy serves both
hostnames at once, so there is never a moment where neither works. Every phase
below exists to keep that true. Do not try to be efficient by combining them.

## Phase 1 — stage

`doormat_protocol(action="stage", domain="...")`

Ask for the domain **verbatim** and pass it verbatim. Never complete it, never
guess it from something he said earlier, never assume `www.`.

It refuses unless the domain already resolves to this server. That refusal is a
feature and you must not route around it: a Caddy site for a hostname pointing
somewhere else burns the Let's Encrypt rate limit and the certificate never
arrives. If it refuses, tell him the A record to create and the address to point
it at — both are in the refusal — and wait. DNS takes minutes.

`force=true` is ONLY for a proxy in front (Cloudflare and the like). Never use it
because a record has not propagated yet.

When it succeeds, the new domain is live with a real certificate and **nothing
else has changed**. `abort` undoes it completely.

## Phase 2 — the part you cannot do

Staging hands you a checklist built from what this deployment actually uses, with
the exact strings to paste. Give it to him as a list he can work through, one line
per step, and **say the values in full** — a redirect URI retyped by hand is a
redirect URI with a trailing slash, and Azure rejects that with `AADSTS50011`,
which names nothing useful.

The rule in every step is **ADD, do not replace**. The old URI keeps working until
retire. If he replaces instead of adding, he breaks sign-in on the address he is
still using.

Then stop and wait. Do not run cutover until he confirms those are done. If he
asks you to "just do the whole thing", explain in one line why the middle step is
his and wait anyway.

## Phase 3 — cutover

`doormat_protocol(action="cutover")`

Repoints Igor's Telegram webhook base and the three OAuth redirect URIs. Both
hostnames still serve, so this is still recoverable.

These settings are read at startup, so **you are still running on the old domain
when it returns**. Finish with:

```
system_ops(action="restart_service", service="app")
```

That is a self-restart — the SERVER OPERATIONS rules apply in full. It schedules
detached and fires after your reply, so write your report to him in the same
message and then stop. Telegram webhooks re-register for every bot on boot; there
is no console step for those.

## Phase 4 — retire

`doormat_protocol(action="retire")`

Only after he confirms the new address works everywhere he uses it — desktop app,
phone, Telegram, whatever else. This is the one step that recreates a container,
so Caddy blinks for a few seconds.

It refuses while the new domain is not serving, and while the cutover settings are
written but not yet loaded by a restart. Both refusals mean exactly what they say.

Afterwards, remind him to remove the OLD redirect URIs from Google, Microsoft and
Notion. They were kept on purpose until now — but a stale redirect URI pointing at
a domain he no longer owns becomes somebody else's login button the day another
person registers it.

## Reporting

Say which phase you are in and which are left. He is moving house across days, not
minutes, and the thing he most needs from you is to know exactly where he stopped.
`status` answers that at any time and costs nothing — use it when he comes back
and asks "where were we".

Never describe a phase as done that you have not run, and never call the move
complete before retire. A half-finished move that reads as finished is the one
outcome this protocol has no defence against.
