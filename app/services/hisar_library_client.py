"""Scoped HTTP boundary to Hisar's authoritative Hall of Records catalog."""
import httpx


class HisarLibraryClient:
    def __init__(self, hisar_url: str, sync_token: str, reader_token: str,
                 *, max_bytes: int = 25 * 1024**2, transport=None):
        self.hisar_url = hisar_url.rstrip("/")
        self.sync_token = sync_token
        self.reader_token = reader_token
        self.max_bytes = max_bytes
        self.http = httpx.AsyncClient(base_url=self.hisar_url, timeout=30, transport=transport,
                                     follow_redirects=False)

    @property
    def configured(self) -> bool:
        return bool(self.sync_token and self.reader_token)

    async def aclose(self):
        await self.http.aclose()

    async def _json(self, method, path, *, sync=False, **kwargs):
        key = "X-Library-Sync-Token" if sync else "X-Library-Reader-Token"
        token = self.sync_token if sync else self.reader_token
        if not token:
            raise ValueError("Hall of Records credentials are not configured")
        response = await self.http.request(method, path, headers={key: token}, **kwargs)
        response.raise_for_status()
        return response.json()

    async def get_events(self, after_sequence: int = 0) -> dict:
        return await self._json("GET", "/library/events", sync=True, params={"after": after_sequence})

    async def get_manifest(self, offset=0, limit=100) -> dict:
        return await self._json("GET", "/library/manifest", sync=True,
                                params={"offset": offset, "limit": limit})

    async def get_document(self, document_id: str) -> dict:
        return await self._json("GET", f"/library/catalog/{document_id}", sync=True)

    async def resolve_access(self, document_ids: list[str], agent_id: str) -> dict:
        return await self._json("POST", "/library/resolve",
                                json={"document_ids": document_ids, "agent_id": agent_id})

    async def report_index_receipt(self, document_id: str, revision_id: str, status: str):
        return await self._json("POST", "/library/index-receipts", sync=True,
                                data={"document_id": document_id, "revision_id": revision_id,
                                      "sync_status": status})

    async def download_revision_blob(self, revision_id: str) -> bytes:
        # Sync credentials are restricted to ingestion and never passed to execution workers.
        if not self.sync_token:
            raise ValueError("Hall of Records sync credentials are not configured")
        async with self.http.stream("GET", f"/library/originals/{revision_id}",
                                    headers={"X-Library-Sync-Token": self.sync_token}) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self.max_bytes:
                    raise ValueError("Original exceeds the configured size limit")
                chunks.append(chunk)
            return b"".join(chunks)


def client_from_settings(settings):
    return HisarLibraryClient(settings.hisar_base_url, settings.hisar_library_sync_token,
                              settings.hisar_library_reader_token, max_bytes=settings.library_max_bytes)