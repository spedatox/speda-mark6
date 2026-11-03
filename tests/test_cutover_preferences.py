import copy
import json
import logging
import sqlite3
import pytest
from sqlalchemy import select,func
from app.config import Settings,JSONFormatter
from app.models.model_policy import ModelPolicyState
from app.models.delivery import TelegramLink,TelegramOwner,TelegramInput
from app.models.voice import VoicePreference
from app.models.automation import Automation,AutomationOperation
from app.services.projects import cell_image_for
from app.services.coordination_budget import CoordinationError
from scripts.import_native_preferences import import_preferences,import_automations
from tests.test_delivery import channel,pair,event
from tests.test_coordination import reply


async def test_offline_preferences_preserve_voices_owner_and_model_aliases(memory):
    runtime={'agent_models':{'nightcrawler':'zai:glm-5.3-flash'},'telegram_models':{'speda':'zai:glm-4.5'},
        'legion_models':{'forge_coder':'zai:glm-5.3','autobot':'zai:glm-5.3'},
        'voice_overrides':{'nightcrawler':{'voice_id':'elevenlabs:eleven_v4:testvoice','speed':1.1}},
        'telegram_owner_id':'12345678901','telegram_chat_id':'12345678901','telegram_started':['speda','nightcrawler']}
    original=copy.deepcopy(runtime)
    report=await import_preferences(memory.sessions,runtime)
    assert runtime==original and report['voice_pins']==1 and report['telegram_links']==2
    async with memory.sessions() as db:
        state=await db.get(ModelPolicyState,1)
        assert state.document['worker_models']=={'autobot':'zai:glm-5.3'}
        assert state.document['telegram_models']=={'speda':'zai:glm-4.5'}
        assert (await db.get(TelegramOwner,1)).sender_id==12345678901
        assert (await db.get(TelegramLink,(1,'friday'))).chat_id==12345678901
        assert (await db.get(VoicePreference,(1,'friday'))).settings==runtime['voice_overrides']['nightcrawler']
    with pytest.raises(ValueError,match='already exists'): await import_preferences(memory.sessions,runtime)


async def test_polling_cutover_watermark_does_not_replay_old_updates(memory,monkeypatch):
    bits,gateway,delivery,calls=await channel(memory,[reply('A new update')])
    await pair(gateway)
    monkeypatch.setattr('app.core.runtime_state.get_telegram_cutover_offset',lambda agent:10)
    old=await gateway.receive(1,'sentinel',event('Already acknowledged by the old poller',9))
    assert old['duplicate'] and not bits.client.calls
    async with memory.sessions() as db: assert await db.scalar(select(func.count()).select_from(TelegramInput))==0
    current=await gateway.receive(1,'sentinel',event('New owner input',10))
    await bits.coordinator.live[current['run_id']]
    assert len(bits.client.calls)==1
    await bits.coordinator.shutdown()


def test_individual_bot_tokens_in_http_diagnostics_are_redacted(monkeypatch):
    from app import config
    secret='123456:fixture-only-private-bot-token'
    monkeypatch.setattr(config,'settings',Settings(_env_file=None,telegram_bot_tokens=json.dumps({'friday':secret})))
    record=logging.LogRecord('httpx',logging.INFO,'',0,'POST https://api.telegram.org/bot'+secret+'/sendMessage',(),None)
    output=JSONFormatter().format(record)
    assert secret not in output and '[REDACTED]' in output


def test_per_role_cell_images_preserve_installed_toolchains_and_reject_unknown_roles():
    settings=Settings(_env_file=None,project_cell_image='shared:fixed',project_cell_images='{"coder":"code:fixed","pentester":"security:fixed"}')
    assert cell_image_for(settings,'coder')=='code:fixed'
    assert cell_image_for(settings,'pentester')=='security:fixed'
    assert cell_image_for(settings,'reviewer')=='shared:fixed'
    settings.project_cell_images='{"unrestricted":"host:latest"}'
    with pytest.raises(CoordinationError):cell_image_for(settings,'coder')


async def test_existing_automation_view_keeps_remote_identity_without_republishing(memory,tmp_path):
    source=tmp_path/'legacy.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE automations(id INTEGER,user_id INTEGER,agent_id TEXT,n8n_workflow_id TEXT,name TEXT,kind TEXT,intent TEXT,spec TEXT,active INTEGER,created_at TEXT)')
        db.execute('INSERT INTO automations VALUES(7,1,?,?,?,?,?,?,1,?)',('nightcrawler','existing-remote','Briefing','schedule','Keep the existing schedule',json.dumps({'cron':'0 8 * * *'}),'2026-10-01 10:00:00'))
    original=source.read_bytes()
    report=await import_automations(source,memory.sessions,{'existing-remote':{'id':'existing-remote','active':True,'nodes':[]}})
    assert report=={'definitions':1,'remote_workflows_modified':0,'historical_runs_replayed':0,'missing_remote_definitions':[]}
    assert source.read_bytes()==original
    async with memory.sessions() as db:
        row=await db.get(Automation,7)
        assert row.n8n_workflow_id=='existing-remote' and row.agent_id=='friday' and row.active
        op=await db.scalar(select(AutomationOperation))
        assert op.stage=='done' and op.workflow['id']=='existing-remote'
