"""Prepare a new full-data candidate from a retained, consistent legacy snapshot.

The original database is never replaced. All legacy tables remain byte-for-byte
equivalent at the row level; native memory is an additional source-linked view.
No provider, scheduler, notification, worker or historical tool is executed.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from scripts.rehearse_sqlite import snapshot, verify


async def prepare(args):
    source = Path(args.snapshot).resolve(strict=True)
    destination = Path(args.destination).resolve()
    report_path = Path(args.report).resolve()
    if report_path.exists() or report_path in {source, destination}:
        raise ValueError('Report must be new and separate from the databases')
    from scripts.import_memory_snapshot import read_snapshot
    original = read_snapshot(source, args.owner)
    retained = snapshot(source, destination)
    os.environ.update(DATABASE_URL='sqlite+aiosqlite:///' + destination.as_posix(),
        EXTERNAL_INTEGRATIONS_ENABLED='false', MEMORY_CAPTURE_ENABLED='false',
        SPEDA_DATA_DIR=str(destination.parent/'candidate-state'), PYTHONDONTWRITEBYTECODE='1')
    # This standalone process must never inherit real providers or delivery keys.
    for name in ('ANTHROPIC', 'OPENAI', 'GEMINI', 'KIMI', 'MOONSHOT', 'ZAI', 'DEEPSEEK', 'NVIDIA', 'OLLAMA'):
        os.environ[name+'_API_KEY']='not-set' if name=='ANTHROPIC' else ''
    from app.database import engine, init_db, close_db, AsyncSessionLocal
    from app.core.context import AgentContext
    from app.services.memory_store import MemoryStore
    from scripts.migrate_native_rest import migrate
    from scripts.import_memory_snapshot import import_documents, import_history
    from scripts.import_iacon_catalogue import import_catalogue
    try:
        plan=await migrate(engine)
        if plan['unsupported_columns']:
            raise ValueError('Unreviewed schema differences prevent preparation')
        upgrade=await migrate(engine,apply=True)
        await init_db()
        context=AgentContext(user_id=args.owner,session_id=0,request_id='offline-cutover-preparation',
            triggered_by='user',trigger_payload={},output_mode='silent',model='unused',system_prompt='',
            conversation_history=[],db=None,timezone=args.timezone)
        store=MemoryStore(AsyncSessionLocal)
        documents=await import_documents(original,store,context)
        history=await import_history(original,store,context)
        catalogue=await import_catalogue(original,store,context)
        preferences=None
        automations=None
        if args.runtime_snapshot:
            from scripts.import_native_preferences import import_preferences,import_automations
            runtime=json.loads(Path(args.runtime_snapshot).read_text(encoding='utf-8'))
            preferences=await import_preferences(AsyncSessionLocal,runtime,owner=args.owner)
            workflows=json.loads(Path(args.workflows_snapshot).read_text()) if args.workflows_snapshot else {}
            automations=await import_automations(source,AsyncSessionLocal,workflows,owner=args.owner,timezone=args.timezone)
        preservation=verify(source,destination)
        if not preservation['preserved']:
            raise ValueError('Original rows changed; candidate must not be deployed')
        report={'source_sha256':original['sha256'],'initial_candidate':retained,'migration':upgrade,
            'documents':documents,'history':history,'catalogue':catalogue,'preservation':preservation,
            'preferences':preferences,'automations':automations,
            'provider_calls':0,'live_database_replaced':False,
            'requires_final_live_snapshot':True}
        report_path.parent.mkdir(parents=True,exist_ok=True)
        with report_path.open('x',encoding='utf-8') as output:
            json.dump(report,output,ensure_ascii=False,indent=2)
        return {'preservation':preservation,'original_memory_files':len(original['files']),
            'catalogue_documents':catalogue['documents'],'catalogue_entities':catalogue['entities'],
            'catalogue_states':catalogue['states'],'review_required':len(catalogue['review_required']),
            'history':history,'provider_calls':0,'report':str(report_path)}
    finally:
        await close_db()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for option in ('snapshot','destination','report'):
        parser.add_argument('--'+option,required=True)
    parser.add_argument('--owner',type=int,default=1)
    parser.add_argument('--timezone',default='Europe/Istanbul')
    parser.add_argument('--runtime-snapshot',help='Private retained runtime_state.json; imports native preferences without executing effects')
    parser.add_argument('--workflows-snapshot',help='Private JSON map of existing n8n workflow baselines; never republishes them')
    print(json.dumps(asyncio.run(prepare(parser.parse_args()))))
