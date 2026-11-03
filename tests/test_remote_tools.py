import json
from dataclasses import replace
import httpx
import pytest
from sqlalchemy import select
from app.config import settings
from app.schemas.llm import Block,ProviderError
from app.skills.onyx import OnyxSkill
from app.skills.sandbox import RunCommandSkill,DeliverFileSkill
from app.services import remote_tools
from app.models.model_policy import ToolExecutionReceipt
from tests.test_worker_control import environment,parent_context


def transport(monkeypatch, handler):
    original=httpx.AsyncClient
    monkeypatch.setattr(remote_tools.httpx,'AsyncClient',lambda **kwargs:original(
        transport=httpx.MockTransport(handler),**kwargs))


async def test_onyx_preserves_wire_contract_and_bounds_and_redacts_reads(memory,monkeypatch):
    monkeypatch.setattr(settings,'onyx_api_url','https://onyx.fixture')
    monkeypatch.setattr(settings,'onyx_api_token','fixture-secret-value')
    seen=[]
    def handler(request):
        seen.append(request)
        if request.method=='GET':
            return httpx.Response(200,json={'tickets':[{'number':i,'title':'fixture-secret-value'} for i in range(40)]})
        return httpx.Response(200,json={'success':True})
    transport(monkeypatch,handler)
    context=memory.context()
    result=json.loads(await OnyxSkill().execute({'action':'list'},context))
    assert len(result['data']['tickets'])==30 and result['data']['partial']
    assert 'fixture-secret-value' not in json.dumps(result)
    result=json.loads(await OnyxSkill().execute({'action':'complete','ticket_number':7,'completion_summary':'Owner deliverable completed.'},context))
    assert result['status']=='acknowledged'
    assert json.loads(seen[-1].content)['action']=='complete_ticket'
    assert seen[-1].url.path=='/api/v1/speda'
    before=len(seen)
    refused=json.loads(await OnyxSkill().execute({'action':'delete','ticket_number':7},context))
    assert refused['status']=='rejected' and len(seen)==before


async def test_uncertain_remote_effect_has_a_native_receipt_and_is_never_resent(memory,monkeypatch):
    monkeypatch.setattr(settings,'onyx_api_url','https://onyx.fixture')
    monkeypatch.setattr(settings,'onyx_api_token','fixture-token')
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(503,text='unconfirmed remote mutation')
    transport(monkeypatch,handler)
    bits,_=await environment(memory)
    ctx=await parent_context(memory,bits)
    ctx.extra['loaded_tools']={'onyx'}
    await bits.engine._registry.register_skill(OnyxSkill())
    call=Block(type='tool_use',id='one-write',name='onyx',input={'action':'add_comment','ticket_number':7,'content':'Requested progress note'})
    with pytest.raises(ProviderError,match='confirm'): await bits.engine._registry.execute_recorded(call,ctx)
    with pytest.raises(ProviderError,match='unknown outcome'): await bits.engine._registry.execute_recorded(call,ctx)
    assert len(calls)==1
    async with memory.sessions() as db:
        receipt=await db.scalar(select(ToolExecutionReceipt).where(ToolExecutionReceipt.call_id=='one-write'))
        assert receipt.status=='started'
    await bits.coordinator.shutdown()


async def test_sandbox_command_and_file_delivery_are_bounded_and_never_overwrite(memory,monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'sandbox_url','http://sandbox.fixture')
    monkeypatch.setattr(settings,'temp_outputs_dir',str(tmp_path/'outputs'))
    calls=[]
    def handler(request):
        calls.append(request)
        if request.method=='GET': return httpx.Response(200,content=b'generated file')
        return httpx.Response(200,json={'stdout':'a'*15000,'stderr':'','exit_code':0,'timed_out':False})
    transport(monkeypatch,handler)
    ctx=memory.context()
    result=json.loads(await RunCommandSkill().execute({'command':'python report.py','timeout':1},ctx))
    assert result['exit_code']==0 and result['output_partial'] and len(result['stdout'])==12000
    first=json.loads(await DeliverFileSkill().execute({'path':'report.csv'},ctx))
    second=json.loads(await DeliverFileSkill().execute({'path':'report.csv'},ctx))
    assert first['file']['name']!=second['file']['name']
    assert len(list((tmp_path/'outputs').glob('*.csv')))==2
    before=len(calls)
    assert json.loads(await DeliverFileSkill().execute({'path':'../outside'},ctx))['status']=='rejected'
    assert json.loads(await RunCommandSkill().execute({'command':'do not send'},replace(ctx,task_role='worker')))['status']=='rejected'
    assert len(calls)==before


async def test_sandbox_timeout_keeps_unknown_command_held_across_new_calls(memory,monkeypatch):
    monkeypatch.setattr(settings,'sandbox_url','http://sandbox.fixture')
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'stdout':'partial','stderr':'timed out','exit_code':124,'timed_out':True})
    transport(monkeypatch,handler)
    bits,_=await environment(memory)
    ctx=await parent_context(memory,bits)
    ctx.extra['loaded_tools']={'run_command'}
    await bits.engine._registry.register_skill(RunCommandSkill())
    first=Block(type='tool_use',id='command-one',name='run_command',input={'command':'long command'})
    with pytest.raises(ProviderError,match='cleanup'): await bits.engine._registry.execute_recorded(first,ctx)
    second=Block(type='tool_use',id='command-two',name='run_command',input={'command':'do not start this'})
    result=json.loads(await bits.engine._registry.execute_recorded(second,ctx))
    assert result['status']=='reconciling' and len(calls)==1
    await bits.coordinator.shutdown()
