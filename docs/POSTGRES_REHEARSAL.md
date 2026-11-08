# PostgreSQL migration rehearsal

Production remains on SQLite. A consistent snapshot passed SQLite's integrity check. The private rehearsal copied the 111 active ORM tables into an empty PostgreSQL 16 database and verified all 288,543 rows by typed content fingerprints, including original IDs, message JSON, attachments and memory evidence. The full SQLite snapshot retains 43 legacy tables and seven legacy session fields alongside the active data. Keep that snapshot as an archive and reconcile all later production writes before a future cutover.

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

## Conditions for cutover

- Repeat recall, Hall edition, citation and authority checks on the final snapshot before switching traffic. The frozen ranking control applies only to frozen snapshots.
- Verify concurrent agents, queue recovery and shared budgets on the migrated schema. Port and test the Solus database guards and the production backup, deploy and rollback controller against PostgreSQL.
- Use a fresh snapshot and reconcile the final write delta during a quiesced switch. Retain verified backups and test rollback with writes made after the switch.
