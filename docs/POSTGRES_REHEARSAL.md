# PostgreSQL migration rehearsal

The initial rehearsal used a consistent production SQLite snapshot that passed SQLite's integrity check. It copied the 111 active ORM tables into an empty PostgreSQL 16 database and verified all 288,543 rows by typed content fingerprints, including original IDs, message JSON, attachments and memory evidence. The full SQLite snapshot retains 43 legacy tables and seven legacy session fields alongside the active data.

The import required a 64-bit type for attendance timestamps and reversible encoding for zero bytes in PostgreSQL memory text. Conversation search also projects actual JSON zero-byte escapes to a safe separator before extracting text. Its excerpts still come from the original message. Both SQLite and PostgreSQL history tests cover original-text retention and literal escape handling.

## Recall results

Seventeen cases yielded 27 outputs across automatic memory, deliberate recall, conversation history and direct course reads. The comparison used the same frozen data, ran without models, and checked ranked evidence, exact text, attribution, scope, and the serialized payload.

| Ranking path | Same ranked evidence and text | Same serialized payload | Exact full output |
| --- | ---: | ---: | ---: |
| Original PostgreSQL fallback | 14 of 27 | 9 of 27 | 9 of 27 |
| Frozen SQLite ranking control with PostgreSQL evidence | 27 of 27 | 26 of 27 | 20 of 27 |
| PostgreSQL-backed BM25 | 27 of 27 | 27 of 27 | 27 of 27 |

The original PostgreSQL fallback lost baseline evidence in 13 outputs. The replacement keeps the same BM25 tokenizer and scoring in a disposable, in-memory FTS5 projection. PostgreSQL transactions record per-row search versions alongside changes. The ranker reads those versions and the authoritative evidence from one repeatable-read snapshot, then updates only the changed index entries. Owner and edition scope apply before the ranking limit. Tied review dates use the original serving-unit ID as a stable tiebreaker.

All three history and all four course outputs matched exactly, including the six-record course index, the exact MIS 231 document and denial for another owner. All 68 Hall keyword probes on the same snapshot matched passage IDs, order and scores for individual editions and the whole index. Integration tests exercise current and historical editions, access revocation, updated titles and concurrent citation creation.

Thirty automatic recall probes ran in groups of two, three and four at the existing deadline. PostgreSQL completed all 30 and matched the SQLite payload in every case where SQLite completed. SQLite hit five deadlines in this sample.

## Reproducing the gate

Run the commands from the repository root with a private snapshot and an empty loopback PostgreSQL database named `speda_rehearsal`. Set `SPEDA_REHEARSAL_POSTGRES_URL` to its asyncpg connection URL. Place the JSON reports in a private operations directory.

```text
python -m scripts.rehearse_postgres source.sqlite3 active-copy.json
python -m scripts.compare_postgres_recall source.sqlite3 cases.json postgres-bm25.json --require-parity
python -m scripts.compare_postgres_recall source.sqlite3 cases.json ranking-control.json --sqlite-ranking-control
```

The cases file contains a JSON array. Each case has a `label` and a `mode` of `personal`, `history`, or `course`. Personal and history cases supply a `query`. Course cases can supply `args` with a course code and term. A case can specify an agent, owner, or timezone.

## Production cutover

The host-owned `scripts.cutover_postgres` prepares a private PostgreSQL service and rehearses the verified application image on a disposable copy. The actual switch takes the production deployment lock, gates ingress, stops writers and captures a fresh SQLite backup. It verifies all active row contents, refreshes PostgreSQL planner statistics, and repeats recall and Hall ranking on the same frozen dataset.

Before reopening traffic, the host restores a PostgreSQL dump to an isolated container, checks row fingerprints, and verifies an authenticated synthetic memory write survives a restart there. It resets a saved SQLite database override through the versioned configuration store and verifies the app's effective database setting after startup. It checks existing Hall citations through the running application and retains the SQLite archive, PostgreSQL dump, and pre-cutover state and configuration.

Subsequent CI releases use PostgreSQL backups and verify restore, existing-row preservation, and compatibility with both the candidate and retained images. Code rollback uses the current database. A return to SQLite after production writes requires reconciling those writes with the retained archive.
