import httpx
from typing import Dict, Any, List, Optional
import os

class HisarLibraryClient:
    def __init__(self, hisar_url: str, sync_token: str, reader_token: str):
        self.hisar_url = hisar_url.rstrip("/")
        self.sync_token = sync_token
        self.reader_token = reader_token

    async def get_events(self, after_sequence: int = 0) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.hisar_url}/library/events",
                params={"after": after_sequence},
                headers={"X-Library-Sync-Token": self.sync_token}
            )
            resp.raise_for_status()
            return resp.json()

    async def resolve_access(self, document_ids: List[str], agent_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.hisar_url}/library/resolve",
                json={"document_ids": document_ids, "agent_id": agent_id},
                headers={"X-Library-Reader-Token": self.reader_token}
            )
            resp.raise_for_status()
            return resp.json()

    async def report_index_receipt(self, document_id: str, revision_id: str, status: str):
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.hisar_url}/library/index-receipts",
                data={"document_id": document_id, "revision_id": revision_id, "sync_status": status},
                headers={"X-Library-Sync-Token": self.sync_token}
            )
            resp.raise_for_status()

    async def download_revision_blob(self, revision_id: str) -> bytes:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.hisar_url}/library/revisions/{revision_id}/download",
                headers={"X-Library-Reader-Token": self.reader_token}
            )
            resp.raise_for_status()
            return resp.content

# Provide a singleton instance or factory using settings
def get_library_client() -> HisarLibraryClient:
    # Assuming config defines these or use env directly
    url = os.getenv("HISAR_INTERNAL_URL", "http://localhost:8600")
    sync_token = os.getenv("HISAR_LIBRARY_SYNC_TOKEN", "")
    reader_token = os.getenv("HISAR_LIBRARY_READER_TOKEN", "")
    return HisarLibraryClient(url, sync_token, reader_token)
