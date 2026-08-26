# Skyfall Protocol — the owner's launch rail

**Agent:** Speda only · **Tool:** `skyfall_protocol` (arms, never fires) · **Endpoints:** `/protocols/skyfall/*`

| Piece | Where |
|---|---|
| projects, validation, masking, the request | [`app/services/skyfall.py`](../packages/igor/app/services/skyfall.py) |
| the tool and its three gates | [`app/skills/skyfall.py`](../packages/igor/app/skills/skyfall.py) |
| the HTTP surface | [`app/routers/skyfall.py`](../packages/igor/app/routers/skyfall.py) |
| which surfaces may arm | [`app/core/surface.py`](../packages/igor/app/core/surface.py) |
| what Speda is told | `app/prompts/core/12_skyfall.md` |
| desktop countdown / project pane | `SkyfallCountdown.tsx`, `SkyfallProjects.tsx` |
| phone countdown / protocols pane | `ui/skyfall/SkyfallCountdown.kt`, `ui/settings/ProtocolsTab.kt` |
| the invariants, pinned | [`tests/test_skyfall.py`](../packages/igor/tests/test_skyfall.py) |

> **Note on the name.** `SKYFALL_PROTOCOL_PLAN.md` used to describe a fleet
> kill-switch and licence spine for distributed Igor binaries. That design is
> gone; only the name carried over. Nothing here relates to it.

---

## What it is

The owner defines **projects**. A project is a name, an explanation, and an
endpoint it hits — URL, method, an optional JSON body, optional headers, and how
long its countdown runs.

Arming one opens a full-screen countdown. Letting the clock reach zero fires the
endpoint. Aborting sends nothing at all.

## The one property everything defends

> **Nothing fires without a countdown the owner could have aborted.**

Strip that away and what remains is an agent that will POST to any URL in the
owner's config on being asked nicely. Every rule below exists to keep it true.

### Two ways in, one screen

| Route | How |
|---|---|
| **By voice** | "Activate the Skyfall protocol" → Speda asks which project → `skyfall_protocol` arms it → an SSE event opens the countdown |
| **From settings** | Settings → Protocols → Skyfall → pick a project → the same countdown |

Both land on the same component. The settings pane does **not** draw its own
clock; it fetches the arming payload and hands it to the shell, which owns the
one screen. A second countdown is how a protocol ends up with a path that skips
its own safety.

### The tool arms; it cannot fire

There is no path from `skyfall_protocol` to `services.skyfall.fire`. Arming
writes a message to the client saying "open the clock". The **client** fires when
the clock runs out.

### Three gates on arming

1. **`triggered_by != "user"` refuses.** Carried from `lockdown.py`, which
   carried it from House Party, where it was a live failure: a dispatched agent
   was handed an "EMERGENCY" instruction as an ordinary inter-agent task.
2. **A surface with no screen refuses.** Telegram carries "activate Skyfall"
   perfectly well and has nowhere to draw a countdown or an abort. Desktop, web,
   Android and iOS may arm; Telegram and an unnamed client may not.
3. **An ambiguous project name refuses.** A project resolves only on an exact id,
   an exact name, or a single substring hit. Two similar names and a model's
   guess is how the owner ends up watching a clock count down to the wrong
   endpoint — with the abort window as the only thing between the guess and a
   real request. The tool lists the candidates and asks again.

### If the clock stopped being shown, it does not fire

Both clients measure the **wall clock** between frames. A hidden window runs no
frames at all — minimise it, background the app, let the machine sleep — and the
naive reading on return is "already past the deadline, fire now": a request going
out with no countdown and no chance to stop it.

So a frame gap past ~1.5s means the countdown was not on screen while it
mattered, and the launch **stands down** instead. The screen is the protocol; a
screen nobody was shown did not run. It fails toward "did not fire", which is the
only direction this may fail in.

### Aborting is the absence of an action

Abort does not cancel a request in flight — it means the client never makes one.
Nothing can arrive too late, and a crashed renderer, a closed window or a dead
process all land on "did not fire". `POST /abort` afterwards only writes it down;
if that call fails, the abort still happened.

---

## Who writes a project

**The owner, from the settings pane. Nothing else.**

There is no tool that creates, edits or deletes a project, and adding one would
undo the whole design: an agent that could write the target *and* pull the
trigger could hit anything, and the countdown would be guarding a URL the owner
never chose. Agents arm what already exists, by name.

Header **values** get the portal-credential treatment: stored server-side, masked
on every read, never returned to a client and never placed where a model can read
them. A project's `Authorization: Bearer …` must not come back out in a chat
message. Saving a form rendered from masked data sends the mask back, which the
server reads as "leave this one alone" — so editing a description does not blank
a token the owner never retyped.

The countdown payload carries **no body and no headers**. The request is assembled
server-side at zero: a client that never holds the secret cannot leak it, and one
that cannot alter the payload cannot turn an armed countdown into a different
request than the one that was armed.

### What is deliberately not guarded

The URL is not filtered and internal hosts are not blocked. `http://n8n:5678/…`
and `http://app:8000/…` are the likely targets, not an attack — this is the
owner's own configuration, typed by the owner, in their own settings pane. The
boundary that matters is *who writes the project*, and that is above.

---

## Validation happens at save, never at zero

A JSON body that fails to parse, a countdown under 3 seconds, a method that is
not one of GET/POST/PUT/PATCH/DELETE, a URL without a scheme — all refused when
the project is **saved**. Nobody wants a surprise at the one moment the clock is
running and the owner is watching.

The 3-second floor is not decoration: a one-second clock is a screen that
technically exists and cannot actually be aborted, which is the same as not
having one.

---

## Reading the outcome

`fired` and `ok` are separate fields, and the screen renders them separately:

| State | Means |
|---|---|
| not fired | nothing left the machine — a deleted or unusable project |
| fired, ok | the target answered 2xx |
| fired, not ok | it went out and the target rejected it, or the network died |

The case that must never be rendered as "nothing happened" is "we do not know".
A client that loses contact mid-launch reports `fired: true` with the honest
error, because it cannot know the request did not go out.

Every fire and every abort is logged with the project, the method, the URL and
the seconds remaining — "did that fire or not?" has to be answerable afterwards,
and silence cannot answer it.

---

## Safety summary

- No deployment flag: the protocol is inert with zero projects configured, and it
  touches no host, no firewall and no proxy — only URLs the owner wrote.
- `restricted_to={"speda"}` — the rest of the roster has no launch rail.
- The tool refuses automated triggers, screenless channels and ambiguous names.
- The tool cannot fire, the countdown cannot be skipped, and both clients stand
  the launch down rather than fire a clock nobody watched.
