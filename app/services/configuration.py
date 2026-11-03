"""Owner configuration service; validation, persistence and running/saved state."""
import asyncio
import copy
import json
import logging
import math
from uuid import uuid4
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from pydantic import TypeAdapter,ValidationError
from sqlalchemy.exc import ArgumentError
from app.config import Settings,DeploymentSettings
from app.config_schema import catalog,secret_field
from app.services.config_store import ConfigStore,ConfigConflict,ConfigUnavailable
from app.services.coordination_budget import CoordinationError


class Configuration:
    def __init__(self,settings,*,store=None,instance_id=None):
        self.settings,self.store=settings,store or ConfigStore()
        self.running=settings.model_dump()
        self.boot_overrides=set(self.store.read()['values'])
        self.deployment=DeploymentSettings().model_dump()
        self.instance_id=instance_id or uuid4().hex

    def desired(self,state):
        # Environment/default values are captured at boot; explicit reset means
        # the startup sources will resolve them on the next process start.
        base={**self.running,**{key:self.deployment[key] for key in self.boot_overrides if key in self.deployment}}
        return {**base,**state['values']}

    def snapshot(self):
        state=self.store.read(); desired=self.desired(state); pending=[]; groups=copy.deepcopy(catalog())
        for group in groups:
            for field in group['fields']:
                key=field['key']; value=desired[key]
                changed=value!=self.running[key]
                field.update(is_set=value is not None and value!='',pending_restart=changed,overridden=key in state['values'])
                if changed: pending.append(key)
                sensitive=field['secret']
                if field['type']=='url':
                    try:
                        parsed=urlsplit(str(value or ''))
                        sensitive=bool(parsed.username or parsed.password or parsed.query or parsed.fragment)
                    except ValueError: sensitive=True
                if sensitive:
                    field.update(secret=True,type='password',hint='••••••••' if value else '')
                    field['schema'].pop('default',None)
                else: field['value']=value
        return {'groups':groups,'revision':state['revision'],'instance_id':self.instance_id,
            'restart_required':pending,'setting_count':len(Settings.model_fields),
            'save_semantics':'Saved values apply together on backend restart. Native policies below apply through their existing writers.'}

    def validate(self,values):
        if set(values)-set(Settings.model_fields): raise CoordinationError('config_fields','Unknown configuration keys; reload the configuration catalog.',422)
        clean={}; invalid=[]
        for key,raw in values.items():
            try:
                field=Settings.model_fields[key]
                schema=field.annotation
                value=TypeAdapter(schema).validate_python(raw)
                if isinstance(value,float) and not math.isfinite(value): raise ValueError()
                if isinstance(value,str) and (len(value.encode())>64000 or '\x00' in value): raise ValueError()
                if isinstance(value,str) and value and key.endswith(('_url','_redirect')) and key!='database_url':
                    parsed=urlsplit(value)
                    if parsed.scheme not in {'http','https'} or not parsed.netloc or not parsed.hostname: raise ValueError()
                    parsed.port  # also validates a malformed port
                if isinstance(value,(float,int)) and not isinstance(value,bool) and key not in {'owner_home_lat','owner_home_lng','iacon_telegram_chat_id'}:
                    if value<0 or (key.endswith(('timeout_seconds','max_pages','max_bytes','max_chars','max_images','retention_days')) and value<=0): raise ValueError()
                if key=='owner_home_lat' and value is not None and not -90<=value<=90: raise ValueError()
                if key=='owner_home_lng' and value is not None and not -180<=value<=180: raise ValueError()
                if key=='http_port' and not 1<=value<=65535: raise ValueError()
                if key=='log_level' and value not in {'DEBUG','INFO','WARNING','ERROR','CRITICAL'}: raise ValueError()
                if key=='agent_language' and value not in {'','en','tr'}: raise ValueError()
                if key=='speda_api_key' and (not value.strip() or '\r' in value or '\n' in value): raise ValueError()
                if key=='owner_timezone': ZoneInfo(value)
                if key=='http_host':
                    import ipaddress
                    try: ipaddress.ip_address(value)
                    except ValueError:
                        import re
                        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',value): raise ValueError()
                if key=='project_cell_backend' and value!='docker': raise ValueError()
                if key in {'telegram_bot_tokens','mcp_package_pins','project_cell_images'}:
                    parsed=json.loads(value)
                    if not isinstance(parsed,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in parsed.items()): raise ValueError()
                    if key=='project_cell_images':
                        from types import SimpleNamespace
                        from app.services.projects import cell_image_for
                        cell_image_for(SimpleNamespace(project_cell_images=value,project_cell_image=''),'coder')
                if key=='database_url':
                    from sqlalchemy.engine import make_url
                    if make_url(value).drivername not in {'sqlite+aiosqlite','postgresql+asyncpg'}: raise ValueError()
                clean[key]=value
            except (ValueError,TypeError,KeyError,ValidationError,ArgumentError): invalid.append(key)
        if invalid: raise CoordinationError('config_values','Invalid values for: '+', '.join(invalid)+'. No changes were saved.',422)
        # Run actual field validators and constraints without rereading a live
        # deployment or returning a pydantic error containing a submitted secret.
        try: Settings.model_validate({**self.running,**clean})
        except ValidationError: raise CoordinationError('config_values','Configuration does not satisfy backend field constraints. No changes were saved.',422) from None
        return clean

    async def save(self,values,revision,request_id,reset=()):
        if type(revision) is not int or revision<0: raise CoordinationError('config_revision','Reload configuration and supply its revision.',422)
        if len(values)>len(Settings.model_fields) or len(reset)>len(Settings.model_fields): raise CoordinationError('config_bound','Too many configuration entries',422)
        if set(reset)-set(Settings.model_fields): raise CoordinationError('config_fields','Unknown reset keys',422)
        if set(reset)&set(values): raise CoordinationError('config_fields','A field cannot be changed and reset together',422)
        clean=self.validate(values)
        try: state,duplicate=await asyncio.to_thread(self.store.save,revision,clean,request_id,reset)
        except ConfigConflict as exc: raise CoordinationError('config_conflict',str(exc),409) from None
        except ConfigUnavailable as exc: raise CoordinationError('config_unavailable',str(exc),503) from None
        snapshot=self.snapshot()
        logging.getLogger(__name__).info('configuration_saved',extra={'revision':state['revision'],'keys':sorted(clean),'resets':list(reset)})
        return {'revision':state['revision'],'request_id':request_id,'duplicate':duplicate,'applied_live':[],
            'restart_required':snapshot['restart_required'],'rejected':[],'saved':sorted(clean),'reset':list(reset)}


