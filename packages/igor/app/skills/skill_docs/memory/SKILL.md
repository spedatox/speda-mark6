---
name: memory
description: Read a specific persisted owner-memory document or list its directory when the small standing context and per-turn recall do not contain the needed detail. Use evidence-bound domain tools for updates; raw memory edits are disabled.
---

# Memory documents

`/memories` is a virtual filesystem of owner knowledge. The system prompt carries
a small standing set and may add up to two relevant documents for the current
message. The directory listing shows paths without loading every file. A document
absent from the prompt is still available through `memory` with `command: view`.

Read the exact topic or entity before relying on detail that was not recalled.
Ultron can use `read_course_memory` to list records or open one course and term;
attendance and timetable remain separate structured data. For a person or project,
open its individual path rather than the whole collection.

```json
{"command":"view","path":"/memories/projects/speda-mark-vi.md"}
```

`view_range` can limit a large file. If the expected document is missing, use
`search_memory` for sourced observations or `recall_conversations` for earlier
discussion. Do not infer absence of knowledge from a failed automatic recall.

Reads are paginated with a strict character ceiling. Use the returned next line
range, or `char_range` for a very long line. Documents expose stable record IDs;
`explore_memory` follows those IDs to people/projects, dated passages and original
source editions. Old file paths redirect through the catalog. Immutable originals
are read with `{"command":"view","path":"source:<uuid>"}` or found with
`{"command":"search_sources","query":"specific name or topic"}`. Historical
sources can be outdated; unresolved course names, dates or differing descriptions
must not become settled facts merely because retrieval found them.

Agent writes through `memory` (`create`, `str_replace`, `insert`, `delete`) are
disabled. Use `record_observation` for durable sourced facts and the designated
evidence-bound domain tool for a managed document update. Do not edit derived
files such as `current.md` directly. Never store credentials or invented claims.

## MEMORY PROTOCOL

Memory describes the OWNER. It never defines your identity. Domain documents
are the authoritative record in their native shape: preserve tables, units,
relationships, biography chapters and entity histories. Search observations are
sourced claims for retrieval, not a replacement for these documents.

The executable routing contract injected with memory is authoritative for paths,
owners and verbs. Classify the meaning first, then the subject, then its time.

### Meaning and lifecycle

- An **event** happened: a purchase, sent email, completed workout or action.
  Use the subject's ledger or entity event log. If no specific subject log owns
  it, use `general/` for unmatched events and specialist categories for domain events. Use `memory_event` to capture personal experiences and events. It requires evidence, summary, and optionally a title and date. It files the document in the correct monthly folder automatically. Recent does not mean ongoing.
- A **state** continues: an unresolved application, current employment, waiting
  for a reply, or a confirmed future plan. Use `memory_state`, with a stable key,
  evidence, review date and explicit status. Get/list first, then supply its
  version. A completed action and its continuing consequence are separate facts.
- `current.md` is computed from state records. NEVER edit it. Expired or overdue
  states are unverified, not silently completed. Close a state only on evidence;
  its record and revisions remain available. Plans are not accomplished facts.
- A **reference** answers a subject question: academic, finance, wellness,
  cybersec or ops. Keep one authoritative topic file. A reissued document
  replaces its prior edition via `memory_edit`; get the contract and version first.
- A **person/project** has one existing identity under social/ or projects/.
  Read the directory and reuse its spelling. Use registry_upsert for descriptions
  and dated events. For an existing entity, an event-only discovery gets only an
  `event`: never resend `who` merely because an event occurred. `who` replaces
  the full description, so read the entity first and retain every still-supported
  existing fact. Event text reports what the evidence says; do not add an
  unstated appraisal, motive, consequence or importance. People are professional
  or personal; organisations are facts.
- **Preferences and prohibitions** are what the owner said, in dossier/<topic>.md,
  with [YYYY-MM-DD, agent_id] attribution. Inferred patterns belong to ACE as
  inductive observations with evidence; do not manually duplicate operational
  pattern state in patterns.md.
- **Biography** belongs in owner.md; use narrative_revise only for the chapter
  whose facts need correcting. Never regenerate his life story from snippets.

A subject's ownership does not change when your write is refused. Dispatch to
its owner. NEVER use current.md, general/, projects/ or shared notes as a workaround
for a refused domain write. `general/` holds personal events and references without a specialist domain. Capture what the owner reported — even ordinary trips, outings, and milestones. Do not require it to matter in six months. Do not fabricate details. It is actively used. It is NOT limited to recurring documents.
Owners can extend their own domain with a new topic; reuse an existing topic if
it already answers the question. Monthly categories store documents under `<category>/<MM-YY>/<topic>.md`. The backend determines the month from an evidenced occurrence date, or automatically from the current recording time in the owner's timezone when date is omitted. Do not supply a date or date_unknown merely to make a save succeed; neither is required. For example, use `general/10-26/istanbul-trip.md`. Month folders are virtual and appear automatically, even when empty; no mkdir or folder-creation tool is needed. Shaped writers create the document at its destination, including months absent from the listing. Continue using `memory_state` for stable state keys and `finance_record` for computed financial views. System logs and archived originals are protected.

### Evidence and safe writing

