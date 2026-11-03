import json
from uuid import uuid4
import httpx
import pytest
from app.main import create_app
from app.skills.legion import LegionInspectSkill,LegionStatusSkill
from tests.test_worker_control import environment,worker


async def test_original_legion_routes_require_auth_preserve_cursors_and_owner_scope(memory):
    bits,control=await environment(memory)
    parent,child,ticket=await worker(memory,bits,control)
    _,other,_=await worker(memory,bits,control,owner=2)
    await control.publish(child,{'phase':'text','text':'Private scoped findings'})
    await control.finish(child,'succeeded','Confirmed worker findings')
    app=create_app()
    app.state.worker_control=control
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
        assert (await client.get('/legion/executions')).status_code==401
        assert (await client.get('/legion/executions/'+child.step_id)).status_code==401
        client.headers['X-API-Key']='test-only-key'
        rows=(await client.get('/legion/executions',params={'session_id':parent.session_id})).json()
        assert len(rows)==1 and rows[0]['execution_id']==child.step_id
        assert (await client.get('/legion/executions/'+other.step_id)).status_code==404
        events=(await client.get(f'/legion/executions/{child.step_id}/events')).json()
        assert events['execution']['status']=='succeeded'
        assert (await client.get(f'/legion/executions/{child.step_id}/events',params={'after':events['last_sequence']})).json()['events']==[]
        assert (await client.get(f'/legion/executions/{child.step_id}/events',params={'after':-1})).status_code==422
        response=await client.get(f"/legion/attach/{ticket['ticket']}")
        frames=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert response.status_code==200 and frames[-1]['type']=='subagent'
        assert frames[-1]['data']['execution_id']==child.step_id and frames[-1]['data']['report']=='Confirmed worker findings'
        assert (await client.post(f'/legion/executions/{child.step_id}/interrupt')).json()['accepted'] is False
        body={'text':'Do not silently reopen finished work','message_id':uuid4().hex}
        assert (await client.post(f'/legion/executions/{child.step_id}/messages',json=body)).status_code==409
        assert (await client.post(f'/legion/executions/{child.step_id}/messages',json={**body,'origin':'agent'})).status_code==422
    await bits.coordinator.shutdown()


async def test_legacy_ticket_status_and_inspection_cursor_use_native_authority(memory):
    bits,control=await environment(memory)
    bits.coordinator.workers=control
    parent,child,admission=await worker(memory,bits,control)
    await control.finish(child,'succeeded','Results preserved under the original execution')
    status=json.loads(await LegionStatusSkill().execute({'id':admission['ticket']},parent))
    assert status['execution_id']==child.step_id and status['status']=='succeeded'
    result=json.loads(await LegionInspectSkill().execute({'execution_id':child.step_id,'after':0},parent))
    assert result['events'][-1]['phase']=='finished' and result['execution']['status']=='succeeded'
    assert result['next_cursor']==result['last_sequence']
    assert not bits.client.calls
    await bits.coordinator.shutdown()
