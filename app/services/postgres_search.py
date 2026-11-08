"""SQLite FTS5 ranking over a disposable projection of PostgreSQL rows."""

import asyncio
import json
import sqlite3
from threading import Lock

from sqlalchemy import select, text


_TRACKED = {
    "memory_changes": "user_id, routing_text, source_role, revoked, provenance, created_at",
    "memory_serving_units": "user_id, routing_text",
    "library_passages": "content, revision_id, generation_id",
    "library_revisions": "document_id",
    "library_documents": "title",
}


async def install_postgres_search(connection):
    """Changes and their versions commit together. Revisions need no global writer lock."""
    await connection.exec_driver_sql("CREATE SEQUENCE IF NOT EXISTS lexical_version_seq AS bigint")
    await connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS lexical_versions (
            corpus text NOT NULL,
            record_id text NOT NULL,
            version bigint NOT NULL DEFAULT nextval('lexical_version_seq'),
            PRIMARY KEY (corpus, record_id)
        )
    """)
    await connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION track_lexical_version() RETURNS trigger
        LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
        BEGIN
            IF TG_OP = 'TRUNCATE' THEN
                DELETE FROM lexical_versions WHERE corpus = TG_TABLE_NAME;
                RETURN NULL;
            END IF;
            IF TG_OP = 'DELETE' OR (TG_OP = 'UPDATE' AND OLD.id <> NEW.id) THEN
                DELETE FROM lexical_versions WHERE corpus = TG_TABLE_NAME AND record_id = OLD.id::text;
                IF TG_OP = 'DELETE' THEN
                    RETURN NULL;
                END IF;
            END IF;
            INSERT INTO lexical_versions(corpus, record_id) VALUES (TG_TABLE_NAME, NEW.id::text)
            ON CONFLICT (corpus, record_id) DO UPDATE SET version = excluded.version;
            RETURN NULL;
        END
        $$
    """)
    for table, columns in _TRACKED.items():
        await connection.exec_driver_sql(f"""
            CREATE OR REPLACE TRIGGER lexical_row_version
            AFTER INSERT OR DELETE OR UPDATE OF id, {columns} ON {table}
            FOR EACH ROW EXECUTE FUNCTION track_lexical_version()
        """)
        await connection.exec_driver_sql(f"""
            CREATE OR REPLACE TRIGGER lexical_truncate
            AFTER TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION track_lexical_version()
        """)
        await connection.exec_driver_sql(f"""
            INSERT INTO lexical_versions(corpus, record_id)
            SELECT '{table}', source.id::text FROM {table} AS source
            WHERE NOT EXISTS (
                SELECT 1 FROM lexical_versions WHERE corpus = '{table}' AND record_id = source.id::text
            )
            ON CONFLICT (corpus, record_id) DO NOTHING
        """)


async def pin_search_snapshot(db):
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    db.info["fts5_versions"] = {}


_SCHEMA = """
    PRAGMA temp_store = MEMORY;
    CREATE TABLE documents(
        id TEXT NOT NULL UNIQUE, content TEXT NOT NULL, title TEXT NOT NULL,
        owner_id INTEGER, eligible INTEGER, owner_statement INTEGER, created_at TEXT,
        revision_id TEXT, generation_id TEXT
    );
    CREATE VIRTUAL TABLE terms USING fts5(content, title, content='documents', content_rowid='rowid');
    CREATE TRIGGER document_added AFTER INSERT ON documents BEGIN
        INSERT INTO terms(rowid, content, title) VALUES (new.rowid, new.content, new.title);
    END;
    CREATE TRIGGER document_removed AFTER DELETE ON documents BEGIN
        INSERT INTO terms(terms, rowid, content, title)
        VALUES ('delete', old.rowid, old.content, old.title);
    END;
"""


class SearchProjection:
    def __init__(self, name):
        self.name = name
        self._state = (None, {})
        self._sqlite = None
        self._refresh = asyncio.Lock()
        self._thread = Lock()

    async def query(self, db, version_sql, load, rank_sql, parameters):
        """Version maps compare the visible rows, even when transactions commit out of order."""
        snapshot = db.info["fts5_versions"]
        if self.name not in snapshot:
            snapshot[self.name] = await db.scalar(text(version_sql), {"corpus": self.name}) or "{}"
        token = snapshot[self.name]
        async with self._refresh:
            while True:
                base = self._state
                versions = base[1] if token == base[0] else json.loads(token)
                changed = [] if versions is base[1] else [
                    identity for identity, version in versions.items() if base[1].get(identity) != version
                ]
                documents = await load(changed) if changed else []
                if {row[0] for row in documents} != set(changed):
                    raise RuntimeError("Search versions and documents disagree")
                removed = () if versions is base[1] else base[1].keys() - versions.keys()
                result = await asyncio.to_thread(self._apply, base, token, versions, removed, documents,
                                                 rank_sql, parameters)
                if result is not None:
                    return result

    def _apply(self, base, token, versions, removed, documents, sql, parameters):
        with self._thread:
            # A cancelled waiter can leave its SQLite work running. Re-plan
            # against the published versions if that work won the race.
            if self._state is not base:
                return None
            if self._sqlite is None:
                connection = sqlite3.connect(":memory:", check_same_thread=False)
                try:
                    connection.executescript(_SCHEMA)
                except BaseException:
                    connection.close()
                    raise
                self._sqlite = connection
            if removed or documents:
                with self._sqlite as connection:
                    connection.executemany("DELETE FROM documents WHERE id = ?", ((identity,) for identity in removed))
                    connection.executemany("DELETE FROM documents WHERE id = ?", ((row[0],) for row in documents))
                    connection.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)", documents)
            if versions is not base[1]:
                self._state = (token, versions)
            return self._sqlite.execute(sql, parameters).fetchall()