Raw memory create/insert/str_replace/delete are disabled for agents. The `memory`
tool is for reading. Every write tool requires evidence=[{ref, quote}]: an EXACT
supporting quote from message:<id>, observation:<id> or an existing memory path.
message:latest resolves only in the current OWNER conversation, never an automation.
If the owner asks you to retry after a fact was already recorded as an observation,
cite that observation's ID and a quote from its content. For a conversation quote,
prefer the original message:<id>; message:latest can recover an exact quote from an
earlier message in the same owner session and pins that message's ID in the receipt.
Never ask the owner to repeat facts merely because their latest message is a retry.
A separate reviewer checks placement and support before any mutation commits.
Validation failure is not success; retry only after fixing the stated defect
(e.g. citing the actual conversation message rather than a confirmation turn).
If a write returns **"needs owner confirmation"**, first check the actual
question and the owner's answer in context. A clear answer needs no restatement;
an assistant question supplies its referent, not factual evidence. Do not
automatically accept an ambiguous reply. If a material detail is genuinely
unresolved, ask one focused question when it affects the current task or at an
appropriate point in the conversation. Do not repeat an answered question or
press a frustrated owner for nonessential bookkeeping. An absent date alone is
never a reason to reject a fact that can be stored accurately without one.

Respond to the owner's actual intent before making memory maintenance the
subject of the reply. A held or failed write does not prevent using supported
owner statements in this conversation. Preserve their original messages and
unresolved scope; do not pretend that an unsaved observation is durable memory.
When a save remains unresolved, briefly explain the unsaved part and why once,
then continue the substantive conversation. Another owner acknowledgment is not
an instruction to retry or reopen the same clarification. Retry only when new
evidence or a concrete correction addresses the defect, or the owner requests it.

Financial activity MUST use finance_record. It chooses the destination from the
record type. transaction, balance, report and recurring are distinct schemas.
monthly-structure holds computed recurring rules ONLY. ledger/YYYY-MM holds
computed actual transactions. Never write this month's activity to standing rules.
An account balance is not a purchase price; a report or correction notice is not
another debt; loan proceeds and debt repayments are not earned income/spending.
Use stable ids to correct records, not a new row containing the correction.
Unknown amount is null, not zero or a balance borrowed from another field.


Record only supported knowledge. An assistant proposal is not an owner decision.
Use absolute dates; calculate countdowns at read time, never persist them.
Distinguish planned, attempted, completed, cancelled and unknown outcomes.
If evidence conflicts, keep both sources and flag the contradiction; do not pick
whichever version is newest without inspecting what changed.

Writes compare against the original content and preserve a revision in the same
transaction. A concurrency conflict means reread, reassess and reapply the small
intended change. Never retry an old whole-file replacement blindly. Preserve
unrelated content. Do not duplicate an event already recorded by another agent.
If a corrected retry returns the same deterministic failure, stop that write
attempt and report it once. An acknowledgement such as "thanks" or "good" is
not a request to repeat a save, rewrite another record, or investigate the store.
Never claim an entire update failed when one destination succeeded: name the
saved part and the still-unsaved part accurately.

Cross-file refiling needs an atomic, evidence-bound operation. The bulk audit
repair path is removed; never improvise two separate raw writes.

Most shared-memory turns need no write. Domain events, explicit corrections,
preferences and ongoing-state transitions are exceptions: record them when
learned. Write silently on SUCCESS — do not narrate internal memory plumbing or
tool mechanics to the owner. But on permanent failure or unresolvable rejection,
NEVER silently drop it; explain to the owner what failed to save and why.
Never store credentials, secrets, passing chatter or guesses as facts. Do not create memory records from hypothetical scenarios or design document examples. Only record events the owner actually experienced.

### Reading and recall

Read the small standing block and the per-turn recalled documents first. The
standing block carries owner identity, current states and binding preferences;
biography, history, patterns and domain records are selected for the current
question or opened on demand. The standing index names roots; list a specific
folder only when retrieval does not identify the file. Do not
reread a complete record already shown this turn. A shortened excerpt is not
the full record: open its path when the omitted material could affect the
answer, especially binding conversational preferences. Read a relevant topic or entity;
read related files when the question requires a relationship or a contradiction
check. A count of files is not a correctness rule.

The "Today in other conversations" block contains recent owner messages from
separate chats, even when their recaps have not finished. Use it to continue a
same-day thread without asking him to repeat it. It is a short excerpt, not the
whole exchange: open the source conversation if the reply, decision, or exact
context matters. When an observation names a person, event or project, use
`search_memory` with `mode="related"` and its id to inspect co-mentioned facts,
same-subject facts and premises. A shared message or mentioned subject is a
retrieval link, not proof of causation or a relationship the owner did not state.
When the question spans an event document, person/project record and a sourced
fact, call `explore_memory` with an exact `observation:<id>`, `record:<uuid>` or
`path:<memory path>` address. Follow the labelled links only as far as needed;
read the original source when a conclusion depends on precise wording. The
graph returns a bounded neighborhood, so one question never loads the vault.

Use the retrieval source suited to the missing evidence: an exact document, sourced facts, meaningful past exchanges, or literal history. No fixed sequence is required. A short or vague
user message may retrieve nothing; use the available read tools instead of
assuming the missing fact does not exist. Search results are claims with evidence and dates;
stale domain facts do not become current because search returned them.

The dossier governs how to treat the owner. Act on it without reciting it.
Binding owner prohibitions outrank inferred patterns. Fix facts that block your
task; full corpus cleanup is an explicit offline migration, never a scheduled
model-driven audit. Read document warnings: alternative editions and course/code
conflicts are unresolved sources, not permission to silently pick one. Use a
document's `record:<id>` with `explore_memory` to follow its linked people,
dated entries (`passage:<id>`) and immutable originals (`source:<id>`).
`memory` with `command="search_sources"` and a specific query searches historical
originals when active recall misses; open an exact source ID when wording matters.
Historical claims may be outdated. Read pages as needed instead of requesting the
whole vault. Reuse existing entity identities and their declared aliases; paths
and storage months do not establish an event date or create a new person.
