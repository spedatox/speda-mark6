---
name: calendar-operations
description: Create or change calendar commitments, recurrence and attendees accurately; load when a scheduling operation needs these details.
---

## THE CALENDAR IS THE RECORD

His calendar is not one tool among many. It is the only place the system and the
owner both write down his commitments, and it is the thing that makes an
answer about his week true rather than plausible. Every agent reads it. Every
agent writes it. The whole roster shares one record of one man's time, which is
exactly why what goes in it has to be complete enough for another agent — or him,
in March — to act on without asking anybody.

### Read it before you answer

When the answer depends on live commitments or availability, load the calendar
(`use_toolset` → `google_calendar`) and read the window with
`calendar_list_events` before concluding. Examples include:

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

"I'm not going to work tomorrow." "The exam moved to Friday." "I'll be in Ankara
next week." When he communicates a concrete commitment or cancellation,
reconcile the affected record. Distinguish that from recounting an event,
expressing a boundary or discussing a possibility. Do not turn every mention
of a day into an unsolicited calendar mutation. For an authorized update:

**read → find what it touches → if it isn't there, ASK → write → one line back.**

### When the calendar doesn't have it, ask

He says he isn't going to work tomorrow and there is no work on tomorrow. You do
not have what you need to write anything correct, and you do not get to fill the
hole: not with a guessed shift, not with a silent "noted", not with a reply that
pretends the sentence was small talk.

Ask one short question for exactly the missing piece — what the thing is, what
hours it runs, whether it repeats. Then write it properly, the commitment AND the
absence, so the second time he says it you already know. Never ask for something
the calendar could have told you if you had read it first, and never stack
questions: the smallest one that unblocks the write, then act.

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
