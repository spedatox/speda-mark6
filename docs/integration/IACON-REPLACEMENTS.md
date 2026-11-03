# Iacon is implemented; old tool names are not a second memory backlog

The initial route/skill audit compared literal names from `final` with mounted
names. That overstated missing memory work. The approved Iacon architecture
replaces the old writers, observation weights and maintenance engine rather than
registering them alongside the new catalogue.

| Original interface | Native authority |
| --- | --- |
| `memory`, `search_memory` | Prepared Iacon context and one bounded `memory_lookup` batch |
| `memory_event`, `memory_state` | Source-backed typed episodes/states with reviewed lifecycle updates |
| `record_observation`, `forget_observation` | Attributed evidence, reviewed claims, explicit retraction and revision history |
| `memory_edit`, `ledger_append`, `registry_upsert`, `narrative_revise` | Owner document/editor CAS operations and reviewed Iacon proposals; direct model file writers remain retired |
| `memory_audit` and old memory admin jobs | Durable Orion maintenance, bounded interpretation/review and fenced publication |
| Course memory tools | Course entities, linked episodes/documents and the same Iacon retrieval/writer authority |
| Old graph/pattern tools | Stable typed links, evidence-backed inferred claims and corrections; no claim of an identical graph browser or separate reinforcement learner |
| `list_library`, `search_library`, `read_library` | Hall of Records list/search/read operations in `memory_lookup`, using the same turn batch, time and byte allowance |
| `finance_record` | Solus ledger and immutable receipts; personal memory is not an accounting writer |

The machine-readable [replacement mappings](replacements.json) are applied by
`audit_integration.py`. `superseded` is distinct from test-verified compatibility:
it records an intentional architecture replacement, not identical old behavior.

The core Iacon catalogue, evidence/revisions, source preservation, corrections,
temporal qualifiers, prepared retrieval, reviewed maintenance, living-state
follow-ups and Hisar adapter already exist. Listing and exact Hall document reads
now share the existing bounded lookup interface as well. Native literal archive
search and owner-local date ranges are implemented with original message/source
IDs and attribution. They share the existing lookup allowance; the old paid
embedding query path is intentionally replaced, without claiming identical
semantic ranking. Live Hisar/Telegram acceptance remains a separate check, not
a reason to restore the old memory engine.

Projects and canvas functionality are deferred at the owner's request. Their
future knowledge UX must use the established scope and provenance boundaries.
