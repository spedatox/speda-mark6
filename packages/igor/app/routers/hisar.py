# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Authenticated transport for the app-owned Hisar workspace picker."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.workspaces import WorkspaceError

router = APIRouter(tags=["hisar"])


class CreateDirectoryRequest(BaseModel):
    path: str
    name: str


@router.get("/hisar/dirs")
async def hisar_dirs(request: Request, path: str = "/"):
    try:
        return await request.app.state.hisar_workspaces.directories(path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/hisar/dirs", status_code=201)
async def create_hisar_dir(request: Request, body: CreateDirectoryRequest):
    try:
        return await request.app.state.hisar_workspaces.create(body.path, body.name)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
