# Memory Architecture — Revision R1 ("Semantics Correction")

Status: **PLAN** — owner-ordered revision of four file contracts in the v2
architecture (docs/MEMORY_ARCHITECTURE.md). Planning only; no code has been
touched for R1. When green-lit, this document is the implementation contract.

---

## 0. What the owner corrected, in his own terms

1. **dossier.md** — it is the agents' *observations about him, formed as they
   talk to him* — and agents must **actually learn from it**, not merely hold it.
2. **owner.md** — *who he is and his biography*: everything that happened
   **before Mark VI existed**. Historical context so an agent knows the man it
   serves. When new information about that past arrives, the file is **updated**.
3. **history.md** — everything that happened **while using Mark VI** (and no
   longer applies today).
4. **social.md** — a person important enough to be there gets a **section**:
   first *who they are and their context to the owner*, then a **timestamped
   event log** of things concerning them.

The deep change hiding in (2)+(3): the boundary between `owner.md` and
`history.md` is not "identity vs past" — it is **temporal**. One epoch line
divides them: **the birth of Mark VI**. Everything before it that shaped him →
`owner.md`. Everything after it that has since ended → `history.md`. v2 got this
half right in the design doc and wrong in the shipped prompt, which framed
owner.md as "identity + address forms" and let history.md absorb *any* past.

---

## 1. Revised file contracts

Only the four corrected files are restated here; current.md, projects.md,
sessions.md, and log.md are unchanged from v2.

### 1.1 `owner.md` — The Prior (pre-Mark-VI)

**Answers: "Who is he, and what shaped him before I existed?"**

- Two-part structure:
  1. **Identity header** — name, codename, standard address (sir / Efendim),
     and the handful of identity constants agents need every turn. These stay
     here because they are *who he is*, not preferences about behaviour.
  2. **Biography** — his life from birth to the creation of Mark VI: education,
     places, formative work, family background, the events that explain him.
     Organized by theme or era, not as a diary.
- **Living portrait of a fixed past.** When the owner reveals or corrects a
  pre-Mark-VI fact, the file is updated **in place** — never appended as a
  contradictory second entry, never demoted anywhere. The past doesn't expire;
  our record of it just gets more accurate.
- What moves OUT under R1: any explicit *behavioural preference* framing
  ("prefers concise responses", "no padding") migrates to `dossier.md` (§1.3).
  Address forms stay (identity constants, not preferences).

### 1.2 `history.md` — The Mark VI Era Ledger

**Answers: "What happened during Mark VI's watch that no longer applies?"**

