# Mark VI Memory Architecture — v4 ("Schema'd Documents")

Status: **PROPOSED** — designed by reading all fourteen memory files end to end,
not from a taxonomy document. Supersedes v3's central claim (that every surface
can be derived from atomic observations) and keeps v3's record, provenance and
recall in a narrower, correct role.

---

## 1. The Evidence

Every failure below was found by reading the owner's real memory. None of them is
hypothetical, and none of them was detected by anything in the system.

**1. A corrupted generation was written straight into memory and stayed.**
`academic.md`, sitting there since 2026-07-20:

```
} аиҳабыലയാള񎢄】【。】【”】【♀♀♀♀♀♀assistant to=functions.memory  大发官网json error? Need retry correct.{
```

A leaked function-call preamble, mojibake across four scripts, and Chinese
gambling spam. A model failed mid-generation and the failure was persisted as
knowledge. Nothing validated it, nothing flagged it, nobody noticed for three
weeks.

**2. Content written above the document title.** `current.md` opens with a
paragraph about a Diyanet warning, *then* `# Current — what's active right now`.
An `insert` at line 0 landed above the H1. The file is malformed markdown.

**3. Two documents concatenated in one file.** `academic.md` contains a second
H1 (`# Academic Materials — Siberay Bootcamp Theme`) at line 59. It is really
four documents: KPSS prep, the 2026–27 academic calendar, the Siberay course-book
theme spec, and an Erasmus evaluation.

**4. Content bleeding across sections.** In the same file, Erasmus evaluation
bullets sit under the `## HTML Template` heading, between two lines about
template placeholders. Whoever wrote them used `str_replace` on a nearby anchor.

**5. Heading levels contradict each other.** In `social.md` every person is
`### Name` — except `## Semra Bayrak`, who is a level up. `## Siberay Board` is a
category, but contains both a flat name list and a `### Pınar Uzun` person entry.
This is exactly what made the v3 seed read "Professional" as a person.

**6. Stale facts asserted as current.** `ops.md` states "**No Postgres** — Igor
runs on SQLite. Postgres container is dormant." The server is running
`speda-postgres-1 Up 2 hours (healthy)`. The same file routes an agent to
`packages/api/`, a directory renamed to `packages/igor/` months ago, and to
`/app/data/speda.db`.

**7. No file had a declared shape, so no write could be checked against one.**

These are not seven bugs. They are one absence: **memory is a database with no
schema, no validation, no constraints and no integrity checks.** Every failure
above is a class that databases solved decades ago.

---

## 2. The Four Shapes

Profiling every file by its own content — heading depth, table density, bullet
stamping, prose ratio, and which agent actually writes it — the store sorts into
four kinds. This is discovered, not invented, and it is what v3 got wrong by
modelling only the fourth.

### 2.1 NARRATIVE — prose chapters, read whole

`owner.md` (15 KB) — *Origins*, *The Uludağ Years*, *The Istanbul Summer (2024)*,
*The Devastation and Ankara Arrival*, *The Web of Lies*, *Kurtulus Park and the
Turn*. A life story with an `## Employment History` register at the end. Written
by orion (12), speda (4), owner (2).

Not decomposable. Injected whole. The prose IS the artifact.

### 2.2 REGISTRY — one section per entity, fixed inner schema

| File | Index | Inner schema |
|---|---|---|
| `social.md` (27 KB) | `## Category` → `### Person` | `**Who:**` paragraph, `**Events:**` dated bullets, optional `**Core pattern:**` |
| `projects.md` (38 KB) | `## Project` | `Status:` / `Stack:` lines, then `### Architecture / Features / Tech Stack / Team` |
| `cybersec.md` (0.7 KB) | `## Aspect` | tracks / progress table / resources |

Two levels in `social.md` — category then person — which is precisely the level
v3 misread. The spec makes it explicit so it cannot be misread again.

### 2.3 LEDGER — indexed by time or topic, tables carry the payload