class PostgresMemorySearch:
    def __init__(self):
        self.sources = SearchProjection("memory_changes")
        self.units = SearchProjection("memory_serving_units")

    async def rank(self, db, model, target, user_id, match, limit, recent):
        source = target == "sources"
        projection = self.sources if source else self.units
        columns = [model.id, model.routing_text, model.user_id]
        if source:
            columns.extend([model.revoked, model.provenance, model.source_role, model.created_at])

        async def load(ids):
            documents = []
            for start in range(0, len(ids), 4096):
                subset = [int(identity) for identity in ids[start:start + 4096]]
                for identity, content, owner, *meta in (await db.execute(
                    select(*columns).where(model.id.in_(subset))
                )).all():
                    if source:
                        revoked, provenance, role, created = meta
                        eligible = not revoked and provenance.get("eligible_for_context", 1) in (None, 1)
                        owner_statement = role == "owner"
                        created_at = created.isoformat()
                    else:
                        eligible, owner_statement, created_at = True, False, None
                    documents.append((str(identity), content, "", owner, eligible, owner_statement,
                                      created_at, None, None))
            return documents

        order = "d.created_at DESC" if recent else "bm25(terms)"
        scope = "AND d.owner_statement = 1" if recent else ""
        rows = await projection.query(db,
            """SELECT json_object_agg(record_id, version ORDER BY record_id COLLATE "C")::text
               FROM lexical_versions WHERE corpus = :corpus""", load,
            f"""SELECT d.id FROM terms JOIN documents d ON d.rowid = terms.rowid
                WHERE terms MATCH ? AND d.owner_id = ? AND d.eligible = 1 {scope}
                ORDER BY {order}, CAST(d.id AS INTEGER) DESC LIMIT ?""", (match, user_id, limit))
        return [int(row[0]) for row in rows]


class PostgresLibrarySearch:
    def __init__(self):
        self.projection = SearchProjection("library_passages")

    async def rank(self, db, match, revisions, generations, limit):
        from app.models.library import LibraryDocument, LibraryPassage, LibraryRevision
        from app.services.lexical import fold

        async def load(ids):
            query = select(LibraryPassage.id, LibraryPassage.content, LibraryDocument.title,
                           LibraryPassage.revision_id, LibraryPassage.generation_id).join(
                LibraryRevision, LibraryRevision.id == LibraryPassage.revision_id).join(
                LibraryDocument, LibraryDocument.id == LibraryRevision.document_id)
            documents = []
            for start in range(0, len(ids), 4096):
                rows = await db.execute(query.where(LibraryPassage.id.in_(ids[start:start + 4096])))
                documents.extend((identity, fold(content), fold(title), None, True, False, None, revision, generation)
                                 for identity, content, title, revision, generation in rows)
            return documents

        # JSON parameters keep the same edition and generation filters without
        # growing SQLite's bound-variable list with the size of the library.
        return await self.projection.query(db, """
            SELECT json_object_agg(p.id, ARRAY[passage.version, revision.version, document.version]
                                   ORDER BY p.id COLLATE "C")::text
            FROM library_passages p
            JOIN library_revisions r ON r.id = p.revision_id
            JOIN lexical_versions passage ON passage.corpus = :corpus AND passage.record_id = p.id
            JOIN lexical_versions revision ON revision.corpus = 'library_revisions' AND revision.record_id = r.id
            JOIN lexical_versions document ON document.corpus = 'library_documents' AND document.record_id = r.document_id
        """, load, """
            SELECT d.id, bm25(terms, 1, 2) AS rank FROM terms JOIN documents d ON d.rowid = terms.rowid
            WHERE terms MATCH ? AND d.revision_id IN (SELECT value FROM json_each(?))
            AND d.generation_id IN (SELECT value FROM json_each(?))
            ORDER BY rank, d.id LIMIT ?
        """, (match, json.dumps(list(revisions)), json.dumps(generations), limit))
