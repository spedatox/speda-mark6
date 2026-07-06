# Mark VI Memory Architecture — v2 ("Orion Charter")

Status: **DESIGN** — approved scope: strict file boundaries, the Orion custodian
agent, and owner-committed edits from the systems board. This document is the
contract; implementation follows it exactly.

---

## 1. The Problem Being Solved

The v1 memory system works but its boundaries eroded in practice:

- Facts drift between files (active work lingering in `history.md`, project
  detail leaking into `owner.md`).
- Nothing *owns* hygiene — cleanup is a side effect of whichever agent happens
  to notice, mid-conversation, with conversation-priority attention.
- The owner can *see* memory (DATA_BANKS // KNOWLEDGE panel) but cannot
  *correct* it, so errors persist until an agent stumbles on them.
- New file types (gym sessions, social graph) were added ad hoc with no
  declared schema, accelerating the blur.

v2 fixes this with three moves: **(a)** a strict, closed file taxonomy with
one-question-per-file routing, **(b)** a dedicated custodian agent — **Orion** —
whose entire job is Mark VI maintenance, and **(c)** an owner write path with
audit and precedence rules.

---

## 2. The Canonical File Set

The `/memories` virtual filesystem contains **exactly these files**. Creating
any other top-level file is a protocol violation; Orion merges strays into the
canonical set and deletes them.

> **Amended by Revision R1** (docs/MEMORY_REVISION_R1.md). Where the text below
> conflicts with R1, R1 governs. Key R1 change: owner.md and history.md are
> divided by the **epoch line** — the birth of Mark VI (2026-05). Pre-Mark-VI
> context → owner.md; Mark-VI-era states that ended → history.md.

| File | Temporal nature | The one question it answers |
|---|---|---|
| `current.md` | **Volatile present** | *What is true in the owner's life right now?* |
| `owner.md` | **The Prior (pre-Mark-VI)** | *Who is he, and what shaped him before I existed?* |
| `dossier.md` | **Observed preferences** | *What have we observed he likes, dislikes, wants — and in what manner?* |
| `projects.md` | **Active ledger** | *What is he building, and where does each effort stand?* |
| `social.md` | **People registry** | *Who matters to him — who ARE they to him — and what's the latest?* |
| `sessions.md` | **Training log** | *What happened in the gym, day by day?* (Atomix's domain) |
| `history.md` | **Mark VI-era ledger** | *What happened during Mark VI's watch that no longer applies?* |
| `log.md` | **System trail** | Rolling one-line session summaries (system-owned, bounded) |

### 2.1 Injection policy

Injected into every turn's context (as today): `current.md`, `owner.md`,
`dossier.md`, `history.md`. Read on demand via the memory tool:
`projects.md`, `social.md`, `sessions.md`, `log.md` — **except** Atomix, which
additionally gets `sessions.md` injected (it is his working file), and any
agent handling a person-centric task reads `social.md` first.

### 2.2 Per-file contracts

Every file begins with a two-line header block maintained by Orion:

```
# <Title>
_Last audited: YYYY-MM-DD by orion · Last written: YYYY-MM-DD by <agent_id|owner>_
```

**`current.md` — the present-tense lens.** 3–10 bullets, each date-stamped
`(as of YYYY-MM-DD)`. This file *disambiguates everything else*: when two
facts elsewhere could both apply (e.g. "works an IT job" in history vs
"working at Arel Tarım" here), `current.md` decides which is live. Rules:

- Only genuinely active states: location, employment, training regimen,
  immediate milestones, active concerns.
- A state that ends is not deleted — it is **demoted to `history.md`** with
  its start/end dates in the same edit.
- Conditional/causal phrasing is preserved ("in Bursa **because** semester
  ended", "IT job on hold, **resumes September**") — the *why* and the
  *until-when* are what make the snapshot self-expiring.

**`owner.md` — The Prior (pre-Mark-VI).** [R1] Identity constants (name,
codename, address forms) plus the owner's biography from birth → the creation
of Mark VI (2026-05): education, places, formative work, family background —
the fixed prior that lets an agent know the man it serves. Updated **in place**
as facts are revealed or corrected; the past does not expire, our record just
sharpens. Behavioural preferences do **not** live here (they moved to
`dossier.md` under R1); no projects, no schedules, no current states.

**`dossier.md` — observed preferences the agents must learn from.** [R1]
Observations built as agents talk to him: what he likes, dislikes, wants, and
*in what manner* — **both stated preferences and inferred patterns** (the old
"inferred-only" limit is lifted). Sections: `## Likes / responds well to`,
`## Dislikes / friction`, `## Wants — and in what manner`, `## Open questions`.
Entries carry observer + date: `- [2026-07-06, sentinel] wants totals before
breakdowns.` Agents are **obligated to act on it** at response time (an entry
read and violated is worse than none) and to **feed it** when they observe a
new preference — while still never reading it aloud or citing it. Orion may
merge identical observations across agents but never authors or rewords them.

**`projects.md` — the dated ledger.** One `## Project Name` section per
project with a status line and reverse-chronological dated entries:

```
## Project Prowler
Status: DESIGNED, not yet implemented (for OSTİM Technical University)
- [2026-07-06] …
```

When a project ships, dies, or is abandoned, Orion moves its whole section to
`history.md` under `## Completed / Retired Projects` — `projects.md` holds
**active and paused** work only.

**`social.md` — the people registry.** [R1] One `## Person Name` section per
person important enough to track. Each section is two blocks: a **Who** block
(who they are and their context to the owner — a miniature owner.md for that
person, updated in place as understanding improves) and an append-only
**Events** log (timestamped, newest first). Facts about a *person* route here
even when they touch the owner's plans; the owner-side consequence (e.g.
"cutting weight for the wedding") goes to `current.md`, cross-referenced. Orion
folds long event logs up into the Who block or year summaries; a person who
leaves the owner's life is demoted whole to `history.md` `## People`.

**`sessions.md` — gym log, Atomix's pen.** Only Atomix writes entries; other
agents read. Entry format:

```
## 2026-07-06 — Push (day 4/6)
- lifts / notes / bodyweight if measured
```

Orion compresses: entries older than 4 weeks collapse into one-line weekly
summaries; older than 12 weeks into monthly trend lines. Raw detail is never
carried forever — the *trend* is the durable asset. Program-level facts
("6 days/week, cutting for the wedding") live in `current.md`, not here.

**`history.md` — the Mark VI-era ledger.** [R1] Things that began AND ended
during Mark VI's watch (since 2026-05) and no longer apply — organized by theme
(`## Employment`, `## Completed / Retired Projects`, `## Past States`,
`## People`). Entries arrive **only by demotion** and always carry their active
date range. Pre-Mark-VI context does **not** belong here — that is `owner.md`
(see the epoch test in §2.3). Nothing in `/memories` is ever hard-deleted; it
is demoted here. This is the one file agents never need to consult to act — it
stops stale facts masquerading as current ones and answers "when did X?"

### 2.3 Routing decision tree (the boundary law)

A new fact lands in **exactly one** file:

1. Is it about **another person**? → `social.md` (owner-side consequence, if
   any, additionally to `current.md`).
2. Is it a **gym session**? → `sessions.md` (Atomix only).
3. [R1] Is it a fact about his life **before Mark VI existed** (biography,
   formative context), or a correction to that record? → `owner.md` (updated in
   place). Identity constants (name, address forms) live here too.
4. [R1] Is it an **observation about his preferences** — likes, dislikes, wants
   and in what manner, whether **stated or inferred**? → `dossier.md`.
5. Is it about a **project's** state or progress? → `projects.md`.
6. Is it an **active state** of his life right now? → `current.md`.
7. Did something **stop being true**? [R1] Apply the **epoch test**: a state
   that began and ended during Mark VI's watch → demote to `history.md` (with
   date range); newly-learned pre-Mark-VI context → **update `owner.md`**, not a
   demotion. Correct `current.md` in the same operation either way.

**Conflict rule:** `current.md` outranks every other file for the present
tense. If any file contradicts it, the other file is wrong — fix it, don't
average it.

---

## 3. Orion — the Custodian Agent

### 3.1 Identity

| Field | Value |
|---|---|
| `agent_id` | `orion` |
| Domain | Mark VI maintenance — memory custodian, system hygiene, host operations |
| Profile file | `app/profiles/orion.py` (Rule 10 — identity only here) |
| Character | The archivist/quartermaster of the roster. Terse, procedural, zero flair. Reports in changelogs, not prose. |
| `dispatch_target` | `True` — other agents may hand him cleanup work |
| Chat | Standard `POST /chat/orion` + appears in the roster UI like any other agent. No special routing — the existing generic `agent_id` path covers him. |
| Models | Sonnet when the owner talks to him; Haiku for all background passes — the existing `allocate_model()` policy already yields this (user→sonnet, n8n/agent→haiku). No new mechanism. |

### 3.2 Tool allowlist

Orion's `tool_allowlist` (Rule 5 — declared in the profile, enforced by the
registry):

