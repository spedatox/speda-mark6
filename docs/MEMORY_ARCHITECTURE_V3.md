# Mark VI Memory Architecture — v3 ("One Record, Derived Surfaces")

Status: **IMPLEMENTED** (phases 1-6 built and tested; not yet run against
production data). Supersedes the write model of v2
(`docs/MEMORY_ARCHITECTURE.md`), whose file taxonomy and Orion charter survive in
the reduced form described in §7.

**Before the first deploy, in this order:**

1. `POST /admin/memory/reindex` — seeds the pre-v3 files into the record and
   derives the rest from conversation history. Long-running; call it from a
   terminal, not a UI that will time out.
2. `POST /admin/memory/shadow` — read `at_risk_facts`. Zero means the record
   reproduces everything the files held. Non-zero lists exactly what would be
   lost; record those facts before going further.
3. `POST /admin/memory/compose` — write owner.md and current.md from the record.
4. Import `scripts/n8n/memory_audit.json` if it is not already active.

---

## 1. What v2 Gets Wrong

v2 fixed v1's boundary rot with a strict taxonomy, a custodian, and (in the v3
amendment already shipped) structural validation and an observation store. Files
stopped being a free-for-all. But one thing stayed unchanged, and it is the
thing that still lets memory drift:

> **The markdown file is the primary record, and the observation is a copy of it.**

An agent that learns something writes a sentence into a file *and*, if it
remembers to, records an observation. Two write paths, two artifacts, no
mechanism that keeps them agreeing. Every failure mode v2 was written to prevent
survives in weaker form:

| v1 symptom | v2 answer | Why it still happens |
|---|---|---|
| Facts drift between files | Routing tree + schema gate | The gate checks *form*, not whether the fact was already filed elsewhere. Two agents can file one fact into two files and both writes are valid. |
| Nothing owns hygiene | Orion's nightly audit | Reconciling two sources every night is a permanent tax. A system that cannot drift needs no reconciler. |
| Stale facts look current | Demotion to `history.md` | Demotion is a *text move*. Text moves lose things — the fact, its date range, or its provenance, depending on which half of the edit succeeded. |
| Ad-hoc file types | Closed taxonomy | Enforced now. This one is genuinely fixed. |

And a fifth problem v2 never addressed: **the record cannot be rebuilt.** Because
the files hold content that exists nowhere else, re-indexing means destroying
hand-curated knowledge. A memory system you cannot rebuild is one you can only
ever patch.

---

## 2. The Principle

**One write path. Every durable fact enters as an observation. Every file is
derived from observations and never independently authored.**

```
        conversation ──┐
        owner edit ────┼──▶  observations  ──▶  rendered surfaces  ──▶  injected
        re-index ──────┘     (the record)      (/memories/*.md)          context
```

Three consequences, and they are the entire justification:

1. **Drift becomes impossible**, not merely detected. A file cannot disagree
   with the record because it *is* the record, formatted. Orion's boundary sweep
   and dedup passes stop existing — not because we stopped caring, but because
   there is nothing left for them to find.
2. **Rebuilding becomes safe and routine.** Files are disposable output. Wipe
   them, re-render, get byte-identical results. Re-index from raw history
   whenever the extraction gets better, without losing anything the owner wrote.
3. **The routing decision moves from write time to record time.** An agent stops
   answering "which of eight files does this go in?" mid-conversation — a
   judgement it makes badly under task pressure — and answers "what is this a
   fact *about*?", which it can answer from the sentence itself.

---

## 3. The Record

`observations`, extended from what shipped. New fields carry the three things
files were doing implicitly: **what a fact is about**, **when it was true**, and
**what replaced it**.

