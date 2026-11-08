# PostgreSQL migration rehearsal

Production remains on SQLite. A consistent snapshot passed SQLite's integrity check. The private rehearsal copied the 111 active ORM tables into an empty PostgreSQL 16 database and verified all 288,543 rows by typed content fingerprints, including original IDs, message JSON, attachments and memory evidence. The full SQLite snapshot retains 43 legacy tables and seven legacy session fields alongside the active data. Keep that snapshot as an archive and reconcile all later production writes before a future cutover.

The import required a 64-bit type for attendance timestamps and reversible encoding for zero bytes in PostgreSQL memory text. Conversation search also projects actual JSON zero-byte escapes to a safe separator before extracting text. Its excerpts still come from the original message. Both SQLite and PostgreSQL history tests cover original-text retention and literal escape handling.

## Recall results

Seventeen cases yielded 27 outputs across automatic memory, deliberate recall, conversation history and direct course reads. The comparison used the same frozen data, ran without models, and checked ranked evidence, exact text, attribution, scope, and the serialized payload.

| Ranking path | Same ranked evidence and text | Same serialized payload | Exact full output |
| --- | ---: | ---: | ---: |
| Current PostgreSQL fallback | 14 of 27 | 9 of 27 | 9 of 27 |
| Frozen SQLite ranking control with PostgreSQL evidence | 27 of 27 | 26 of 27 | 20 of 27 |

The PostgreSQL fallback lost baseline evidence in 13 outputs. All three history and all four course outputs matched exactly, including the six-record course index, the exact MIS 231 document and denial for another owner. The frozen control matched the evidence and its ranking in all cases. Seven outputs differed in omission diagnostics, including one serialized payload. The control uses the frozen SQLite ranker to isolate search differences from storage differences.

## Reproducing the gate

Run the commands from the repository root with a private snapshot and an empty loopback PostgreSQL database named `speda_rehearsal`. Set `SPEDA_REHEARSAL_POSTGRES_URL` to its asyncpg connection URL. Place the JSON reports in a private operations directory.

```text
python -m scripts.rehearse_postgres source.sqlite3 active-copy.json
python -m scripts.compare_postgres_recall source.sqlite3 cases.json native.json --require-parity
python -m scripts.compare_postgres_recall source.sqlite3 cases.json ranking-control.json --sqlite-ranking-control
```

The cases file contains a JSON array. Each case has a `label` and a `mode` of `personal`, `history`, or `course`. Personal and history cases supply a `query`. Course cases can supply `args` with a course code and term. A case can specify an agent, owner, or timezone.

## Conditions for cutover

- Preserve SQLite's matching, ranked evidence and user-visible qualifiers under PostgreSQL, including when new evidence arrives or access is revoked. The frozen ranking control applies only to frozen snapshots.
- Rehearse live Hall of Records access, edition selection, search ranking and citations. PostgreSQL currently uses a literal fallback for Hall ranking.
- Verify concurrent agents, queue recovery and shared budgets on the migrated schema. Port and test the Solus database guards and the production backup, deploy and rollback controller against PostgreSQL.
- Use a fresh snapshot and reconcile the final write delta during a quiesced switch. Retain verified backups and test rollback with writes made after the switch.
