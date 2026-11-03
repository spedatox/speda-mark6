"""Offline native views of retained production preferences and scheduler metadata.

No provider calls, bot pairing messages, workflow publication or old job replay.
The input JSON and workflow baselines are private operational snapshots.
"""
import copy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from uuid import uuid5,NAMESPACE_URL
from sqlalchemy import select


async def import_preferences(sessions,runtime,*,owner=1):
    from app.models.delivery import TelegramOwner,TelegramLink
    from app.models.voice import VoicePreference
    from app.models.model_policy import ModelPolicyState
    from app.profiles.registry import ProfileRegistry
    from app.schemas.voice import VoiceAgentPatch
    from app.services.speech_text import parse_voice_ref
    from scripts.import_model_policy import migrate_legacy
    profiles=ProfileRegistry()
    policy,dispositions=migrate_legacy(runtime)
    report={'voice_pins':0,'telegram_links':0,'model_policy_imported':False,'policy_dispositions':dispositions}
    async with sessions() as db,db.begin():
        if await db.get(ModelPolicyState,owner) is not None:
            raise ValueError('Native policy already exists; review it rather than replacing it')
        db.add(ModelPolicyState(user_id=owner,revision=1,document=policy.model_dump(mode='json')))
        report['model_policy_imported']=True
        for agent,values in runtime.get('voice_overrides',{}).items():
            aid=profiles.canonical(agent)
            settings=copy.deepcopy(values)
            languages=settings.pop('languages',{})
            VoiceAgentPatch.model_validate(settings)
            for scoped in [settings,*languages.values()]:
                VoiceAgentPatch.model_validate(scoped)
                if scoped.get('voice_id'): parse_voice_ref(scoped['voice_id'])
            if set(languages)-{'tr','en'}: raise ValueError('Unknown legacy voice language needs review')
            if languages:settings['languages']=languages
            if await db.get(VoicePreference,(owner,aid)) is not None:
                raise ValueError('Native voice pin already exists; do not overwrite it')
            db.add(VoicePreference(user_id=owner,agent_id=aid,version=1,settings=settings))
            report['voice_pins']+=1
        sender=int(runtime.get('telegram_owner_id') or 0)
        chat=int(runtime.get('telegram_chat_id') or sender or 0)
        if sender and sender==chat:
            if await db.get(TelegramOwner,owner) is not None: raise ValueError('Native Telegram identity already exists')
            db.add(TelegramOwner(user_id=owner,sender_id=sender))
            for agent in runtime.get('telegram_started',[]):
                aid=profiles.canonical(agent)
                db.add(TelegramLink(user_id=owner,agent_id=aid,chat_id=sender,version=1))
                report['telegram_links']+=1
        elif sender:
            raise ValueError('Legacy Telegram owner/chat must identify the same private chat')
    return report


async def import_automations(snapshot,sessions,workflows,*,owner=1,timezone='Europe/Istanbul'):
    from app.models.automation import Automation,AutomationOperation
    from app.profiles.registry import ProfileRegistry
    from app.services.automations import secret_refs
    from app.services.coordination_budget import digest
    profiles=ProfileRegistry()
    with sqlite3.connect(Path(snapshot).resolve().as_uri()+'?mode=ro',uri=True) as source:
        source.row_factory=sqlite3.Row
        if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automations'").fetchone():
            return {'definitions':0,'remote_workflows_modified':0}
        rows=[dict(r) for r in source.execute('SELECT * FROM automations WHERE user_id=? ORDER BY id',(owner,))]
    missing=[]
    async with sessions() as db,db.begin():
        for row in rows:
            if await db.get(Automation,row['id']) is not None: raise ValueError('Native automation identity already exists')
            aid=profiles.canonical(row['agent_id'])
            spec=json.loads(row['spec']) if isinstance(row['spec'],str) else row['spec']
            spec={**spec,'name':row['name'],'kind':row['kind'],'intent':row['intent']}
            if spec['kind']=='mail_watch':
                # The legacy gateway acknowledged Gmail messages with this
                # provider label; native local receipts must not replay them.
                spec.setdefault('label','SPEDA-Seen')
            remote=workflows.get(str(row['n8n_workflow_id']))
            # Old databases can retain definitions deleted in n8n. Preserve the
            # identity and desired state, and show drift without recreating it.
            if not remote: missing.append(row['id'])
            identity=uuid5(NAMESPACE_URL,f'speda:legacy-automation:{owner}:{row["id"]}').hex
            record=Automation(id=row['id'],public_key=identity,user_id=owner,agent_id=aid,
                request_id='legacy-'+str(row['id']),request_hash=digest(spec),name=row['name'],spec=spec,
                timezone=timezone,version=1,content_version=1,applied_version=1,
                connection_binding=({'provider':spec.get('mail_provider','google'),'generation':'legacy0'}
                    if spec['kind']=='mail_watch' else {}),
                desired_active=bool(row['active']),active=bool(row['active']) and bool(remote),
                status=('active' if row['active'] else 'paused') if remote else 'reconciliation_required',
                n8n_workflow_id=row['n8n_workflow_id'],created_at=datetime.fromisoformat(row['created_at']))
            db.add(record)
            await db.flush()
            db.add(AutomationOperation(id=identity,automation_id=record.id,version=1,action='import',
                stage='done',workflow=secret_refs(remote or {}),baseline={}))
    return {'definitions':len(rows),'remote_workflows_modified':0,'historical_runs_replayed':0,
        'missing_remote_definitions':missing}