| File | Index | Payload |
|---|---|---|
| `finance.md` (15 KB) | `## YYYY-MM` → `### Incomes / Expenses / Debts` | 71 table rows, fixed columns per section |
| `wellness.md` (26 KB) | `## 1. DIRECTIVES` / `## 2. PROFILE` / `## 5. LOG` → `### YYYY-MM-DD` | protocol prose + dated session bullets |
| `kpss.md` (3.5 KB) | `## Subject` → `### Topic` | 83 rows: topic / expected questions / net average |
| `academic.md` (7 KB) | term → calendar | dated bullets |
| `ops.md` (5.8 KB) | `## Part 1 Runbook` / `## Part 2 Action Log` → `### YYYY-MM-DD` | tables + dated entries |

**This is the shape v3 destroyed and the shape the owner most needs.** A ledger is
already an index: "what did I spend in July" is a jump to `## 2026-07`, not a
search. Shredding 71 table rows into 56 sentences kept every figure and lost every
relationship between them — which month, which statement, which repayment
schedule.

### 2.4 OBSERVATION LIST — flat, attributed, atomic

`dossier.md`, `current.md`, `history.md`. Independent dated facts with no
structure between them.

Even here v3 over-reached: `dossier.md`'s six sections include
**`## Explicit prohibitions`** — hard behavioural rules ("Do NOT be a yes-man…
agreeing with a known falsehood is worse than being wrong") — and
**`## Trusted contacts & services`**, which is a registry, not a preference. v3
rendered all six into one heading called `General`. A prohibition became
indistinguishable from a mild preference, in the file that governs how every
agent answers.

---

## 3. The Design

### 3.1 Every document declares its grammar

One registry in code — `app/services/memory_spec.py` — one `DocumentSpec` per
file:

```python
DocumentSpec(
    path         = "/memories/finance.md",
    kind         = LEDGER,
    owner_agent  = "sentinel",          # the only agent that may write it
    injected     = False,
    index        = SectionIndex(level=2, pattern=r"^\d{4}-\d{2}$", order="desc"),
    subsections  = {"Incomes":  Table("Date", "Source", "Amount (TL)", "Notes"),
                    "Expenses": Table("Date", "Item",   "Amount (TL)", "Notes"),
                    "Debts":    Table("Debt", "Amount (TL)", "Status", "Notes")},
    preamble     = ("Monthly structure", "Notes", "Scholarships & Loans (Reference)"),
)
```

The spec is the contract. It says what sections may exist, at what level, in what
order, and what a valid entry inside each looks like.

### 3.2 Writes go through the grammar, never through text

`str_replace` on a 15 KB ledger is how a July expense lands under June, and how
Erasmus notes end up under an HTML-template heading. It is replaced by four verbs,
each valid only for its kind:

| Verb | Kind | Guarantees |
|---|---|---|
| `ledger_append(file, key, section, row)` | ledger | the row lands under the right key; columns match the spec; a new key is inserted in index order |
| `registry_upsert(file, path, field, value)` | registry | the entity exists at the right heading level with its full field set; no stray top-level text |
| `record_observation(...)` | observations | the v3 evidence ladder, unchanged |
| `narrative_revise(file, chapter, text)` | narrative | one chapter at a time, orion only |

Every write is **parsed against the spec before it is committed**. The corrupted
`academic.md` line is not a valid entry in any section of any spec — it would have
been rejected at the boundary instead of persisted for three weeks.

An agent may write only its own file (`owner_agent`), which is already how the
store behaves in practice — the revision trail shows `academic.md` written by
ultron 19 times and nobody else, `wellness.md` by atomix 11 of 12, `finance.md` by
sentinel 23 of 32 — it simply was not a rule.

### 3.3 The verifier — the mechanism that keeps it correct

`app/services/memory_verify.py`. Parses every file against its spec and reports
violations. This is the piece whose absence allowed all seven defects.

| Check | Would have caught |
|---|---|
| document parses as its declared kind | #1 corrupted generation |
| exactly one H1, nothing above it | #2 content above the title |
| no second H1; sections match the spec's set | #3 concatenated documents |
| every block belongs to a declared section | #4 Erasmus under HTML Template |
| heading levels match the spec | #5 `## Semra Bayrak` among `###` peers |
| declared cross-references resolve (paths, container names, file names) | #6 `packages/api/`, "no Postgres" |
| no control characters, no mixed-script runs, no tool-call fragments | #1 again, structurally |

It runs in two places:

- **on write** — blocking. A write that would introduce a violation is refused,
  with the rule named and the fix stated, exactly as the current schema gate does.
- **nightly, in Orion's audit** — reporting. Pre-existing violations are listed
  for repair; they never block an unrelated write (the delta rule that already
  makes the schema gate safe to run against a live store).

### 3.4 Repair, never regenerate

The v3 lesson, stated as a rule: **when the verifier finds a violation, the fix
edits that violation and nothing else.** Orion does not rewrite the file, and
nothing renders it from scratch. A malformed heading gets its level corrected; a
stale path gets updated; a corrupted line gets deleted. The surrounding document —
which the owner and eight agents built over months — is not touched.

### 3.5 Retrieval becomes direct where the structure allows

The owner's actual request: fast, easy, correct access.

| Question | v3 | v4 |
|---|---|---|
| "What did I spend in July?" | semantic search over 56 sentences | `memory_get("finance.md", "2026-07")` — the section itself |
| "My KYK repayment schedule?" | hope it was extracted | direct table read |
| "What did I lift on 5 August?" | search | `memory_get("wellness.md", "2026-08-05")` |
| "Who is Hakan Eren?" | search | `memory_get("social.md", "Professional/Hakan Eren")` |
| "Have we discussed X?" | search | unchanged — `search_memory` |

Exact questions stop costing an embedding call and stop being approximate. The
spec also means an agent can list a file's sections without reading the file.

### 3.6 The observation record survives — as the index, not the source

Everything v3 built keeps earning its place: `record_observation`, the evidence
ladder, provenance, supersession, validity, `search_memory`, surprisal,
near-duplicate detection, the archivist, the durable queue, the revision trail.

What changes is its **role**. It is no longer what the files are rendered from. It
is the **cross-cutting semantic index over them** — documents keep their
structure, and every fact inside them stays searchable by meaning.

---

## 4. Migration

| Step | Action |
|---|---|
| 1 | **Derivation off.** `RENDERED_FILES = ()`, `COMPOSED_FILES = ()`. Done. |
| 2 | Restore `dossier`, `finance`, `projects`, `sessions`, `social`, `history`, `current` from their last pre-render revision. |
| 3 | Delete `sessions.md`; `wellness.md` is its successor and Atomix's source. |
| 4 | Write the specs for all thirteen remaining files. |
| 5 | Ship the verifier; run it in report-only mode and fix what it finds (the seven above, plus whatever else). |
| 6 | Ship the four write verbs; retire `str_replace` on spec'd files. |
| 7 | Split `academic.md` into `academic.md` (calendar + standing) and `kpss.md` (already separate) + `siberay.md` (course-book theme). |

Step 1 must precede step 2 — restoring a file that is still rendered is
overwritten on the next turn.

---

## 5. The Trade

**Costs.** One more concept (document *kind*), which the store already had
implicitly. The write surface grows from one tool to four, each narrower and
harder to misuse than the `str_replace` it replaces. Specs must be written and
maintained — thirteen of them, once.

**Buys.** Ledgers, programs and registries keep the structure that makes them
usable. Exact questions get exact answers with no model call. An agent physically
cannot write outside its own file or its file's shape. A corrupted generation
cannot enter memory. And the seven defects above become seven checks that run
every night instead of seven things nobody was looking for.