- **`memory`** — full command set; he is the only agent expected to use
  `delete` (and only as the tail end of a demotion).
- **`system_ops`** — host computer operation (see 3.5).
- **`recall_conversations` / `search_history`** — to verify a fact against
  what was actually said before moving or merging it.
- **`notifications`** — to push an audit digest when he changed something
  structural.
- **No** research/web tools, no document generation, no dispatch initiation
  except replies. He maintains; he does not investigate the outside world.

### 3.3 The nightly audit (his core loop)

n8n is the sole scheduler (unchanged). A nightly workflow fires:

```
POST /trigger/orion
{"type": "cron", "job": "memory_audit", "output_mode": "silent"}
```

The audit pass, in order:

1. **Boundary sweep** — walk every canonical file against the routing tree
   (§2.3); migrate misfiled facts; merge stray non-canonical files into the
   canonical set and delete them.
2. **Demotion pass** — anything in `current.md`/`projects.md` that evidence
   (session log, recent conversations) shows has ended → `history.md` with
   date range.
3. **Dedup & normalize** — collapse duplicate facts (keep the newer,
   better-dated one), normalize timestamps to `YYYY-MM-DD`, convert relative
   dates ("next month") to absolute.
4. **Compression** — `sessions.md` weekly/monthly rollups (§2.2), `log.md`
   trim to its bound.
