"""Check an isolated candidate over real HTTP, including durable memory and restart.

Read-only by default. --exercise-candidate creates one synthetic memory record
and restarts the candidate; never use that option against the live service.
Credentials are read from SPEDA_API_KEY and never printed or put in arguments.
"""
import argparse
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def run(base, exercise=False):
    key=os.environ['SPEDA_API_KEY']
    def request(path, *, body=None, authenticated=True, expected=200):
        headers={'X-API-Key':key} if authenticated else {}
        if body is not None: headers['Content-Type']='application/json'
        req=Request(base.rstrip('/')+path,headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(req,timeout=8) as response:
                raw=response.read()
                assert response.status==expected,(path,response.status)
                return json.loads(raw) if 'json' in response.headers.get('Content-Type','') else raw
        except HTTPError as error:
            if error.code!=expected: raise RuntimeError(f'{path}: HTTP {error.code}') from None
            return None
    ready=request('/health/ready',authenticated=False)
    assert ready['status']=='ready'
    request('/config',authenticated=False,expected=401)
    configuration=request('/config')
    sessions=request('/sessions?agent_id=speda&limit=1')
    if sessions:
        sid=sessions[0]['id']
        request(f'/sessions/{sid}/messages')
        request(f'/sessions/{sid}/export')
    for path in ('/models','/model-policy','/coordination/personas','/coordination/global-budget',
                 '/iacon/records?limit=1','/iacon/maintenance','/solus','/rooms-ui'):
        request(path)
    result={'http_ready':True,'unauthenticated_owner_access_rejected':True,
        'history_reload_export':bool(sessions),'models_configuration_memory_and_static_ui':True,
        'synthetic_memory_write':False,'restart_verified':False}
    if exercise:
        token=uuid4().hex
        text='Synthetic release verification '+token
        source=request('/iacon/sources',body={'key':'release-'+token,'text':text})
        command={'command_id':'release-'+token,'kind':'preference','title':'Synthetic release verification',
            'payload':{'text':text},'evidence':[{'source_id':source['source_id'],'start':0,'end':len(text),'quote':text}]}
        record=request('/iacon/records',body=command)
        duplicate=request('/iacon/records',body=command)
        assert record['id']==duplicate['id'] and record['version']==duplicate['version']
        result['synthetic_memory_write']=True
        before=ready['instance_id']
        restart=request('/config/restart',body={'expected_revision':configuration['revision'],
            'request_id':'release-'+token},expected=202)
        assert restart['accepted']
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                current=request('/health/ready',authenticated=False)
                if current['instance_id']!=before:
                    saved=request('/iacon/records/'+record['id']+'?history=true')
                    assert saved['id']==record['id'] and saved['version']==1
                    assert request('/iacon/records',body=command)['id']==record['id']
                    result['restart_verified']=True
                    break
            except (URLError,TimeoutError,RuntimeError,ConnectionError):
                pass
            time.sleep(.5)
        if not result['restart_verified']: raise RuntimeError('Candidate did not restart with durable record and receipt')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--exercise-candidate',action='store_true')
    args=parser.parse_args()
    print(json.dumps(run(args.base_url,args.exercise_candidate)))