class PolicyConfiguration:
    """A configuration view delegates to original versioned policy owners."""
    def __init__(self,model_policy,coordination_budget,global_budget,iacon_budget):
        self.model=model_policy; self.coordination=coordination_budget; self.global_budget=global_budget; self.iacon=iacon_budget

    async def snapshot(self,owner):
        from app.schemas.llm import PolicyDocument
        from app.schemas.coordination import SpendingPolicy
        from app.services.global_budget import GlobalPolicy
        from app.schemas.iacon import MaintenancePolicy
        revision,model=await self.model.snapshot(owner)
        version,coordination=await self.coordination.policy(owner)
        global_state=await self.global_budget.status(owner)
        iacon_version,iacon=await self.iacon.policy(owner)
        return {'policies':[
            {'id':'models','label':'Model routing and native thinking','revision':revision,'value':model.model_dump(),'schema':PolicyDocument.model_json_schema()},
            {'id':'coordination','label':'Rooms, workers and run budgets','revision':version,'value':coordination.model_dump(),'schema':SpendingPolicy.model_json_schema()},
            {'id':'global','label':'Global spending and speech prices','revision':global_state['version'],'value':global_state['policy'],'schema':GlobalPolicy.model_json_schema()},
            {'id':'iacon','label':'Iacon review and maintenance','revision':iacon_version,'value':iacon.model_dump(),'schema':MaintenancePolicy.model_json_schema()},
        ]}

    async def save(self,owner,identity,revision,value):
        from app.schemas.llm import PolicyDocument
        from app.schemas.coordination import SpendingPolicy
        from app.services.global_budget import GlobalPolicy
        from app.schemas.iacon import MaintenancePolicy
        schemas={'models':PolicyDocument,'coordination':SpendingPolicy,'global':GlobalPolicy,'iacon':MaintenancePolicy}
        if identity not in schemas: raise CoordinationError('not_found','Configuration policy is unavailable',404)
        try: policy=schemas[identity].model_validate(value)
        except ValidationError: raise CoordinationError('policy_values','Policy contains invalid fields or values; inspect its schema.',422) from None
        try:
            if identity=='models': await self.model.save(owner,revision,policy)
            elif identity=='coordination': await self.coordination.save(owner,revision,policy)
            elif identity=='global': await self.global_budget.save(owner,revision,policy)
            else: await self.iacon.save(owner,revision,policy)
        except (ValueError,RuntimeError) as exc:
            raise CoordinationError('policy_conflict','Policy save was refused; reload its revision and check model/limit compatibility.',409) from None
        return await self.snapshot(owner)
