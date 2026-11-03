# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configured no-secrets sandbox adapter. Never falls back to the API host."""
import asyncio
import json
from pathlib import Path,PurePosixPath
from uuid import uuid4
from app.config import settings
from app.skills.base import Skill
from app.schemas.llm import ProviderError
from app.services.remote_tools import request_bytes


class RunCommandSkill(Skill):
    name='run_command'
    read_only=False
    memoize=False
    requires_network=True
    deferred=True
    search_keywords='shell script sandbox code python node execute compute terminal'
    description=(
        'Run a bounded command in the configured no-secrets sandbox workspace. '
        'Use it for actual computations, scripts and generated artifacts; project engineering uses native Forge assignments. '
        'It never executes on the API host or receives application credentials. A stopped HTTP transport does not prove the remote process stopped. '
        'Returns bounded stdout/stderr, exit status and timeout information. Unknown executions require inspection rather than automatic replay.')
    input_schema={'type':'object','properties':{'command':{'type':'string','minLength':1,'maxLength':16000},
        'timeout':{'type':'integer','minimum':1,'maximum':120,'default':30}},'required':['command'],'additionalProperties':False}

    async def execute(self,args,context):
        if context.task_role=='worker': return '{"status":"rejected","detail":"Read-only workers cannot execute commands."}'
        if not settings.sandbox_url: return '{"status":"unconfigured"}'
        if context.coordination: await context.coordination.ensure_active(context)
        if context.db is not None:
            from sqlalchemy import select,or_
            from sqlalchemy.ext.asyncio import AsyncSession
            from app.models.model_policy import ToolExecutionReceipt
            async with AsyncSession(bind=context.db.bind) as reader:
                held=await reader.scalar(select(ToolExecutionReceipt.call_id).where(
                    ToolExecutionReceipt.user_id==context.user_id,ToolExecutionReceipt.name==self.name,
                    ToolExecutionReceipt.status.in_(['started','unknown']),
                    or_(ToolExecutionReceipt.request_id!=context.request_id,ToolExecutionReceipt.call_id!=context.tool_call_id)).limit(1))
            if held: return '{"status":"reconciling","detail":"A sandbox command is still active or has an unknown outcome. Inspect it before another execution."}'
        seconds=args.get('timeout',30)
        if context.effective_policy and context.effective_policy.deadline:
            import time
            seconds=min(seconds,int(context.effective_policy.deadline-time.monotonic()))
        if seconds<1: return '{"status":"deadline","detail":"No execution time remains."}'
        status,body=await request_bytes('POST',settings.sandbox_url.rstrip('/')+'/exec',
            json={'command':args['command'],'timeout':seconds},timeout=seconds+5,maximum=220000,
            headers={'Idempotency-Key':f'{context.user_id}:{context.request_id}:{context.tool_call_id}'})
        if status>=300: return json.dumps({'status':'rejected','http_status':status})
        try: data=json.loads(body)
        except ValueError as exc: raise ProviderError('remote_effect_unknown','Sandbox did not return an execution receipt.') from exc
        if not isinstance(data,dict) or type(data.get('exit_code')) is not int:
            raise ProviderError('remote_effect_unknown','Sandbox outcome was not verifiable.')
        if data.get('timed_out') and not data.get('process_tree_stopped'):
            raise ProviderError('remote_effect_unknown','Sandbox timed out without confirmed process cleanup; do not replay the command.')
        return json.dumps({'status':'finished','exit_code':data['exit_code'],'timed_out':bool(data.get('timed_out')),
            'stdout':str(data.get('stdout',''))[:12000],'stderr':str(data.get('stderr',''))[:4000],
            'output_partial':len(str(data.get('stdout','')))>12000 or len(str(data.get('stderr','')))>4000},ensure_ascii=False)


class DeliverFileSkill(Skill):
    name='deliver_file'
    read_only=False
    memoize=False
    requires_network=True
    deferred=True
    search_keywords='sandbox deliver file artifact download output attachment'
    description=(
        'Copy an existing sandbox workspace artifact into a native downloadable file card. '
        'Use it after a sandbox command has produced the requested file, not for arbitrary API-host paths. '
        'Only a relative workspace path is accepted and an existing output is never overwritten. '
        'Returns the registered file metadata; the chat transport displays the download card.')
    input_schema={'type':'object','properties':{'path':{'type':'string','minLength':1,'maxLength':1000},
        'title':{'type':'string','maxLength':200}},'required':['path'],'additionalProperties':False}

    async def execute(self,args,context):
        if context.task_role=='worker': return '{"status":"rejected"}'
        if not settings.sandbox_url: return '{"status":"unconfigured"}'
        raw=args['path']
        path=PurePosixPath(raw)
        if path.is_absolute() or '..' in path.parts or '\\' in raw or ':' in raw or not path.name:
            return '{"status":"rejected","detail":"Use a relative path inside the sandbox workspace."}'
        status,body=await request_bytes('GET',settings.sandbox_url.rstrip('/')+'/file',
            params={'path':str(path)},maximum=10*1024**2)
        if status>=300: return json.dumps({'status':'unavailable','http_status':status})
        name=''.join(char if char.isalnum() or char in ' ._-' else '_' for char in path.name)[:180].strip(' .') or 'artifact'
        destination=Path(settings.temp_outputs_dir)/f'{uuid4().hex}_{name}'
        def save():
            destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open('xb') as stream: stream.write(body)
        await asyncio.to_thread(save)
        from app.core.files import register_file
        return json.dumps({'status':'delivered','file':register_file(context,str(destination),args.get('title') or path.stem)})
