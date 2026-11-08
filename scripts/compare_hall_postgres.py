"""Compare Hall of Records keyword ranks on both copies of a frozen dataset."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.lexical import build_match_query
from app.services.postgres_search import PostgresLibrarySearch, pin_search_snapshot


async def compare(snapshot):
    sqlite = create_async_engine('sqlite+aiosqlite:///' + snapshot.resolve(strict=True).as_uri() + '?mode=ro&uri=true')
    postgres = create_async_engine(os.environ['SPEDA_REHEARSAL_POSTGRES_URL'], hide_parameters=True,
                                   connect_args={'server_settings': {'default_transaction_read_only': 'on'}})
    ranker = PostgresLibrarySearch()
    query = text("""
        SELECT passage_id, bm25(library_passages_fts,0,1,2,0,0,0) AS rank
        FROM library_passages_fts WHERE library_passages_fts MATCH :match
        AND revision_id IN :revisions AND generation_id IN :generations
        ORDER BY rank, passage_id LIMIT 32
    """).bindparams(bindparam('revisions', expanding=True), bindparam('generations', expanding=True))
    checks = 0
    matches = 0
    try:
        async with AsyncSession(sqlite) as before, AsyncSession(postgres) as after:
            editions = (await before.execute(text("""
                SELECT DISTINCT revision_id, generation_id FROM library_passages ORDER BY 1, 2
            """))).all()
            if not editions:
                raise RuntimeError('Expected indexed Hall editions in the frozen dataset')
            terms = (await before.execute(text("""
                SELECT p.revision_id, d.title, substr(p.content, 1, 2000) AS content
                FROM library_passages p JOIN library_revisions r ON r.id = p.revision_id
                JOIN library_documents d ON d.id = r.document_id
                ORDER BY p.position, p.id LIMIT 80
            """))).all()
            await pin_search_snapshot(after)
            for scope in [*[[edition] for edition in editions], editions]:
                revisions, generations = map(list, zip(*scope))
                words = dict.fromkeys(word.casefold() for revision, title, excerpt in terms if revision in revisions
                                      for word in re.findall(r'[^\W\d_]{4,}', title + ' ' + excerpt))
                for phrase in [*list(words)[:16], 'zzspedaqvmrxxcutovercanary']:
                    match = build_match_query(phrase)
                    reference = (await before.execute(query, {'match': match, 'revisions': revisions,
                                                              'generations': generations})).all()
                    actual = await ranker.rank(after, match, revisions, generations, 32)
                    checks += 1
                    matches += list(map(tuple, reference)) == list(map(tuple, actual))
    finally:
        await sqlite.dispose()
        await postgres.dispose()
    return {'ranked_passage_checks': checks, 'exact_matches': matches, 'indexed_edition_generations': len(editions)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    report = asyncio.run(compare(args.snapshot))
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print(f'{report["exact_matches"]}/{report["ranked_passage_checks"]} Hall of Records ranks match exactly.')
    if report['exact_matches'] != report['ranked_passage_checks']:
        raise SystemExit(1)
