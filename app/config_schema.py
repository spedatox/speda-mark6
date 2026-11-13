"""Complete Settings-derived catalog. New fields cannot silently disappear from UI."""
import json
import types
from pathlib import Path
from typing import get_args,get_origin,Union
from app.config import Settings

GROUPS={
    'providers':('Model providers','Provider credentials, endpoints and startup defaults. Native model policy can override startup defaults.'),
    'memory':('Memory and library','Iacon, Hisar and original document access. Review and spending policies have their own versioned controls below.'),
    'runtime':('Backend and storage','Network binding, database, logging, authentication and output paths. Saving never migrates or replaces a dataset.'),
    'connections':('Connections and tools','OAuth application settings, MCP capabilities, grants and isolated service endpoints.'),
    'delivery':('Automation and delivery','n8n, Telegram, device pushes and owner bindings.'),
    'voice':('Speech and canvas','Speech providers, voices, locale and visual canvas presentation. Paid speech also needs a global spending allowance.'),
    'projects':('Execution and workspaces','Existing Forge execution settings. Remaining project release gates are tracked separately.'),
    'news':('News and domain services','News quotas, feeds, navigation, location and independent domain providers.'),
    'other':('Additional settings','Automatically included settings without a specialized group.'),
}


def group_for(key):
    if key.startswith(('anthropic','openai','gemini','zai','deepseek','nvidia','kimi','moonshot','ollama','vertex','llm_','chat_max')): return 'providers'
    if key.startswith(('memory_','iacon_','hisar_','library_')): return 'memory'
    if key.startswith(('project_','orchestration_')): return 'projects'
    if key.startswith(('tts_','voice_canvas_','azure_speech','elevenlabs')): return 'voice'
    if key.startswith(('telegram_','n8n_','speda_callback','fcm_')): return 'delivery'
    if key.startswith(('mcp_','google_client','google_refresh','google_oauth','microsoft_','notion_','playwright_','browser_','onyx_','sandbox_','gpt_researcher','shannon_')): return 'connections'
    if key in {'database_url','http_host','http_port','debug','log_level','owner_timezone','speda_api_key','cors_allowed_origins','temp_outputs_dir','external_integrations_enabled','house_party_passphrase','solus_ui_dir'}: return 'runtime'
    if key.startswith(('news','google_maps','owner_home','aircraft','nosyapi','abuse','otx','shodan','hunter','etherscan','blockchair','intelx','brave','alpha_vantage','tavily','exa','github')): return 'news'
    return 'other'


def secret_field(key):
    return key=='database_url' or any(word in key for word in ('api_key','secret','password','passphrase','token'))


def catalog():
    path=Path(__file__).with_name('config_labels.json')
    labels=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    groups={key:{'id':key,'label':label,'blurb':help,'fields':[]} for key,(label,help) in GROUPS.items()}
    schema=Settings.model_json_schema(by_alias=False)
    for key,info in Settings.model_fields.items():
        annotation=info.annotation
        args=get_args(annotation) if get_origin(annotation) in {Union,types.UnionType} else (annotation,)
        base=next((t for t in args if t is not type(None)),str)
        secret=secret_field(key)
        kind='password' if secret else 'bool' if base is bool else 'int' if base is int else 'float' if base is float else 'url' if key.endswith(('_url','_redirect')) else 'text'
        old=labels.get(key,{})
        help=info.description or ('Optional; clear the value to use no value.' if type(None) in args else '')
        if key=='database_url': help='Database connection string. A change switches the data source after restart; it does not move or migrate existing data.'
        if key in {'speda_api_key','http_host','http_port','cors_allowed_origins'}: help='A change may require updating the client connection or deployment network mapping after restart.'
        if key in {'mcp_package_pins','telegram_bot_tokens'}: help='JSON object. Use exact package versions for MCP; agent IDs map to private bot tokens for Telegram.'
        field={'key':key,'label':old.get('label',key.replace('_',' ').capitalize()),'type':kind,'secret':secret,
            'requires_restart':True,'help':help,'placeholder':'','options':[], 'nullable':type(None) in args,
            'schema':schema.get('properties',{}).get(key,{}),'env_key':key.upper()}
        if key=='log_level': field.update(type='select',options=['DEBUG','INFO','WARNING','ERROR','CRITICAL'])
        if key=='agent_language': field.update(type='select',options=['','en','tr'],help='Reply language: blank follows the conversation; en is English; tr is Turkish. Applies on restart.')
        if key=='project_cell_backend': field.update(type='select',options=['docker'])
        if key=='owner_timezone':
            from zoneinfo import available_timezones
            field.update(type='select',options=sorted(available_timezones()))
        if key in {'llm_main_model','llm_background_model'}:
            from app.profiles.speda import SPEDAProfile
            field.update(type='select',options=['',*(s.ref for s in SPEDAProfile.model_specs)])
        groups[group_for(key)]['fields'].append(field)
    return [g for g in groups.values() if g['fields']]