```python
# ── identity of the claim ────────────────────────────────────────────────
content: str                  # ONE fact, self-contained
level: str                    # explicit | deductive | inductive | contradiction
observer: str                 # agent_id, or "owner"

# ── what it is ABOUT — replaces the routing tree ─────────────────────────
subject: str                  # "owner" | "person:Zeynep" | "project:Prowler"
domain: str                   # biography | preference | state | project
                              #   | person | training | finance | event

# ── WHEN it was true — replaces demotion ─────────────────────────────────
valid_from: date | None       # when the fact started holding (None = always)
valid_until: date | None      # when it stopped (None = still true)
superseded_by: int | None     # the observation that replaced this one

# ── provenance (already shipped) ─────────────────────────────────────────
source_ids, premises, sources, pattern_type, confidence,
session_id, message_ids, reinforcement_count, embedding, deleted_at
```

`path` is **removed**. Which file a fact appears in is now computed from
`(subject, domain)`, so storing it would be a second, disagreeable answer to a
question that already has one.

### 3.1 Subject makes routing computable

v2's routing tree is seven questions an agent answers in prose. Six of them are
really one question — *what is this about?* — and the seventh is about time:

| v2 routing question | v3 |
|---|---|
| About another person? → social.md | `subject = "person:<name>"` |
| A gym session? → sessions.md | `domain = "training"` |
| Pre-Mark-VI biography? → owner.md | `domain = "biography"` |
| A preference? → dossier.md | `domain = "preference"` |
| A project's state? → projects.md | `subject = "project:<name>"` |
| An active life state? → current.md | `domain = "state"`, `valid_until = None` |
| Stopped being true? → history.md | `valid_until = <date>` |

The last row is the important one. **"Has it stopped being true" stops being a
file-location question and becomes a column.** No text is moved, so no move can
half-fail. `current.md` and `history.md` are the same query with the filter
flipped.

### 3.2 Supersession replaces overwriting

When a figure changes or a state ends, the old observation is not edited and not
deleted: it gets a `valid_until` and a `superseded_by` pointer to its
replacement. Two things fall out of this that v2 cannot do:

- **"What did he earn last year?"** is answerable. Today that figure was
  overwritten by `str_replace` and is gone.
- **A wrong correction is reversible.** Supersession is a link, not a
  destruction; unlinking restores the prior state exactly.

The conflict rule survives verbatim and gets sharper: *the present tense is
whatever has `valid_until IS NULL`*. Nothing can contradict it, because there is
no second place for the present tense to live.

---

## 4. The Surfaces

Files are output. Three kinds, distinguished by **how they are produced** —
which is the classification v2 was missing, because it classified files by what
they answer without ever saying who maintains them.

### 4.1 Rendered — pure function, no LLM

Deterministic assembly from a query. Byte-identical for identical input, so the
injected prompt block is stable by construction rather than by careful caching.

| File | Query |
|---|---|
| `dossier.md` | `domain=preference`, valid, grouped by `pattern_type`, attributed + dated |
| `sessions.md` | `domain=training`, newest first, compressed by age |
| `finance.md` | `domain=finance`, latest of each supersession chain |
| `projects.md` | `subject LIKE "project:%"`, valid, grouped by project |
| `social.md` | `subject LIKE "person:%"` — Who block from `domain=biography`, Events from `domain=event` |
| `history.md` | `valid_until IS NOT NULL`, grouped by domain, with date ranges |

Six of eight files stop being written by anyone. They cannot be misfiled,
duplicated, or left stale, because they are not stored — they are computed.

### 4.2 Composed — Orion writes prose, citing the record

Two files are genuinely narrative, and rendering them as bullet lists would make
them worse for the human who reads them:

- **`owner.md`** — the biography. Prose paragraphs over `domain=biography,
  subject=owner`, composed nightly. Every paragraph carries the observation ids
  behind it in an HTML comment, so a claim in the prose can always be traced.
- **`current.md`** — the present-tense snapshot. Composed from `domain=state,
  valid_until IS NULL`, because the *selection* of what matters right now is a
  judgement, not a filter.

Composition may only reword and select. It may not introduce a claim with no
observation behind it — the same no-fabrication guardrail Orion already has,
now mechanically checkable: every id cited must exist.

