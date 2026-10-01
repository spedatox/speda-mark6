# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit offline cutover: python -m scripts.cleanup_memory_corpus --source ... --output ... --report ..."""
import argparse
import json
from pathlib import Path
from app.services.memory_cleanup import CorpusCleanup


def export_corpus(database: Path, directory: Path):
    """Read-only, plain Markdown export for inspection/Obsidian; no second writer."""
    import sqlite3
    directory=directory.resolve()
    directory.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(database.resolve().as_uri()+"?mode=ro",uri=True)
    db.row_factory=sqlite3.Row
    try:
        index=["# Memory corpus export", "", "Read-only inspection copy. The database and evidence-bound writers remain authoritative.", "", "## Current documents"]
        for row in db.execute("SELECT * FROM memory_files ORDER BY path"):
            target=(directory / row["path"].removeprefix("/memories/")).resolve()
            if not target.is_relative_to(directory):
                raise ValueError("Unsafe export path: "+row["path"])
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(row["content"],encoding="utf-8",newline="")
            index.append(f"- [{row['path']}]({target.relative_to(directory).as_posix()})")
        source_dir=directory/"sources"
        source_dir.mkdir(exist_ok=True)
        for row in db.execute("SELECT * FROM memory_sources ORDER BY id"):
            target=(source_dir/(row["id"]+".md")).resolve()
            if not target.is_relative_to(source_dir):
                raise ValueError("Unsafe source ID in export")
            target.write_text(row["content"],encoding="utf-8",newline="")
        from urllib.parse import quote
        record_paths={row["record_id"]:row["path"] for row in db.execute("SELECT m.record_id,f.path FROM memory_record_meta m JOIN memory_files f ON m.memory_file_id=f.id")}
        passage_paths={row["id"]:row["path"] for row in db.execute("SELECT id,path FROM memory_passages WHERE retired=0")}
        connections=["# People, projects and connected documents", "", "Links indicate document mentions and shared evidence; they do not establish causation. The full typed graph is in connections.json.", ""]
        for entity in db.execute("SELECT e.*,h.current_edition_id FROM memory_entities e LEFT JOIN memory_entity_heads h ON e.id=h.entity_id AND e.user_id=h.user_id ORDER BY canonical_name"):
            connections.extend(["## "+entity["canonical_name"], "", "`entity:"+entity["id"]+"`", ""])
            paths=set()
            if entity["current_edition_id"] in record_paths:
                paths.add(record_paths[entity["current_edition_id"]])
            for edge in db.execute("SELECT source_ref FROM memory_graph_edges WHERE target_ref=? AND relation_type IN ('mentions','about')",("entity:"+entity["id"],)):
                kind,_,ident=edge[0].partition(":")
                path=record_paths.get(ident) if kind=="record" else passage_paths.get(ident) if kind=="passage" else ident if kind=="path" else None
                if path and path.startswith('/memories/'):
                    paths.add(path)
            for path in sorted(paths):
                connections.append(f"- [{path}]({quote(path.removeprefix('/memories/'))})")
            connections.append("")
        (directory/"CONNECTIONS.md").write_text("\n".join(connections),encoding="utf-8")
        index.extend(["", "## Connections and historical originals", "", "- [People/project connections](CONNECTIONS.md)", "- Original source payloads: sources/ (sources-manifest.json identifies every original path and hash).", "- unresolved-issues.json preserves unresolved source discrepancies."])
        (directory/"INDEX.md").write_text("\n".join(index)+"\n",encoding="utf-8")
        for table,name in (("memory_sources","sources-manifest.json"),("memory_graph_edges","connections.json"),("memory_issues","unresolved-issues.json"),("memory_passages","passages.json")):
            rows=[]
            for row in db.execute("SELECT * FROM "+table):
                payload=dict(row)
                if table=="memory_sources":
                    payload.pop("content",None)
                    payload.pop("original_row",None)
                rows.append(payload)
            (directory/name).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    finally:
        db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--path-mappings",type=Path,help="Reviewed historical path → canonical path JSON. No guessed identity merges.")
    parser.add_argument("--export",type=Path,help="Optional read-only Markdown vault export for inspection.")
    args=parser.parse_args()
    mappings=json.loads(args.path_mappings.read_text(encoding="utf-8")) if args.path_mappings else None
    migration=CorpusCleanup(args.source,args.output,path_mappings=mappings)
    try:
        report=migration.run()
    finally:
        migration.close()
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    if args.export:
        export_corpus(args.output,args.export)
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__=="__main__":
    main()
