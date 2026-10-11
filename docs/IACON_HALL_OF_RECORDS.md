# Iacon Hall of Records

Local implementation rebuilt on 10 October 2026. This report describes source
and verified isolated behavior; it does not assert a production deployment.

Iacon is the owner's persistent reference archive. Hisar owns named records,
immutable original bytes and edition authority. Igor owns derived indexing and
bounded evidence retrieval. It is separate from personal memory, project desks,
Forge inputs and generated output files.

## Owner workflow

Open **Iacon Hall of Records / Iacon Kayıtlar Salonu** from the Hisar desktop,
Finder sidebar or dock. Add one original, name the record, choose its type and
collection, and optionally supply issue/coverage dates and term. Collections
are metadata, not filesystem folders or document identity.

The catalog filters titles, aliases, collections and institutions. Select a
record to open its original, edit metadata/access, upload another edition,
inspect history, activate a retained edition, retry indexing, or archive and
restore the record. A stale metadata/upload version returns 409 instead of
overwriting another change. Withdrawing through the API hides a record but
retains bytes; the UI offers reversible archive instead.

Default agent access is all assistant profiles. Edit details accepts an explicit
allowed-agents list. Machine vault credentials cannot bypass library policy by
opening a generic Finder/download path; sync and reader tokens are never passed
to Forge workers. In-process retrieval workers inherit their parent's agent
scope, and the service validates that scope live.

## What is implemented

- Hisar catalog mutations and their ordered events commit in the same SQLite
  transaction. Blob uploads have durable staging receipts, size/format checks,
  fsync, SHA-256 addressing and exclusive publication before catalog commit.
  Crash leftovers are retained and marked interrupted for operator inspection.
- Adding/replacing the same bytes and edition metadata in one record is
  idempotent. Replacements retain older bytes, edition sequence and supersession.
  Active-edition changes must reference an edition of that same record.
- Startup enrolls supported files already in Documents/Library once, with a
  durable path receipt in the catalog transaction. It snapshots originals and
  leaves every legacy source file in place. Unsupported/oversized files remain
  untouched and enrollment failure is logged. Changes to legacy files after
  enrollment are not a live binding; upload a new edition explicitly.
- Generic uploads to the Library mount enroll records. Generic rename/delete/
  mkdir/overwrite cannot bypass the catalog. Internal catalogs, blobs and trash
  are inaccessible through generic vault paths, including resolved aliases.
- Igor mirrors authoritative catalog metadata and every retained edition.
  Generation IDs are stable per owner, source, revision and extractor fingerprint.
  Jobs enter the existing background_jobs queue with session_id=None. Delivery
  cursors advance only after work has been durably accepted.
- Post-commit Hisar delivery, one Igor startup reconciliation, owner Refresh and
  the n8n workflow recover missing events. There is no internal polling timer or
  scheduler. /admin/tasks/drain retains the existing retry/startup recovery.
- Original MIME, size and hash are checked before extraction. Passages and
  readiness publish atomically. Parser failures never become searchable text.
- SQLite FTS5/BM25 uses Turkish-aware search folding, keeping quoted source
  text unchanged. Authorization, active edition, archive state and coverage
  eligibility are applied before top-k. Dates are evaluated in AgentContext's
  timezone. An explicit on_date requires declared coverage dates.
- List/search/read are real Skill implementations, read-only, with memoization
  disabled so within-turn authority changes are checked. Search is initially
  discoverable; list/read use the established deferred tool discovery.
- Citations use an exact passage hash and immutable edition identity. Authenticated
  source resolution rechecks the authoritative record and refuses withdrawn
  records or changed evidence. Historical/archived citations are labeled.

## Readiness and limits

