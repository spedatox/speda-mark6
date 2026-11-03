"""Live provider inventory for model pickers; declarations still own capabilities."""
import asyncio
import logging
import re
from time import monotonic

from app.services.model_policy import parse_model_ref


def model_order(row):
    """Keep families together, newer versions first, aliases before dated builds."""
    _, model = parse_model_ref(row['id'])
    model = model.casefold()
    snapshot = re.search(r'-(\d{4}-\d{2}-\d{2}|\d{8}|\d{4})$', model)
    date = int(snapshot[1].replace('-', '')) if snapshot else 0
    base = model[:snapshot.start()] if snapshot else model
    match = re.match(r'^(.*?)(\d+(?:[.-]\d+)*)(.*)$', base)
    family, version, variant = (match.groups() if match else (base, '', ''))
    numbers = [int(n) for n in re.split(r'[.-]', version)] if version else []
    version_key = tuple(-n for n in (numbers + [0] * 8)[:8])
    variant_key = tuple((1, int(part)) if part.isdigit() else (0, part)
                        for part in re.split(r'(\d+)', variant))
    return (not bool(version), family, version_key, variant_key,
            bool(snapshot), -date, model)


class ModelCatalog:
    def __init__(self, client):
        self.client = client
        self._cache = {}
        self._locks = {}
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def chat_model(model, provider):
        if provider == 'openai' and (model.startswith(('babbage-', 'davinci-')) or '-instruct' in model):
            return False
        return not any(marker in model.lower() for marker in (
            'embed', 'rerank', 'retriev', 'moderation', 'imagen', 'veo',
            'image-generation', 'image-preview', '-image', 'dall-e', 'tts',
            'audio', 'whisper', 'realtime', '-live-', 'transcrib', 'translat',
            'sora-',
        ))

    async def _fetch(self, provider):
        config = self.client.settings
        if provider == 'anthropic':
            rows = []
            async for model in self.client.anthropic_client().client.models.list(limit=100):
                rows.append({'id': model.id, 'name': model.display_name or model.id})
            return rows

        headers, params = {}, {}
        if provider == 'vertex':
            url = 'https://aiplatform.googleapis.com/v1beta1/publishers/google/models'
            headers['Authorization'] = f'Bearer {await self.client.vertex_token()}'
            collection = 'publisherModels'
            params['pageSize'] = 200
        else:
            url = getattr(config, f'{provider}_base_url').rstrip('/') + '/models'
            collection = 'models' if provider == 'gemini' else 'data'
            key = getattr(config, f'{provider}_api_key', '')
            if provider == 'kimi':
                key = key or config.moonshot_api_key
            if key:
                headers['x-goog-api-key' if provider == 'gemini' else 'Authorization'] = (
                    key if provider == 'gemini' else f'Bearer {key}')

        rows, seen_pages = [], set()
        for _ in range(50):
            response = await self.client.http.get(url, headers=headers, params=params,
                timeout=1.5 if provider == 'ollama' else 8.0)
            response.raise_for_status()
            body = response.json()
            for model in body[collection]:
                methods = model.get('supportedGenerationMethods')
                if methods is not None and 'generateContent' not in methods:
                    continue
                mid = model.get('id') or model.get('name', '')
                if provider in {'gemini', 'vertex'}:
                    mid = mid.rsplit('/', 1)[-1]
                if mid:
                    rows.append({'id': mid, 'name': model.get('displayName') or mid})
            page = body.get('nextPageToken')
            if page:
                params['pageToken'] = page
            elif body.get('has_more') and body.get('last_id'):
                page = body['last_id']
                params['after'] = page
            else:
                return rows
            if page in seen_pages:
                raise ValueError('Repeated model catalog page')
            seen_pages.add(page)
        raise ValueError('Model catalog page limit exceeded')

    async def _provider(self, provider):
        async with self._locks.setdefault(provider, asyncio.Lock()):
            cached = self._cache.get(provider)
            if cached and cached[0] > monotonic():
                return cached[1]
            try:
                async with asyncio.timeout(8 if provider != 'ollama' else 1.5):
                    rows = await self._fetch(provider)
                rows = [dict(row, id=(row['id'] if provider == 'anthropic' else f"{provider}:{row['id']}"))
                        for row in rows if self.chat_model(row['id'], provider)]
                if not rows:
                    raise ValueError('Empty chat model catalog')
            except Exception as exc:
                # Do not log exception messages: provider URLs may contain credentials.
                if provider != 'ollama':
                    self.logger.warning('provider_model_catalog_unavailable',
                        extra={'provider': provider, 'error_type': type(exc).__name__})
                rows = None
            self._cache[provider] = (monotonic() + 60, rows)
            return rows

    async def available(self, policy, document):
        providers = [p for p in ('anthropic', 'openai', 'gemini', 'vertex', 'zai',
            'deepseek', 'nvidia', 'kimi', 'ollama') if self.client.configured(f'{p}:catalog')]
        inventories = await asyncio.gather(*(self._provider(p) for p in providers))
        declared = policy.specs(document)
        result = {}
        for provider, rows in zip(providers, inventories):
            if rows is None:
                rows = [{'id': ref, 'name': parse_model_ref(ref)[1]} for ref in declared
                        if parse_model_ref(ref)[0] == provider and provider != 'ollama']
            for row in sorted(rows, key=model_order):
                result[row['id']] = row
        return list(result.values())