- **Epoch-bounded**: contains ONLY things that occurred *after Mark VI's birth*
  (2026-05, per the system's own founding record) and have since ended, paused
  indefinitely, or stopped being true. It is the sediment layer *under*
  current.md — never a general-purpose past.
- Populated **only by demotion** from current.md / projects.md / social.md,
  each entry carrying its active date range, organized by theme
  (`## Employment`, `## Completed / Retired Projects`, `## Past States`, …).
- **The epoch test at demotion time** (new rule): when a fact is being demoted
  or re-filed, ask *"was this true before Mark VI existed?"*
  - Yes, and it's biography-grade context → it belongs in `owner.md`.
  - No — it lived and died during the Mark VI era → `history.md`.
  This replaces v2's undifferentiated "everything that stopped being true →
  history.md."

### 1.3 `dossier.md` — Observations the Agents Must Learn From

**Answers: "What have we observed about what he likes, dislikes, and wants —
and in what manner he wants it?"**

- **Broadened source.** v2 restricted the dossier to *inferences from
  reactions, never stated facts*. R1 widens it to **observations formed while
  talking to him** — both inferred patterns AND explicitly stated preferences
  about how he wants things done ("give me totals before breakdowns", visible
  frustration at padding, what he engages with vs ignores). The distinction
  that matters is no longer stated-vs-inferred; it is **about-his-preferences
  vs about-his-life** (life facts route to current/owner/history as ever).
- **Structure** (replaces the v2 four-section template):
  - `## Likes / responds well to`
  - `## Dislikes / friction`
  - `## Wants — and in what manner` (the "do I want what, in what way" section:
    task-shaped standing observations, e.g. *"wants plans as numbered concrete
    steps, not prose"*)
  - `## Open questions`
  - Every entry keeps attribution: `- [YYYY-MM-DD, agent_id] observation`.
- **The learning mandate — the actual point of the file.** The prompt language
  changes from v2's passive "act on it silently, never cite it" to an active
  obligation, applied at response time:
  > Before you respond, check your behaviour against the dossier. If it records
  > that he dislikes something, do not do that thing. If it records how he wants
  > a kind of output, produce it that way without being re-told. A dossier
  > entry you read and then violate is worse than no dossier at all.
  The "never read it aloud / never cite it" rule **stays** — learning from it is
  mandatory; quoting it at him remains forbidden.
- **Write duty (both directions).** Agents don't only apply it — they feed it:
  when the owner corrects an agent, praises a format, or states a standing
  preference mid-conversation, the observing agent files it here (attributed,
  dated) in that same session. This is what "as they talk to me" means
  operationally.
- Orion's guardrail is *refined*, not removed: he still never rewrites
  observation content (it belongs to the agent that observed it), but he may —
  in addition to v2's re-order/dedupe — **merge duplicate observations across
  agents** into one entry preserving the earliest date and listing the agents
  that independently observed it (convergent observations are the strongest
  signal in the file, and the merge should show that strength).

### 1.4 `social.md` — The People Registry, Hardened

**Answers: "Who matters to him — who ARE they to him — and what's the latest?"**

Per-person section schema becomes two explicit blocks (v2 had only a one-line
relationship header):

```markdown
## <Person's name>

**Who:** <who they are and their context to the owner — relation, role in his
life, standing facts about them worth knowing: 2–6 lines, updated in place as
understanding improves, like a miniature owner.md for that person>

**Events:**
- [YYYY-MM-DD] <thing that happened concerning them> (newest first)
```

- **Admission bar unchanged**: "important enough to get there." Passing mentions
  don't create sections; an agent creates a section when a person clearly
  recurs in the owner's life.
- **Who-block is living** (updated in place, corrections overwrite); the
  **Events log is append-only** with timestamps, newest first.
- Routing unchanged from v2: facts about a person land here; the owner-side
  consequence goes to current.md with a cross-reference.
- Orion compression rule (new, mirrors sessions.md): when a person's event log
  grows long, old events collapse into the Who-block as durable context or into
  one-line year summaries — the *relationship understanding* is the durable
  asset, not every timestamp. A person who has left the owner's life is demoted
  whole — section moved to `history.md` under `## People` with date range.

---

## 2. Routing tree — R1 deltas

The v2 seven-step tree survives with three amendments:

- **Step 3 (rewritten):** ~~"explicit rule about identity/address/communication
  → owner.md"~~ → **"A fact about his life BEFORE Mark VI existed (biography,
  formative context), or a correction to that record? → owner.md."**
- **Step 4 (rewritten):** ~~"an inference he did not state → dossier.md"~~ →
  **"An observation about his preferences — what he likes, dislikes, or wants
  and in what manner, whether stated or inferred? → dossier.md."**
- **Step 7 (amended):** demotion now carries the **epoch test** — ended
  Mark-VI-era states → history.md; newly-learned pre-Mark-VI context →
  owner.md (as an update, not a demotion).

The governing rule is untouched: **current.md outranks everything for the
present tense.**

---

## 3. Change list — every artifact R1 touches

| # | Artifact | Change |
|---|----------|--------|
| 1 | `docs/MEMORY_ARCHITECTURE.md` §2.2, §2.3 | Amend the four file contracts + routing tree per §1–§2 above; add the epoch line to the taxonomy table. Mark v2 text superseded by R1 where they conflict. |
| 2 | `prompts/core/08_memory.md` | Table rows for owner/history/dossier/social reworded; routing steps 3/4/7 replaced; **new "LEARN FROM THE DOSSIER" clause** (the response-time obligation + the write duty, §1.3); epoch test added to the demotion rule. |
| 3 | `app/skills/memory.py` `INITIAL_FILES` | owner.md template gains `## Biography (pre-Mark VI)` scaffold; dossier.md template → the four R1 sections with the attribution format; social.md template → the Who/Events schema with one worked example; history.md template header reframed "Mark VI era — demotions only". (Templates seed NEW deployments only — live data moves via §4.) |
| 4 | `app/skills/skill_docs/memory/SKILL.md` | File-list descriptions updated to R1 semantics; the social.md example updated to the Who/Events schema. |
| 5 | `prompts/agents/orion/02_audit.md` | Pass 2 (Demotion) gains the epoch fork; Pass 1 boundary definitions updated; new sub-rule in Pass 3 for cross-agent dossier merge (§1.3); Pass 4 gains social.md compression + whole-person demotion (§1.4). |
| 6 | `prompts/agents/orion/01_identity.md` | Guardrail wording updated: "never rewrite dossier content" → "never author observations; merging duplicates across agents is permitted per the audit procedure." |
| 7 | `app/services/memory.py` `_DOSSIER_PROMPT` | Rewrite: drop "inferred from how he REACTS, not from facts he states"; new instruction = observations from conversations including stated preferences, four R1 sections, attribution preserved, no invention. Section names in the output contract updated to match §1.3. |
| 8 | `app/services/memory_store.py` `CANONICAL_FILES` | Description strings only (the one-question-per-file text shown nowhere critical, but keep it truthful). No schema change. |
| 9 | Frontend | **No changes.** The board renders whatever the files contain; edit/history/restore are schema-agnostic. |

No database changes. No new endpoints. No new skills. R1 is a semantics
revision — prompts, templates, and two service strings.

## 4. Migration of live data (one-time, content-level)

Existing deployments already have populated files under the old semantics.
Template changes don't retroactively apply (`ensure_seeded` only fills gaps), so
the content itself must be re-filed once. Two viable paths:

**Path A — Orion's first R1 audit does it (recommended).** After the prompt
changes deploy, trigger `POST /trigger/orion` once with an intent naming this
document: *"Run a full R1 migration audit: apply the revised file contracts —
move pre-Mark-VI content from history.md into owner.md's Biography; move
behavioural-preference lines from owner.md into dossier.md; restructure
social.md sections to the Who/Events schema; then run your normal passes."*
Every move is revision-logged, so anything he mis-files is one restore away.

**Path B — the owner does it by hand** from the systems board's new edit mode,
with Orion's audit only verifying afterwards. Slower, but zero model judgement
involved. Choose this if the current files are small enough to re-file in ten
minutes.

Either way the migration is **content moved via the normal write paths** —
never a script mutating rows directly, so the revision trail stays complete.

## 5. Decisions taken in this plan (flag if you disagree)

1. **Address forms stay in owner.md** ("sir / Efendim" is an identity constant,
   not a behavioural preference). Only preference-shaped lines migrate to the
   dossier.
2. **Epoch date = 2026-05** (Mark VI's founding, per the system's own project
   record). Stated explicitly in the prompts so agents can apply the epoch test
   mechanically.
3. **The dossier keeps its "never cite aloud" rule** despite the broadened
   learning mandate — the file drives behaviour, it is not conversational
   material.
4. **Stated standing instructions now live in the dossier**, not owner.md. This
   is the direct consequence of owner.md becoming purely biographical; the
   dossier's "Wants — and in what manner" section is their new home.

## 6. Implementation order (when green-lit)

1. Prompt + template changes (change-list items 2–6) — one commit, they only
   affect new turns.
2. Service strings (items 7–8) — same commit or the next.
3. Update the architecture doc (item 1) so the contract matches what shipped.
4. Run the Path-A migration trigger; owner reviews the diff from the board's
   revision history; restore anything mis-filed.