| State | Meaning |
|---|---|
| no_edition | Named entry has no original yet |
| pending / processing | Exact active edition has not finished indexing |
| ready_keyword | Complete supported text extraction and keyword retrieval |
| needs_review | Original retained; extraction is unavailable or incomplete |
| failed | Retryable transport/integrity/indexing failure; inspect the queue |
| archived | Hidden from default current retrieval; explicitly selected history remains available |
| withdrawn | Hidden from all retrieval; retained originals are not purged |

Supported **original uploads**: PDF, UTF-8 TXT/Markdown/CSV, PNG/JPEG, DOCX and
XLSX, up to 25 MiB by default. Supported **extraction**: text PDF (up to 200
pages), UTF-8 text/Markdown and CSV with repeated header context per row.
Passages are at most 2,000 characters; search/read return at most eight. Reads
have an offset/next_offset continuation. Extracted text is capped at two million
characters. A mixed/scanned PDF with any blank-text page requires review for the
entire edition; it is never silently considered complete.

OCR, Office extraction, semantic embeddings, reliable PDF table geometry,
extraction-correction UI and client-native citation cards are not implemented.
Images and Office originals therefore honestly require review. These are
deliberate visible limitations, replacing the previous fake OCR/search results.
PostgreSQL uses an explicit bounded literal-search fallback; BM25 is SQLite-only.
This change does not claim semantic or vector retrieval.

## Configuration and recovery

Igor uses HISAR_BASE_URL (existing container-network address),
HISAR_LIBRARY_SYNC_TOKEN, HISAR_LIBRARY_READER_TOKEN, LIBRARY_OWNER_USER_ID
(default 1), LIBRARY_MAX_BYTES and LIBRARY_MAX_PAGES. Hisar uses matching scoped
tokens, HISAR_LIBRARY_MAX_BYTES, and optional HISAR_IGOR_URL (default
http://app:8000), HISAR_IGOR_API_KEY and HISAR_IGOR_N8N_SECRET for post-commit
delivery. Keep credentials in deployment env files, never browser code.

Hisar owner refresh calls Igor with the two required headers. If delivery is
unconfigured or unavailable, the original remains saved and the view says so.
The importable n8n template is packages/igor/scripts/n8n/iacon_reconcile.json.
It does deterministic full reconciliation only, without an agent turn. Existing
queue-drain workflows handle retries.

Existing table names and IDs are retained. Igor adds only the position column
to library_passages. Old placeholder index generations are excluded by the v2
fingerprint and rebuilt from authoritative originals. Hisar normalizes old
unsupported readiness receipts to pending. No migration destroys original
files, old passages or catalog editions.

For deployment, back up both SQLite stores using their online backup facilities
and preserve Hisar's complete .hisar/library blob tree plus legacy sources.
Igor database backups alone do not contain the originals. Use the shared SPEDA
production operations instructions before any server action. Apply both apps
together, configure the scoped tokens/delivery, import the n8n workflow, then
check upload, replacement, revocation and citation resolution on the running
services. This task has not changed production.

## Verification

- packages/igor/tests/test_library_hall_records.py: isolated real SQL indexing,
  Turkish BM25, bounded reads, citations, current-edition freshness, access
  filtering, date coverage, replay/durable acceptance, tamper rejection,
  scanned PDFs, real Skill contracts and dual-header reconciliation.
- Hisar tests/test_library.py: real multipart upload/download, Finder enrollment,
  idempotent revisions/CAS, archive/restore, scoped auth, format/date/size limits,
  retained originals, receipt validation, internal path protection and lossless
  legacy enrollment.
- packages/igor/scripts/verify_iacon.py --hisar-checkout <local Hisar checkout>:
  both actual APIs plus the actual background queue, a simulated restart,
  hash-verified indexing, Turkish retrieval, citation resolution, replacement
  freshness and live revocation. Uses only temporary test data and test secrets.
- Hisar Vite production build and desktop browser inspection.

Five existing Hisar symlink tests require Windows symlink privileges unavailable
in this session; all other Hisar tests run. No test caches or scratch databases
are created in either checkout.