### 4.3 System trails — unchanged

`log.md` and `.audit/` are not owner knowledge and stay as they are.

---

## 5. The Owner's Write Path

§4.3 of v2 makes owner commits ground truth. That survives and gets stronger.

An owner edit from the systems board no longer writes a file — it **records
observations with `observer="owner"`**, which:

- outrank every agent observation on conflict (precedence is a column, not a
  convention);
- are **never regenerated by a re-index** — the re-indexer rebuilds agent
  observations and re-applies owner ones on top, untouched;
- carry the same revision trail (`MemoryRevision`) they do today.

Editing a *rendered* file directly is no longer meaningful, so the panel changes
shape: the owner edits **facts**, not markdown. That is a UI change and the
biggest single piece of work in this proposal (§10, phase 4).

Until that ships, the existing file editor keeps working against a compatibility
path: an owner file edit is diffed against the rendered output and the delta is
recorded as owner observations.

---

## 6. Re-indexing

`POST /admin/reindex-memory` — safe, idempotent, repeatable, because the only
thing it destroys is derived output.

```
1. PRESERVE   owner-authored observations are set aside, untouched.
2. SEED       (first run only) current file contents become observations, by
              TWO passes: a bullet parser that recovers structured entries with
              their `[date, agent]` stamps exactly, and a model pass that
              recovers everything the parser cannot see. The second pass is not
              optional — the pre-v3 files are prose-heavy (owner.md's whole
              biography, every `**Who:**` block) and a regex-only seed silently
              dropped all of it while the shadow report reported zero risk.
3. DERIVE     conversation history is walked in batches; one structured-output
              call per batch yields observations. Honcho's "minimal deriver":
              a single cheap call per batch, not an agentic loop.
4. RECONCILE  dedup + reinforcement as it goes; supersession chains built from
              the temporal ordering of the batches.
5. RESTORE    owner observations re-applied on top, winning every conflict.
6. RENDER     all six rendered surfaces regenerated; Orion composes the two
              narrative ones on his next audit.
```

Step 2 is what makes the FIRST run non-destructive, and it only ever runs once.
Every subsequent re-index is pure: better extraction prompt → re-run → better
memory, with zero risk to anything the owner wrote.

**Cost estimate.** One cheap-tier call per batch of history. At Honcho's default
batch sizing this is tens of calls for a normal history, not thousands — the
existing `history_indexer` does the same shape of work in "a couple of minutes"
by its own docstring.

---

## 7. What Orion Becomes

His job gets smaller and more honest. Four of his seven passes disappear because
the conditions they searched for cannot arise:

| Pass | v3 |
|---|---|
| 1. Boundary sweep | **Gone.** Files are rendered; misfiling is not expressible. |
| 2. Demotion | **Gone.** Ending a state is setting `valid_until`, done at record time. |
| 3. Dedup & normalise | **Gone** for exact/structural cases (write-time dedup + rendering). |
| 4. Compression | **Kept**, as a rendering rule (age-based rollup) rather than an edit. |
| 5. Snapshot refresh | **Kept and expanded** — this is now his main job: composing `owner.md` and `current.md` from the record. |
| 6. Consolidation | **Kept and sharpened** — surprisal-driven: derive from the novel, merge the near-duplicate, record contradictions. |
| 7. Report | **Kept.** |

He stops being a janitor and becomes what the charter always called him: the
archivist. The nightly turn spends its budget on reasoning about the record
instead of moving text between files.

---

## 8. What Recall Becomes

Unchanged in shape — the five-rung ladder stands — but two rungs get sharper:

- `search_memory` gains `as_of` (what was true on a date) and `subject` filters.
  "What did he think about X in June" becomes a query rather than an
  archaeology expedition through transcripts.
- Contradiction and supersession are first-class, so an agent asserting
  something stale can be told it is stale, with the observation that replaced it.

