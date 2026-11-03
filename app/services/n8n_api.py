"""Bounded n8n control plane. Mutations have durable callers and no hidden retries."""
import json
import re
import asyncio
from urllib.parse import quote,urlsplit
from app.schemas.llm import ProviderError
from app.services.remote_tools import request_bytes


class N8nClient:
    def __init__(self,config): self.config=config
    @property
    def configured(self):
        parsed=urlsplit(self.config.n8n_api_url)
        return bool(parsed.scheme in {'http','https'} and parsed.hostname and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment and self.config.n8n_api_key)

    async def request(self,method,path,*,payload=None,key=None,params=None):
        if not self.configured: return 503,None
        headers={'X-N8N-API-KEY':self.config.n8n_api_key,'Accept':'application/json'}
        if key: headers['Idempotency-Key']=key
        kwargs={'headers':headers,'maximum':1024*1024,'timeout':15}
        if payload is not None: kwargs['json']=payload
        if params: kwargs['params']=params
        status,body=await request_bytes(method,self.config.n8n_api_url.rstrip('/')+'/api/v1'+path,**kwargs)
        if status>=300: return status,None
        try: data=json.loads(body) if body else {}
        except ValueError as exc: raise ProviderError('n8n_outcome_unknown','n8n did not return a verifiable response.') from exc
        return status,data

    async def get(self,identity):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',str(identity)):
            raise ProviderError('n8n_identity','Workflow identity is invalid.')
        status,data=await self.request('GET','/workflows/'+quote(str(identity),safe=''))
        if status==404: return None
        if status>=300: raise ProviderError('n8n_unavailable','n8n workflow state could not be verified.')
        if not isinstance(data,dict): raise ProviderError('n8n_protocol','n8n workflow state was not a structured record.')
        return data

    async def named(self,name):
        matches=[]
        cursor=None
        for _ in range(5):
            params={'name':name,'limit':100}
            if cursor: params['cursor']=cursor
            status,data=await self.request('GET','/workflows',params=params)
            if status>=300 or not isinstance(data,dict): raise ProviderError('n8n_unavailable','n8n workflow listing is unavailable.')
            if not isinstance(data.get('data'),list): raise ProviderError('n8n_protocol','n8n returned an invalid workflow listing.')
            matches.extend(item for item in data['data'] if isinstance(item,dict) and item.get('name')==name)
            cursor=data.get('nextCursor')
            if not cursor: return matches
        raise ProviderError('n8n_partial_listing','Workflow listing was incomplete; no absence or retry was inferred.')

    async def ping(self):
        if not self.configured: return False
        try:
            async with asyncio.timeout(2):
                status,data=await self.request('GET','/workflows',params={'limit':1})
                return status<300 and isinstance(data,dict) and isinstance(data.get('data'),list)
        except (ProviderError,TimeoutError): return False
