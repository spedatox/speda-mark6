import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.memory_file import MemoryFile

logger = logging.getLogger(__name__)
router = APIRouter(tags=["memory"])


@router.get("/memory/files")
async def list_memory_files(db: AsyncSession = Depends(get_db)):
    """
    SPEDA's knowledge bank — the /memories virtual filesystem, read-only.
    Backs the DATA_BANKS // KNOWLEDGE panel in the systems board so the owner
    can see exactly what SPEDA has extracted and remembers about them.
    """
    result = await db.execute(
        select(MemoryFile)
        .where(MemoryFile.user_id == 1)
        .order_by(MemoryFile.path)
    )
    files = result.scalars().all()
    return [
        {
            "path": f.path,
            "content": f.content or "",
            "updated_at": f.updated_at.isoformat() if f.updated_at else None,
        }
        for f in files
    ]