---

## 9. Invariants

The system holds these by construction, not by prompt:

1. Every durable fact has exactly one record and one home. There is nowhere else
   for it to be.
2. Nothing is destroyed. Ending is `valid_until`; replacing is `superseded_by`;
   removing is `deleted_at`. All three are reversible.
3. Every claim in a composed file cites observations that exist.
4. Owner observations outrank agent observations, always, and survive every
   rebuild.
5. Rendered files are a pure function of the record — regenerating them changes
   nothing.
6. The record can be rebuilt from raw history at any time without losing
   anything the owner authored.

---

## 10. Migration

Phased and reversible. Each phase ships and is useful alone; the system is never
in a half-state where memory is unreadable.

| Phase | Work | Risk | Status |
|---|---|---|---|
| 1 | Schema: add `subject`, `domain`, `valid_from`, `valid_until`, `superseded_by`; drop `path`. Additive migration guarded per column. | None | **Done** |
| 2 | `record_observation` takes and requires the new fields; subject normalisation and convergence; supersession; `as_of` / `subject` / `domain` filters on recall. Files still written as today — both paths live. | Low | **Done** |
| 3 | Renderers for the six rendered files (`app/services/memory_render.py`), plus `compare_to_stored` — **read-only shadow mode**, so we learn whether derivation reproduces the files before trusting it. | None | **Done** (not yet invoked from anywhere) |
| 4 | Seed (`memory_reindex.seed_from_files`) + re-index from history. Six files flipped to rendered; `memory_schema` refuses hand edits to them and names `record_observation` instead. Rendering joins the post-turn queue. | **The real one** — see §11 | **Done** |
| 5 | `GET/POST /memory/observations`, `/end`, `DELETE` — the owner edits facts. `is_owner_editable` returns False for every derived file, so the panel greys the textarea out. | Medium | **Backend done**, desktop UI outstanding |
| 6 | Orion's charter cut from seven passes to five; `08_memory.md` rewritten around subject/domain/validity; the dossier and current.md prompts deleted from `services/memory.py`. | Low | **Done** |

**Where the system actually is.** The write path is flipped and the invariants in
§9 hold in code, verified by `test_v3`. Two things remain before this is true of
the running system:

- **The migration has not been run.** Until `POST /admin/memory/reindex` is
  called against production data, the record is empty and the derived files will
  render empty. Run the shadow report before trusting the flip.
- **The desktop panel still shows textareas.** The backend refuses those edits
  and reports `editable: false`, so nothing is silently lost — but until
  `packages/heartbreaker` is updated to drive `/memory/observations`, the owner's
  only way to correct a fact is the API directly.

Phase 3 is deliberately a dress rehearsal. If rendering cannot reproduce the
current files to the owner's satisfaction, we learn it while both systems are
still running and nothing has been flipped.

---

## 11. The Trade

**What this costs:**

- A real re-index, and a first-run seed step whose parsing of existing files
  will not be perfect. Phase 3's shadow mode is the mitigation, not a guarantee.
- The systems board changes shape: editing facts, not markdown. Some owners
  prefer editing prose directly; that option goes away for six of eight files.
- Composed files depend on Orion running. If his audit is broken, `owner.md` and
  `current.md` go stale — where today they are static text that at least stays
  put. (Mitigated: the previous composition is kept until a new one succeeds.)
- More schema, more code, one more concept (supersession) for future work to
  understand.

**What it buys:**

- Drift becomes impossible rather than nightly-corrected.
- The record becomes rebuildable, so extraction quality can improve forever
  without migration pain.
- Time-travel queries: what was true on a date, what a figure used to be, when a
  position changed.
- Orion's job shrinks by more than half, and what remains is reasoning rather
  than text-shuffling.

**The decision is §2.** Everything else follows from it. If the answer is no,
the fallback is smaller and still worthwhile: keep files primary, add the
observation backfill, and accept nightly reconciliation as permanent.
