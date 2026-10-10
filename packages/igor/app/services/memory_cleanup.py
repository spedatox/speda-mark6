# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline, resumable and lossless corpus cutover. Never opens a live app DB.

The source is read-only. Every changed original row is retained before cutover;
each canonical document commits independently with its aliases and metadata.
No inference, re-extraction, embeddings, reviewer or provider is invoked.
"""
import base64
import hashlib
import json
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.services.memory_catalog import canonical_path, declared_aliases, document_identity, retired_monoliths
from app.services.memory_paths import parse_monthly_path, slugify

VERSION = "corpus-v3"
LAYOUT_POLICY = "retire-monoliths-v1"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(key: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, VERSION + ":" + key).hex


def packed(row) -> str:
    return json.dumps(dict(row), ensure_ascii=False, sort_keys=True,
        default=lambda v: {"base64": base64.b64encode(v).decode()} if isinstance(v, bytes) else str(v))


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _unique_blocks(blocks):
    """Remove only byte-identical blocks or blocks contained verbatim in another."""
    result = []
    for block in blocks:
        block = block.strip()
        # Bold delimiters and whitespace are presentation, not a second event.
        normalized = lambda s: " ".join(s.replace("**", "").split())
        if not block or any(normalized(block) in normalized(old) for old in result):
            continue
        result = [old for old in result if normalized(old) not in normalized(block)]
        result.append(block)
    return result


def merge_documents(rows: list[dict]) -> str:
    if len(rows) == 1:
        return rows[0]["content"]
    ordered = sorted(rows, key=lambda r: (r["updated_at"], r["id"]), reverse=True)
    title = ordered[0]["content"].splitlines()[0]
    if all("**Who:**" in row["content"] and "**Events:**" in row["content"] for row in ordered):
        descriptions, events = [], []
        for row in ordered:
            who, event = row["content"].split("**Who:**", 1)[1].split("**Events:**", 1)
            descriptions.append(who)
            events.extend(re.split(r"(?m)(?=^- )", event))
        # Keep conflicting descriptions, never guess which one remains true.
        return title + "\n\n**Who:** " + "\n\n".join(_unique_blocks(descriptions)) + "\n\n**Events:**\n" + "\n".join(_unique_blocks(events)) + "\n"
    sections = defaultdict(list)
    for row in ordered:
        body = row["content"].partition("\n")[2]
        parts = re.split(r"(?m)^(## [^\n]+)\n", body)
        sections[""].append(parts[0])
        for i in range(1, len(parts), 2):
            sections[parts[i]].append(parts[i+1])
    result = [title]
    for heading, bodies in sections.items():
        if heading:
            result.append(heading)
        # Bullets/log entries can deduplicate independently; prose stays intact.
        blocks = []
        for body in bodies:
            blocks.extend(re.split(r"\n\n|(?m:(?=^- ))", body))
        result.append("\n\n".join(_unique_blocks(blocks)))
    return "\n\n".join(part for part in result if part) + "\n"


def build_plan(files: list[dict]) -> list[dict]:
    retired = retired_monoliths(files)
    groups = defaultdict(list)
    for row in files:
        if "/." in row["path"] or (row["user_id"], row["path"]) in retired:
            continue
        identity = document_identity(row["path"], row["content"])
        path = canonical_path(row["path"], row["updated_at"], row["content"])
        # Identity is scoped by domain + group and exact title normalization.
        if identity:
            category, kind, name = identity
            parsed = parse_monthly_path(path)
            key = (row["user_id"], category, parsed.group if parsed else "", slugify(name))
        else:
            key = (row["user_id"], path)
        groups[key].append(row)
    plan = []
    for key, rows in sorted(groups.items(), key=lambda item: str(item[0])):
        latest = max(rows, key=lambda r: (r["updated_at"], r["id"]))
        target = canonical_path(latest["path"], latest["updated_at"], latest["content"])
        if target.startswith("/memories/states/"):
            # The lifecycle's verified date outranks migration/write timestamps.
            from app.services.memory_states import parse
            latest = max(rows, key=lambda r: (parse(r["content"])["verified_on"], r["updated_at"], r["id"]))
            after = latest["content"]
        else:
            after = merge_documents(rows)
        plan.append({"user_id": latest["user_id"], "target": target,
                     "rows": rows, "keeper": latest["id"], "content": after})
    # A guessed identity must never silently collide with another identity's path.
    targets = [(u["user_id"], u["target"]) for u in plan]
    if len(set(targets)) != len(targets):
        raise ValueError("Ambiguous target paths; explicit identity mapping required.")
    return plan


class CorpusCleanup:
    def __init__(self, source: Path, output: Path, *, path_mappings: dict[str, str] | None = None):
        self.source, self.output = source.resolve(), output.resolve()
        if self.source == self.output or (self.output.exists() and self.source.samefile(self.output)):
            raise ValueError("Source and output must differ; in-place migration is forbidden.")
        self.fingerprint = file_hash(self.source)
        self.migration_id = VERSION + "-" + LAYOUT_POLICY + "-" + self.fingerprint[:16]
        self.path_mappings = path_mappings or {}
        for old_path,target in self.path_mappings.items():
            if any(not p.startswith("/memories/") or not p.endswith(".md") or ".." in p or "\\" in p for p in (old_path,target)):
                raise ValueError("Path mappings must contain safe memory document addresses.")
        self.plan_hash = digest(VERSION+LAYOUT_POLICY+self.fingerprint+json.dumps(self.path_mappings, sort_keys=True))
        self.original = sqlite3.connect(self.source.as_uri()+"?mode=ro&immutable=1", uri=True)
        self.original.row_factory = sqlite3.Row
        # A supplied snapshot must have no live WAL; immutable mode ignores it.
        if Path(str(self.source)+"-wal").exists():
            self.original.close()
            raise ValueError("Source has a WAL; take a consistent offline backup first.")
        if not self.output.exists():
            self.output.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.output) as dest:
                self.original.backup(dest)
        self.db = sqlite3.connect(self.output)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.now = datetime.now(timezone.utc).isoformat()
        has_runs = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_migration_runs'").fetchone()
        run = self.db.execute("SELECT * FROM memory_migration_runs WHERE plan_id=?", (self.migration_id,)).fetchone() if has_runs else None
        if run and run["source_fingerprint"] != self.fingerprint:
            raise ValueError("Output belongs to another source.")
        if run and run["plan_hash"] != self.plan_hash:
            raise ValueError("Identity mappings changed; resume only the original plan.")
        if not run:
            # Refuse to turn an arbitrary existing database into a migration copy.
            tables=[r[0] for r in self.original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                if self.table_hash(self.original, table) != self.table_hash(self.db, table):
                    raise ValueError("Existing output does not match the source; choose a new output.")
        self._schema()
        if not run:
            with self.db:
                self.db.execute("INSERT INTO memory_migration_runs (plan_id,plan_hash,source_fingerprint,phase,schema_version_from,schema_version_to,total_sources,committed_count,created_at,updated_at) VALUES (?,?,?,'prepared',2,3,?,0,?,?)",
                    (self.migration_id, self.plan_hash, self.fingerprint,
                     self.original.execute("SELECT count(*) FROM memory_files").fetchone()[0], self.now, self.now))

    def close(self):
        self.original.close()
        self.db.close()

    def _schema(self):
        from sqlalchemy import create_engine
        from app.database import Base
        import app.models
        engine = create_engine("sqlite:///" + self.output.as_posix())
        Base.metadata.create_all(engine, tables=[Base.metadata.tables[n] for n in (
            "memory_sources", "memory_passages", "memory_capture_payloads", "memory_issues", "memory_graph_edges",
            "memory_entities", "memory_entity_heads", "memory_record_meta", "memory_record_links",
            "memory_path_aliases", "memory_migration_runs")])
        engine.dispose()
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS memory_cleanup_units (
            migration_id TEXT NOT NULL, unit_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
            committed_at TEXT NOT NULL, PRIMARY KEY (migration_id,unit_key));
          CREATE TABLE IF NOT EXISTS memory_cleanup_snapshots (
            migration_id TEXT NOT NULL, table_name TEXT NOT NULL, row_id INTEGER NOT NULL,
            original_row TEXT NOT NULL, PRIMARY KEY (migration_id,table_name,row_id));
        """)

    @staticmethod
    def table_hash(db, table: str, *, before_id: int | None = None) -> str:
        columns = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
        if not columns:
            return "absent"
        order = '"id"' if "id" in columns else ','.join('"'+c+'"' for c in columns)
        where = f" WHERE id <= {int(before_id)}" if before_id is not None else ""
        h = hashlib.sha256()
        for row in db.execute(f'SELECT * FROM "{table}"{where} ORDER BY {order}'):
            value = packed(row).encode("utf-8")
            h.update(len(value).to_bytes(8, "big")); h.update(value)
        return h.hexdigest()

    def snapshot(self, table: str, row):
        self.db.execute("INSERT OR IGNORE INTO memory_cleanup_snapshots VALUES (?,?,?,?)",
                        (self.migration_id, table, row["id"], packed(row)))

    def issue(self, user_id, key, kind, refs, detail):
        self.db.execute("INSERT OR IGNORE INTO memory_issues (user_id,issue_key,kind,refs,detail,status,created_at) VALUES (?,?,?,?,?,'open',?)",
                        (user_id, key, kind, json.dumps(refs), detail, self.now))

    def edge(self, user, source, target, relation, evidence=None):
        if source != target:
            self.db.execute("INSERT OR IGNORE INTO memory_graph_edges (user_id,source_ref,target_ref,relation_type,evidence_ref,created_at) VALUES (?,?,?,?,?,?)",
                            (user, source, target, relation, evidence, self.now))

    def preserve(self):
        """Capsules and control rows commit before any source retirement."""
        with self.db:
            for row in self.original.execute("SELECT * FROM memory_files ORDER BY id"):
                source_id = stable_id(f"{self.fingerprint}:file:{row['id']}")
                classification = "system" if "/.audit/" in row["path"] else "historical" if "/.archive/" in row["path"] else "original"
                self.db.execute("INSERT OR IGNORE INTO memory_sources VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (source_id, row["user_id"], f"{self.fingerprint}:file:{row['id']}", row["path"],
                     row["content"], digest(row["content"]), classification, packed(row), self.migration_id, self.now))
            for table in ("memory_record_meta", "memory_path_aliases", "memory_capture_jobs", "memory_entity_heads", "memory_entities"):
                for row in self.original.execute(f"SELECT * FROM {table}"):
                    self.snapshot(table, row)

    def _metadata(self, file_id, user, path, content, recorded_at):
        meta = self.db.execute("SELECT * FROM memory_record_meta WHERE memory_file_id=?", (file_id,)).fetchone()
        record = meta["record_id"] if meta else stable_id(f"record:{self.fingerprint}:{file_id}")
        monthly = parse_monthly_path(path)
        category = path.split("/")[2].removesuffix(".md")
        period = monthly.period.canonical if monthly else recorded_at[:7]
        kind = "state" if category == "states" else "reference"
        if "finance-record-v1" in content:
            from app.services.finance_records import parse
            kind = parse(content)["type"]
        if meta:
            self.db.execute("UPDATE memory_record_meta SET kind=?,content_hash=?, schema_version=3, version=version+CASE WHEN content_hash<>? THEN 1 ELSE 0 END WHERE id=?",
                            (kind, digest(content), digest(content), meta["id"]))
        else:
            self.db.execute("INSERT INTO memory_record_meta (memory_file_id,record_id,user_id,category,kind,period,period_basis,recorded_at,date_precision,schema_version,version,content_hash) VALUES (?,?,?,?,?,?,'recorded_at',?,'unknown',3,1,?)",
                            (file_id, record, user, category, kind, period, recorded_at, digest(content)))
        identity = document_identity(path, content)
        if identity:
            name = identity[2]
            existing = self.db.execute(
                "SELECT id FROM memory_entities WHERE user_id=? AND category=? AND canonical_name=?",
                (user, category, name),
            ).fetchone()
            entity_id = existing[0] if existing else stable_id(f"entity:{user}:{category}:{slugify(name)}")
            self.db.execute("INSERT OR IGNORE INTO memory_entities VALUES (?,?,?,?,?,?,?)",
                            (entity_id, user, category, identity[1], name, json.dumps(declared_aliases(name,content)), self.now))
            self.db.execute("UPDATE memory_record_meta SET entity_id=?,edition_seq=1 WHERE record_id=? AND user_id=?", (entity_id, record, user))
            self.db.execute("INSERT INTO memory_entity_heads (user_id,entity_id,current_edition_id,version,updated_at) VALUES (?,?,?,1,?) ON CONFLICT(user_id,entity_id) DO UPDATE SET current_edition_id=excluded.current_edition_id,version=memory_entity_heads.version+1,updated_at=excluded.updated_at",
                            (user, entity_id, record, self.now))
            self.edge(user, "record:"+record, "entity:"+entity_id, "about")
            self.edge(user, "entity:"+identity[1]+":"+name, "entity:"+entity_id, "same_identity")
        self.edge(user, "record:"+record, "path:"+path, "stored_at")
        return record

    def apply_unit(self, unit):
        user, target, rows = unit["user_id"], unit["target"], unit["rows"]
        unit_key = f"{user}:{target}"
        payload = digest(json.dumps(unit, sort_keys=True, ensure_ascii=False))
        prior = self.db.execute("SELECT payload_hash FROM memory_cleanup_units WHERE migration_id=? AND unit_key=?", (self.migration_id, unit_key)).fetchone()
        if prior:
            if prior[0] != payload:
                raise ValueError("Resumed plan differs from the committed unit.")
            return False
        with self.db:
            for row in rows:
                live = self.db.execute("SELECT * FROM memory_files WHERE id=?", (row["id"],)).fetchone()
                if not live or live["content"] != row["content"] or live["path"] != row["path"]:
                    raise ValueError("Concurrent or unplanned edit detected; unit rolled back.")
            keeper = next(row for row in rows if row["id"] == unit["keeper"])
            # Remove duplicate physical rows only after preserve() proved byte parity.
            for row in rows:
                if row["id"] != keeper["id"]:
                    self.db.execute("DELETE FROM memory_record_meta WHERE memory_file_id=?", (row["id"],))
                    self.db.execute("DELETE FROM memory_files WHERE id=?", (row["id"],))
            self.db.execute("UPDATE memory_files SET path=?,content=? WHERE id=?", (target, unit["content"], keeper["id"]))
            record = self._metadata(keeper["id"], user, target, unit["content"], keeper["updated_at"])
            for row in rows:
                source_id = stable_id(f"{self.fingerprint}:file:{row['id']}")
                self.edge(user, "record:"+record, "source:"+source_id, "preserves", "source:"+source_id)
                if row["path"] != target:
                    self.db.execute("INSERT INTO memory_path_aliases (user_id,old_path,target_record_id,alias_type,migration_id,created_at) VALUES (?,?,?,'moved',?,?) ON CONFLICT(user_id,old_path) DO UPDATE SET target_record_id=excluded.target_record_id,target_entity_id=NULL,migration_id=excluded.migration_id",
                        (user, row["path"], record, self.migration_id, self.now))
                original_meta = self.original.execute("SELECT * FROM memory_record_meta WHERE memory_file_id=?", (row["id"],)).fetchone()
                if original_meta and original_meta["record_id"] != record:
                    old = original_meta["record_id"]
                    self.edge(user, "record:"+old, "record:"+record, "consolidated_into")
                    self.db.execute("INSERT INTO memory_record_links (user_id,source_record_id,target_record_id,relation_type,source_anchor,created_at) VALUES (?,?,?,'consolidated_into',?,?)", (user,old,record,row["path"],self.now))
                    self.db.execute("UPDATE memory_path_aliases SET target_record_id=? WHERE user_id=? AND target_record_id=?", (record,user,old))
            if len(rows) > 1 and len({r["content"] for r in rows}) > 1:
                self.issue(user, "editions:"+target, "alternative_editions", ["path:"+target]+["source:"+stable_id(f"{self.fingerprint}:file:{r['id']}") for r in rows],
                           "Distinct source editions preserved; differences are not automatically resolved as facts.")
            if target != keeper["path"] or unit["content"] != keeper["content"]:
                self.db.execute('INSERT INTO memory_revisions (user_id,path,author,action,"before","after",request_id,created_at,record_id,migration_id) VALUES (?,?,\'owner_migration\',\'consolidate\',?,?,?,?,?,?)',
                    (user,target,keeper["content"],unit["content"],self.migration_id,self.now,record,self.migration_id))
            self.db.execute("INSERT INTO memory_cleanup_units VALUES (?,?,?,?)", (self.migration_id,unit_key,payload,self.now))
            self.db.execute("UPDATE memory_migration_runs SET phase='cutover',committed_count=committed_count+1,updated_at=? WHERE plan_id=?", (self.now,self.migration_id))
        return True

    def cleanup_history(self):
        files = [dict(row) for row in self.db.execute("SELECT * FROM memory_files")]
        retired = retired_monoliths(files)
        with self.db:
            for row in files:
                if not row["path"].startswith("/memories/.") and (row["user_id"], row["path"]) not in retired:
                    continue
                capsule = self.db.execute("SELECT content,content_hash FROM memory_sources WHERE user_id=? AND original_path=? AND content_hash=?", (row["user_id"],row["path"],digest(row["content"]))).fetchone()
                if not capsule or capsule[0] != row["content"] or capsule[1] != digest(row["content"]):
                    raise ValueError("An archive changed or was not preserved; retirement aborted.")
                self.db.execute("DELETE FROM memory_record_meta WHERE memory_file_id=?", (row["id"],))
                self.db.execute("DELETE FROM memory_files WHERE id=?", (row["id"],))
                if (row["user_id"], row["path"]) in retired:
                    self.issue(row["user_id"], "legacy-source:"+row["path"], "legacy_source_retired",
                               ["path:"+row["path"]],
                               "Superseded monolith preserved byte-for-byte as an original source, outside the active tree. Unique legacy details remain searchable; no semantic reconciliation or factual deletion is implied.")
            # An orphan embedding may carry the ONLY surviving source text.
            orphaned = self.db.execute("SELECT e.* FROM message_embeddings e LEFT JOIN messages m ON m.id=e.message_id LEFT JOIN sessions s ON s.id=e.session_id WHERE m.id IS NULL OR s.id IS NULL").fetchall()
            for row in orphaned:
                self.db.execute("INSERT OR IGNORE INTO memory_sources VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (stable_id(f"{self.fingerprint}:embedding:{row['id']}"),row["user_id"],f"{self.fingerprint}:embedding:{row['id']}",
                     f"message:{row['message_id']}",row["text"],digest(row["text"]),"orphan_message",packed(row),self.migration_id,self.now))
                self.db.execute("DELETE FROM message_embeddings WHERE id=?", (row["id"],))
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='observations_fts'").fetchone():
                self.db.execute("DELETE FROM observations_fts WHERE rowid IN (SELECT id FROM observations WHERE deleted_at IS NOT NULL)")
            for row in self.original.execute("SELECT * FROM memory_capture_jobs WHERE state <> 'committed'"):
                self.issue(row["user_id"],f"capture:{row['id']}","uncommitted_capture",[f"capture:{row['id']}"],
                           "Legacy capture lacks a committed result. Evidence retained; operator reconciliation required, never blind replay.")
                self.db.execute("UPDATE memory_capture_jobs SET state='needs_review',error=? WHERE id=? AND state<>'committed'",
                                ("Lossless migration: inspect original evidence before replay.",row["id"]))
            # Rebind old revision-only ledger aliases to the actual live record.
            aliases = self.db.execute("SELECT a.* FROM memory_path_aliases a LEFT JOIN memory_record_meta m ON m.user_id=a.user_id AND m.record_id=a.target_record_id WHERE m.id IS NULL").fetchall()
            for a in aliases:
                rev = self.original.execute("SELECT path FROM memory_revisions WHERE user_id=? AND record_id=? AND path NOT LIKE '/memories/.%' ORDER BY id DESC LIMIT 1", (a["user_id"],a["target_record_id"])).fetchone()
                if not rev:
                    self.issue(a["user_id"],f"alias:{a['id']}","unresolved_alias",[a["old_path"]],"No surviving target revision; original alias preserved in snapshots.")
                    continue
                target = canonical_path(rev[0], self.now)
                m = self.db.execute("SELECT m.record_id FROM memory_record_meta m JOIN memory_files f ON f.id=m.memory_file_id WHERE m.user_id=? AND f.path=?", (a["user_id"],target)).fetchone()
                if m:
                    self.db.execute("UPDATE memory_path_aliases SET target_record_id=? WHERE id=?", (m[0],a["id"]))

    def build_graph(self):
        """Current catalog plus explicit references. Historical repairs stay out."""
        with self.db:
            for old_path, target in self.path_mappings.items():
                records = self.db.execute("SELECT m.user_id,m.record_id FROM memory_record_meta m JOIN memory_files f ON f.id=m.memory_file_id WHERE f.path=?", (target,)).fetchall()
                if len(records) != 1:
                    raise ValueError("Explicit path mapping has no unique live target: "+target)
                m = records[0]
                self.db.execute("INSERT INTO memory_path_aliases (user_id,old_path,target_record_id,alias_type,migration_id,created_at) VALUES (?,?,?,'renamed',?,?) ON CONFLICT(user_id,old_path) DO UPDATE SET target_record_id=excluded.target_record_id,target_entity_id=NULL", (m["user_id"],old_path,m["record_id"],self.migration_id,self.now))
            entities = self.db.execute("SELECT * FROM memory_entities ORDER BY id").fetchall()
            by_user = defaultdict(list)
            for entity in entities:
                names = [entity["canonical_name"], *json.loads(entity["aliases"])]
                for name in names:
                    if len(name) >= 4:
                        by_user[entity["user_id"]].append((entity["id"], re.compile(rf"(?<!\w){re.escape(name)}(?!\w)",re.IGNORECASE)))
            for obs in self.db.execute("SELECT * FROM observations WHERE deleted_at IS NULL").fetchall():
                user,node = obs["user_id"],f"observation:{obs['id']}"
                self.edge(user,node,"entity:"+obs["subject"],"about")
                for mid in json.loads(obs["message_ids"]):
                    found = self.db.execute("SELECT m.id FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.id=? AND s.user_id=? AND s.triggered_by='user' AND m.role='user'",(mid,user)).fetchone()
                    if found:
                        self.edge(user,node,f"message:{mid}","evidenced_by",f"message:{mid}")
                    else:
                        self.issue(user,f"missing-message:{obs['id']}:{mid}","missing_message",[node,f"message:{mid}"],"Stored message reference does not resolve to an owner message. No evidence was invented.")
                for sid in json.loads(obs["source_ids"]):
                    if self.db.execute("SELECT 1 FROM observations WHERE id=? AND user_id=?",(sid,user)).fetchone():
                        self.edge(user,node,f"observation:{sid}","derived_from")
                if obs["superseded_by"]:
                    self.edge(user,node,f"observation:{obs['superseded_by']}","superseded_by")
                if not json.loads(obs["sources"]) and not json.loads(obs["message_ids"]) and not json.loads(obs["source_ids"]) and not json.loads(obs["premises"]):
                    self.issue(user,f"unsourced:{obs['id']}","legacy_unsourced",[node],"Legacy claim has no recorded source. Preserved unchanged; retrieval is not proof of verification.")
                for eid,pattern in by_user[user]:
                    if pattern.search(obs["content"]):
                        self.edge(user,node,"entity:"+eid,"mentions",node)
                subject_name = obs["subject"].split(":",1)[-1]
                for entity in entities:
                    if entity["user_id"] == user and slugify(subject_name) in {slugify(n) for n in [entity["canonical_name"], *json.loads(entity["aliases"])]}:
                        self.edge(user,"entity:"+obs["subject"],"entity:"+entity["id"],"same_identity")
            live = {(f["user_id"],f["path"]):f for f in self.db.execute("SELECT * FROM memory_files")}
            for (user,path),row in live.items():
                node="path:"+path
                from app.services.memory_passages import passages
                record = self.db.execute("SELECT record_id FROM memory_record_meta WHERE user_id=? AND memory_file_id=?", (user,row["id"])).fetchone()[0]
                for part in passages(record, row["content"]):
                    self.db.execute("INSERT OR IGNORE INTO memory_passages VALUES (?,?,?,?,?,?,?,?,?,0)",
                                    (part["id"],user,record,path,part["kind"],part["text"],part["mentioned_on"].isoformat() if part["mentioned_on"] else None,part["start_line"],part["end_line"]))
                    passage_ref = "passage:"+part["id"]
                    self.edge(user,passage_ref,"record:"+record,"part_of")
                    for eid,pattern in by_user[user]:
                        if pattern.search(part["text"]):
                            self.edge(user,passage_ref,"entity:"+eid,"mentions",passage_ref)
                for eid,pattern in by_user[user]:
                    if pattern.search(row["content"]):
                        self.edge(user,node,"entity:"+eid,"mentions",node)
                for ref in set(re.findall(r"/memories/[\w./-]+\.md",row["content"])):
                    if (user,ref) in live:
                        self.edge(user,node,"path:"+ref,"references",node)
                    else:
                        alias=self.db.execute("SELECT f.path FROM memory_path_aliases a JOIN memory_record_meta m ON m.record_id=a.target_record_id AND m.user_id=a.user_id JOIN memory_files f ON f.id=m.memory_file_id WHERE a.user_id=? AND a.old_path=?",(user,ref)).fetchone()
                        if alias:
                            self.edge(user,node,"path:"+alias[0],"references",node)
                        else:
                            source = self.db.execute("SELECT id FROM memory_sources WHERE user_id=? AND original_path=?",(user,ref)).fetchone()
                            if source:
                                self.edge(user,node,"source:"+source[0],"references",node)
                            else:
                                self.issue(user,"path-ref:"+ref,"unresolved_path",[node,ref],"Document references an unresolved historical path; keep the text and resolve the identity explicitly.")
                for mid in set(re.findall(r"message:(\d+)",row["content"])):
                    found=self.db.execute("SELECT 1 FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.id=? AND s.user_id=?",(mid,user)).fetchone()
                    if found:
                        self.edge(user,node,"message:"+mid,"cites",node)
                    else:
                        self.issue(user,f"document-message:{row['id']}:{mid}","missing_message",
                                   [node,"message:"+mid],"Document cites a missing source message. Text retained; this is not verified evidence.")

    def rebuild_finance_views(self):
        """Separate raw historical tables from typed calculations, without inferring money."""
        from app.services.finance_records import ROOT, MARKER, VIEW, parse, views
        users = [r[0] for r in self.db.execute("SELECT DISTINCT user_id FROM memory_files")]
        with self.db:
            for user in users:
                files = self.db.execute("SELECT * FROM memory_files WHERE user_id=? AND path LIKE '/memories/finance/%'", (user,)).fetchall()
                records = [parse(r["content"]) for r in files if r["path"].startswith(ROOT) and MARKER in r["content"]]
                if not records:
                    continue
                months = [r["path"].rsplit("/",1)[-1][:-3] for r in files if r["path"].startswith("/memories/finance/legacy/")]
                for path,after in views(records, months).items():
                    old = self.db.execute("SELECT * FROM memory_files WHERE user_id=? AND path=?", (user,path)).fetchone()
                    before = old["content"] if old else ""
                    if old and VIEW not in before:
                        raise ValueError("Unpreserved finance source at generated destination: "+path)
                    if before == after:
                        continue
                    if old:
                        file_id = old["id"]
                        self.db.execute("UPDATE memory_files SET content=? WHERE id=?", (after,file_id))
                    else:
                        file_id = self.db.execute("INSERT INTO memory_files (user_id,path,content,updated_at) VALUES (?,?,?,?)", (user,path,after,self.now)).lastrowid
                    record = self._metadata(file_id,user,path,after,old["updated_at"] if old else self.now)
                    self.db.execute('INSERT INTO memory_revisions (user_id,path,author,action,"before","after",request_id,created_at,record_id,migration_id) VALUES (?,?,\'finance_projection\',\'render\',?,?,?,?,?,?)',
                                    (user,path,before,after,self.migration_id,self.now,record,self.migration_id))
                    monthly = parse_monthly_path(path)
                    if monthly and monthly.slug == "ledger":
                        old_path = f"/memories/finance/ledger/{monthly.period.canonical}.md"
                        self.db.execute("INSERT INTO memory_path_aliases (user_id,old_path,target_record_id,alias_type,migration_id,created_at) VALUES (?,?,?,'moved',?,?) ON CONFLICT(user_id,old_path) DO UPDATE SET target_record_id=excluded.target_record_id,target_entity_id=NULL", (user,old_path,record,self.migration_id,self.now))

    def flag_similar_claims(self):
        """Existing vectors suggest review candidates; similarity never deletes facts."""
        import numpy as np
        groups = defaultdict(list)
        for row in self.db.execute("SELECT id,user_id,subject,domain,embedding FROM observations WHERE deleted_at IS NULL AND embedding IS NOT NULL"):
            if len(row["embedding"]) % 4:
                continue
            vector = np.frombuffer(row["embedding"],dtype=np.float32)
            norm = np.linalg.norm(vector)
            if norm and np.isfinite(norm):
                groups[(row["user_id"],row["subject"],row["domain"],len(vector))].append((row["id"],vector/norm))
        with self.db:
            for (user, *_), rows in groups.items():
                if len(rows)<2:
                    continue
                matrix = np.stack([row[1] for row in rows])
                # Strict bounded computation; no full square similarity matrix.
                for start in range(0,len(rows),128):
                    scores=matrix[start:start+128] @ matrix.T
                    for i,j in zip(*np.where(scores>=0.93)):
                        a,b=start+int(i),int(j)
                        if a>=b:
                            continue
                        refs=[f"observation:{rows[a][0]}",f"observation:{rows[b][0]}"]
                        self.issue(user,f"similar:{rows[a][0]}:{rows[b][0]}","similar_claims",refs,
                                   f"Stored-vector similarity {float(scores[i,j]):.4f}. Candidate duplicate, different event, or conflicting value; both originals remain unchanged.")
                        self.edge(user,refs[0],refs[1],"similar_to")

    def flag_course_discrepancies(self):
        """A memory name and timetable code disagree; never silently merge courses."""
        slots = defaultdict(set)
        for row in self.db.execute("SELECT code,name FROM course_slots WHERE active=1"):
            slots[row[0]].add(row[1])
        with self.db:
            for row in self.db.execute("SELECT * FROM memory_files WHERE path LIKE '/memories/academic/courses/%'"):
                code = row["path"].rsplit("/",1)[1][:-3]
                title = row["content"].splitlines()[0].removeprefix("# ")
                name = re.split(r"\s+[—–-]\s+", title, maxsplit=1)[-1]
                if code in slots and not any(slugify(name)==slugify(n) for n in slots[code]):
                    self.issue(row["user_id"],"course-name:"+row["path"],"course_identity_conflict",
                               ["path:"+row["path"]],
                               f"Course {code}: memory title says {name!r}; active timetable says {sorted(slots[code])!r}. Possible translation or misfiled notes. Confirm against original material before attributing lectures; neither source was changed.")

    def verify(self):
        """Compare all fact/history tables, source payloads and surviving rows."""
        for row in self.original.execute("SELECT * FROM memory_files"):
            original = self.db.execute("SELECT * FROM memory_sources WHERE source_key=?",(f"{self.fingerprint}:file:{row['id']}",)).fetchone()
            if not original or original["content"] != row["content"] or original["content_hash"] != digest(row["content"]) or original["original_row"] != packed(row):
                raise ValueError(f"Original not preserved exactly: file {row['id']}")
        changed = {"memory_files","memory_sources","memory_passages","memory_issues","memory_record_meta","memory_path_aliases","memory_entities","memory_entity_heads","memory_record_links","memory_capture_jobs","memory_migration_runs","memory_graph_edges","message_embeddings","observations_fts","observations_fts_data","observations_fts_idx","observations_fts_content","observations_fts_docsize","observations_fts_config"}
        checked = []
        tables = [r[0] for r in self.original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            if table in changed:
                continue
            max_id = self.original.execute("SELECT max(id) FROM memory_revisions").fetchone()[0] if table == "memory_revisions" else None
            if self.table_hash(self.original,table) != self.table_hash(self.db,table,before_id=max_id):
                raise ValueError("Unplanned information change in table: "+table)
            checked.append(table)
        for row in self.original.execute("SELECT * FROM message_embeddings"):
            surviving=self.db.execute("SELECT * FROM message_embeddings WHERE id=?",(row["id"],)).fetchone()
            if surviving:
                if packed(surviving)!=packed(row):
                    raise ValueError("Surviving embedding changed.")
            else:
                source=self.db.execute("SELECT original_row FROM memory_sources WHERE source_key=?",(f"{self.fingerprint}:embedding:{row['id']}",)).fetchone()
                if not source or source[0]!=packed(row):
                    raise ValueError("Orphan embedding not preserved byte-for-byte.")
        if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity verification failed.")
        violations = [tuple(row) for row in self.db.execute("PRAGMA foreign_key_check")]
        if violations:
            raise ValueError(f"Foreign key violations remain: {violations[:5]}")
        stale = self.db.execute("SELECT f.path,f.content,m.content_hash FROM memory_files f JOIN memory_record_meta m ON m.memory_file_id=f.id").fetchall()
        if any(digest(r[1])!=r[2] for r in stale):
            raise ValueError("Catalog hash mismatch.")
        missing_meta = self.db.execute("SELECT count(*) FROM memory_files f LEFT JOIN memory_record_meta m ON m.memory_file_id=f.id AND m.user_id=f.user_id WHERE m.id IS NULL").fetchone()[0]
        if missing_meta:
            raise ValueError("Active documents missing catalog metadata.")
        dangling_entities = self.db.execute("SELECT count(*) FROM memory_record_meta m LEFT JOIN memory_entities e ON e.id=m.entity_id AND e.user_id=m.user_id WHERE m.entity_id IS NOT NULL AND e.id IS NULL").fetchone()[0]
        dangling_heads = self.db.execute("SELECT count(*) FROM memory_entity_heads h LEFT JOIN memory_entities e ON e.id=h.entity_id AND e.user_id=h.user_id LEFT JOIN memory_record_meta m ON m.record_id=h.current_edition_id AND m.user_id=h.user_id WHERE e.id IS NULL OR m.id IS NULL OR m.entity_id<>h.entity_id").fetchone()[0]
        if dangling_entities or dangling_heads:
            raise ValueError("Catalog entity/head relationship is unresolved.")
        from app.services.memory_verify import verify_document
        structure = [str(finding) for row in self.db.execute("SELECT path,content FROM memory_files")
                     for finding in verify_document(row[0],row[1])]
        report={"source_sha256":self.fingerprint,"migration_id":self.migration_id,
                "source_unchanged":file_hash(self.source)==self.fingerprint,
                "original_documents_preserved":len(list(self.original.execute("SELECT id FROM memory_files"))),
                "unchanged_tables_verified":checked,"integrity_check":"ok","foreign_key_violations":0,
                "structural_findings":structure,"active_documents_missing_metadata":missing_meta,
                "provider_calls":0}
        for table in ("memory_files","memory_sources","memory_passages","memory_record_meta","memory_entities","memory_entity_heads","memory_graph_edges","memory_issues"):
            report[table]=self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        report["issue_counts"]={row[0]:row[1] for row in self.db.execute("SELECT kind,count(*) FROM memory_issues GROUP BY kind")}
        if not report["source_unchanged"]:
            raise ValueError("Source changed during migration.")
        return report

    def run(self, *, stop_after: int | None = None):
        run = self.db.execute("SELECT phase FROM memory_migration_runs WHERE plan_id=?",(self.migration_id,)).fetchone()
        if run[0]=="completed":
            return self.verify()
        self.preserve()
        files=[dict(row) for row in self.original.execute("SELECT * FROM memory_files ORDER BY id")]
        committed=0
        for unit in build_plan(files):
            if self.apply_unit(unit):
                committed+=1
                if stop_after is not None and committed>=stop_after:
                    return {"phase":"cutover","committed_this_run":committed}
        self.cleanup_history()
        self.rebuild_finance_views()
        self.build_graph()
        self.flag_similar_claims()
        self.flag_course_discrepancies()
        report=self.verify()
        with self.db:
            self.db.execute("UPDATE memory_migration_runs SET phase='completed',reconciliation_report=?,updated_at=? WHERE plan_id=?",
                            (json.dumps(report),self.now,self.migration_id))
        return report
