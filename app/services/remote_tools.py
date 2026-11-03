"""Bounded configured-sidecar requests; effect uncertainty is never a retry hint."""
import httpx
from app.schemas.llm import ProviderError


async def request_bytes(method, url, *, maximum=131072, timeout=30, **kwargs):
    try:
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=False,trust_env=False) as client:
            async with client.stream(method,url,**kwargs) as response:
                if (response.status_code>=500 or 300<=response.status_code<400) and method not in {'GET','HEAD'}:
                    raise ProviderError('remote_effect_unknown','Remote service did not confirm its outcome; inspect the saved receipt before another action.')
                if response.status_code>=300:
                    return response.status_code,b''
                body=bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body)+len(chunk)>maximum:
                        raise ProviderError('remote_response_bound','Remote response exceeded the allowed size; a dispatched effect may require reconciliation.')
                    body.extend(chunk)
                return response.status_code,bytes(body)
    except (httpx.HTTPError,OSError) as exc:
        raise ProviderError('remote_effect_unknown' if method not in {'GET','HEAD'} else 'remote_unavailable',
            'Remote transport ended without a confirmed outcome. No automatic retry was made.') from exc