5. **Snapshot refresh** — the current.md and dossier refresh passes that
   today run as anonymous background jobs in `services/memory.py` become
   sub-steps of Orion's audit. Orion is the *identity that owns maintenance*;
   background jobs stop being nobody's responsibility. (`generate_title` and
   the per-turn `update_session_log` stay as plain BackgroundTasks — they are
   plumbing, not curation.)
6. **Audit report** — one dated entry appended to `/memories/.audit/log.md`
   (dot-prefixed = system file, hidden from the injection set and from the
   canonical-file rule): what moved, what merged, what was demoted. If a
   *structural* change occurred (file merged/section moved), also emit a push
   notification digest via `output_mode` escalation rules in the n8n payload.

### 3.4 Guardrails (what Orion may never do)

- **Never fabricate.** Orion moves, merges, timestamps, and compresses
  existing text. He does not author new facts about the owner.
- **Never hard-delete owner data.** Deletion is only the final step of a
  demotion whose content already landed in `history.md` (or `.audit/` for
  system debris). "Nothing is deleted, only demoted."
- **Never revert an owner commit** (§4). Owner edits are ground truth; if one
  breaks a boundary rule, Orion *re-files* the content per the routing tree
  but preserves every word, and notes it in the audit report.
- **Never rewrite `dossier.md` content** — he may re-order and dedupe it, but
  behavioural inference belongs to the agents who observed it.

### 3.5 Computer operation (`system_ops`)

The owner wants Orion able to "operate the computer." Scoped design:

- A Tier-1 skill, `app/skills/system_ops.py`, allowlisted **only to Orion**:
  shell execution, file read/write, and process/service inspection on the
  host Mark VI runs on. This covers his real maintenance duties — log
  rotation, `/tmp/speda_outputs` inspection, disk/health checks, DB vacuum
  triggers — and makes him genuinely useful when the owner says "Orion, check
  why the API container is eating RAM."
