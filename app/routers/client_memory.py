"""Knowledge-board compatibility; every canonical write goes through Iacon."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from app.services.coordination_budget import CoordinationError
from app.services.iacon_catalogue import Conflict

router = APIRouter(prefix="/memory", tags=["memory"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Commit(Strict):
    path: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=1000000)
    expected_updated_at: str | None = Field(default=None, max_length=80)


class Rename(Strict):
    old_path: str = Field(min_length=1, max_length=256)
    new_path: str = Field(min_length=1, max_length=256)


class Restore(Strict):
    revision_id: int = Field(gt=0)


async def checked(operation):
    try:
        return await operation
    except KeyError as exc:
        raise HTTPException(404, "Memory record not found") from exc
    except CoordinationError as exc:
        raise HTTPException(exc.status, exc.args[0]) from exc
    except (Conflict, IntegrityError) as exc:
        raise HTTPException(409, "Memory changed; reload before saving") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/files")
async def files(request: Request):
    return await checked(request.app.state.client_memory.files(1))


@router.put("/files")
async def commit(body: Commit, request: Request):
    return await checked(request.app.state.client_memory.commit(1, body.path, body.content, body.expected_updated_at))


@router.delete("/files")
async def retire(path: str, request: Request):
    return await checked(request.app.state.client_memory.retire(1, path))


@router.post("/files/rename")
async def rename(body: Rename, request: Request):
    return await checked(request.app.state.client_memory.rename(1, body.old_path, body.new_path))


@router.get("/files/revisions")
async def revisions(path: str, request: Request):
    return await checked(request.app.state.client_memory.revisions(1, path))


@router.post("/files/restore")
async def restore(body: Restore, request: Request):
    return await checked(request.app.state.client_memory.restore(1, body.revision_id))


@router.get("/folders")
async def folders():
    return [{"path": f"/memories/iacon/{kind}/", "summary": summary,
             "owner_agent": "orion", "open": True} for kind, summary in (
        ("entity", "People, places, organizations, projects and courses"),
        ("claim", "Evidence-backed facts with validity dates"),
        ("preference", "Owner preferences"), ("episode", "Dated events"),
        ("state", "Living states and their next review"),
        ("document", "Original documents and retained imports"))]


@router.get("/sources")
async def sources(request: Request):
    return {"files": [], "authority": "iacon", "assignable": False,
        "agents": [{"agent_id": p["agent_id"], "name": p["name"], "domain": p["domain"],
                    "source": None, "default": None} for p in request.app.state.profiles.roster()]}


class SourceAssignment(Strict):
    agent_id: str
    path: str | None = None


@router.put("/sources")
async def source_assignment(body: SourceAssignment):
    raise HTTPException(409, "Iacon owns the shared catalogue; per-agent canonical file writers were replaced")
