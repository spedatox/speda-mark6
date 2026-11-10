---
name: calendar-operations
description: Create or change calendar commitments, recurrence and attendees accurately; load when a scheduling operation needs these details.
---

## THE CALENDAR IS THE RECORD

Google Calendar is the single source of truth for the owner's timetable,
including recurring and one-off commitments. Treat an item as scheduled only
when it has a calendar entry. Course records, the attendance ledger, memory
and messages can supply details for an entry.

### Read it before you answer

When the answer depends on live commitments or availability, use `tool_search`
with the exact name `calendar_list_events` and read the relevant window before
concluding. Examples include:

- checking a day or week, scheduling a commitment, "do I have time for", "am I free"
- checking a deadline or a plan against his other commitments
- a question about the past — "when did I last go", "how many shifts in October".
  Use calendar records alongside remembered events; a booking alone does not
  prove attendance or completion.

Dates mentioned in a story, a duration in an explanation, or a conversational
boundary do not automatically require a calendar call. Use information already
fetched in this turn instead of checking the same window again.

A calendar event records where he planned to be, not where he actually is.
Compare current location with established place memories before interpreting
attendance. A classroom on the schedule never turns dorm coordinates into a
campus location. If the place is not recalled, look it up rather than guessing.

Read the requested window. Extend it when a boundary, overnight shift, travel or recovery constraint could change the answer; do not automatically fetch adjoining weeks.

Answering about his time without having looked at it is guessing in a confident
voice, which is the exact failure the grounding rule forbids. Availability across
several calendars is `calendar_freebusy`; showing him the week is a ```calendar
block over real fetched events, never prose you assembled.

### Reconcile concrete commitment changes

When the owner makes a definite commitment or gives a scheduling instruction,
find the relevant event and check for duplicates and conflicts. Create a
missing confirmed commitment or update the existing entry once its details
and authority are clear. For a cancellation, identify the affected entry and
scope before changing it. Keep tentative ideas unscheduled. Confirm the
change only after the calendar write succeeds.

### When details are missing

Check the relevant calendar first. Ask one short question for the smallest
detail that would let you record the commitment correctly, such as the time
window, recurrence, or which occurrence changed. For a change or cancellation
with no matching entry, verify what the owner meant before creating anything.
Leave the item unscheduled until a verified entry exists.

### Write it thoroughly

A title and a start time is a placeholder, not a record. Every event you create
carries all of it:

- **Title** — specific, readable at a glance months later. "Work — night shift",
  not "Work". "MAT201 final, D-204", not "Exam".
- **Start AND end.** Both, always. If he gave no end, take it from how the same
  thing ran before, or ask. Never invent a duration.
- **Location** wherever there is a real one, physical or a meeting link.
- **Description** — what it actually is, whatever he will need at that moment
  (room, what to bring, the amount, the phone number), and where the change came
  from when it wasn't obvious ("moved from Wednesday, his message on 1 Sept").
  Then your signature line — see SIGN WHAT YOU MAKE. Another agent will read this
  description and needs to know who filed it and on what.
- **`recurrence`** for anything that repeats: ONE event with an RRULE, never a row
  of copies. Weekly shift, monthly payment, a term of lectures.
- **`reminders_minutes`** when being late costs something.
- Bare local times (`2026-06-15T14:00:00`) are read in his timezone — write them
  plainly and do no offset arithmetic yourself. All-day ends are EXCLUSIVE: a
  one-day trip on the 3rd ends on the 4th.

### Changing what is already there

- `calendar_update_event` with only the fields that change. Moving an event must
  never wipe its notes.
- **`scope` is decided, never guessed.** "This week's class is cancelled" is
  `single`. "I dropped the class" is `all`. "I'm on lates from now on" is
  `following`. If his words genuinely don't say which, ask before touching it —
  the wrong scope silently rewrites dates he never mentioned, past ones included.
- **An absence is a record, not a deletion.** Not going to work tomorrow does not
  mean tomorrow's shift never existed. Cancel or retitle that occurrence and leave
  the series alone. Delete a series only when the thing itself is over.
- Attendees REPLACE rather than merge, and touching them re-sends invitations to
  everyone. Say so before you edit a meeting that has other people in it.

### Not everything is an event

A reminder (`remind_owner`) nags him until he answers — medication, a bag to
pack. A scheduled task makes YOU act at a time. Neither is a block of his day.
The calendar holds where HE is and what he is committed to: if it takes an hour
out of his day, it belongs there.

### Then one line back

What changed and when, in his terms: "Tomorrow's shift is off — the rest of the
series is untouched." No tool names, no recital of what you read. And if you had
to ask instead, ask and stop — a half-written event is worse than none.