- Guardrail set inside the skill (not the prompt): command allow/deny
  patterns, working-directory jail for writes outside `/memories` and
  `/tmp/speda_outputs`, hard timeout, and full command+output logging to
  `.audit/ops.md` with `request_id`.
- Per Rule 11, the tool description spells out what it does, when to use it,
  when NOT to (never for research, never on user data files), and what it
  returns.
- If desktop-level GUI control is later wanted, that is an Optimus-class
  concern (external peer on the owner's machine), **not** a server-side
  skill. Out of scope here; noting it so nobody wedges it into `system_ops`.

---

## 4. Owner Commits from the Systems Board

The DATA_BANKS // KNOWLEDGE panel goes from read-only viewer to editor.

### 4.1 API

`routers/memory.py` grows one write endpoint (router stays logic-free —
persistence goes through a small service):

```
PUT /memory/files
{ "path": "/memories/current.md",
  "content": "<full new content>",
  "expected_updated_at": "<ISO timestamp from the GET>" }
```

- **Optimistic concurrency:** if `updated_at` in the DB no longer matches
  `expected_updated_at`, return **409** with the fresh copy — an agent wrote
  mid-edit; the panel re-diffs and the owner re-commits. No last-write-wins
  clobbering, ever.
- Standard `X-API-Key` auth (Rule 12). Path must be inside the canonical set
  or `.audit/` is rejected — the owner edits memory, not system trails.

### 4.2 Revision trail

New model `MemoryRevision` (`app/models/memory_revision.py`): `id`, `path`,
`author` (`"owner"` | agent_id | `"orion"`), `before`, `after`, `created_at`,
`request_id`. **Every** write path records one — the memory skill, Orion's
audit, and owner commits alike. This gives:

- Rollback of any single edit from the sys panel.
- Orion's morning audit a precise diff of what changed overnight and by whom.
- An answer to "who wrote this and when?" that doesn't depend on prose
  timestamps inside the files.

### 4.3 Precedence

`author="owner"` revisions are **ground truth**. Agents and Orion may re-file
owner-written content into the correct file per §2.3, but may not alter or
drop its substance. If an owner edit contradicts agent-written memory, the
agent-written fact is the one demoted.

### 4.4 Frontend (heartbreaker)

Edit mode on the knowledge panel: textarea over the fetched content, diff
preview, **Commit** button carrying `expected_updated_at`, 409 → show fresh
diff and ask to reapply. A per-file revision list with one-click restore
(restore = a new owner revision, never a history rewrite).

---

## 5. Prompt Protocol Changes

`prompts/core/08_memory.md` is rewritten to carry: the eight-file table, the
per-file contracts (§2.2, condensed), the routing decision tree verbatim, the
conflict rule ("current.md outranks everything for the present tense"), and
one new clause:

> **You are not the janitor.** Fix a misfiled fact if it blocks the task at
> hand; otherwise leave hygiene to Orion's nightly audit. Your job in-turn is
> to *file new facts correctly the first time* using the routing tree.

This reverses v1's "every agent polices every file" instruction — that
diffusion of responsibility is exactly what let the files rot. One custodian,
seven writers who follow the law when writing.

Orion's own prompt directory (`prompts/agents/orion/`) carries the full audit
procedure (§3.3) and guardrails (§3.4) as his identity documents.

---

## 6. Implementation Order (when green-lit)

| Step | Work | Depends on |
|---|---|---|
| 1 | `MemoryRevision` model + hook into `skills/memory.py` writes | — |
| 2 | Seed/migrate: create `social.md`, `sessions.md`; Orion-style one-time boundary sweep of existing content into the canonical set | 1 |
| 3 | Rewrite `08_memory.md` per §5 | 2 |
| 4 | `PUT /memory/files` + panel edit mode | 1 |
| 5 | `app/profiles/orion.py` + `prompts/agents/orion/` + roster UI entry | 3 |
| 6 | `skills/system_ops.py` (Orion-only allowlist) | 5 |
| 7 | Move current/dossier refresh from anonymous tasks into Orion's audit; n8n nightly `memory_audit` workflow | 5 |

Each step is independently shippable; the file law (steps 1–3) delivers value
before Orion exists, and Orion delivers value before `system_ops` does.
