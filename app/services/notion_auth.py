"""App-scoped Notion credentials, including public OAuth token rotation."""
import time
import httpx
from app.config import settings
from app.services.oauth_tokens import CachedOAuthToken


class NotionToken(CachedOAuthToken):
    def clear(self):
        self._access = None

    def credential_material(self):
        from app.core.runtime_state import get_notion_credentials
        return (get_notion_credentials(), settings.notion_client_id, settings.notion_client_secret)

    async def _get_unlocked(self):
        from app.core.runtime_state import get_notion_credentials, set_notion_credentials
        data = get_notion_credentials()
        if data.get('access_token') and (not data.get('expires_at') or time.time() < data['expires_at'] - 60):
            return data['access_token']
        if not data.get('refresh_token'):
            return None
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                response = await client.post('https://api.notion.com/v1/oauth/token',
                    auth=(settings.notion_client_id, settings.notion_client_secret),
                    json={'grant_type': 'refresh_token', 'refresh_token': data['refresh_token']})
                response.raise_for_status()
                token = response.json()
            if not token.get('access_token'):
                return None
            updated = {**data, **token}
            updated['expires_at'] = time.time() + int(token['expires_in']) if token.get('expires_in') else None
            set_notion_credentials(updated)
            return token['access_token']
        except Exception:
            return None
