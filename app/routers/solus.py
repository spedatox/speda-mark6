# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Solus transport only. The compiled UI contains no owner data or credentials."""
from fastapi import APIRouter, Request
from app.schemas.solus import Login, PasswordChange, PasswordSetup
from app.services.solus.transport import SolusRoute, ui_response, asset_response

router = APIRouter(tags=["solus"], route_class=SolusRoute)


@router.get("/solus/api/v1/auth-status")
async def auth_status(request: Request):
    from fastapi.responses import JSONResponse
    return JSONResponse({"configured": await request.app.state.solus_http.security.configured()}, headers={"Cache-Control":"no-store"})


@router.post("/solus/api/v1/login")
async def password_login(request: Request, payload: Login):
    return await request.app.state.solus_http.login(request, payload.password)


@router.post("/solus/api/v1/setup")
async def password_setup(request: Request, payload: PasswordSetup):
    return await request.app.state.solus_http.setup(request, payload.new_password, payload.token)


@router.post("/solus/api/v1/password")
async def password_change(request: Request, payload: PasswordChange):
    return await request.app.state.solus_http.security.change(request, payload.password, payload.new_password)


@router.post("/solus/api/v1/books/{book_id}/import-preview")
async def import_preview(request: Request, book_id: str, payload: dict):
    return await request.app.state.solus_http.preview(request, book_id, payload)


@router.post("/solus/api/v1/books/{book_id}/reconciliation-preview")
async def reconciliation_preview(request: Request, book_id: str, payload: dict):
    return await request.app.state.solus_http.reconciliation_preview(request, book_id, payload)


@router.get("/solus")
@router.get("/solus/")
async def ui():
    return ui_response()


@router.api_route("/solus/assets/{asset_path:path}", methods=["GET", "HEAD"])
async def asset(asset_path: str):
    return asset_response(asset_path)


@router.post("/solus/api/v1/session")
async def login(request: Request):
    return await request.app.state.solus_http.session(request, create=True)


@router.get("/solus/api/v1/session")
async def session(request: Request):
    return await request.app.state.solus_http.session(request)


@router.delete("/solus/api/v1/session")
async def logout(request: Request):
    return await request.app.state.solus_http.session(request, delete=True)


@router.post("/solus/api/v1/commands")
async def command(request: Request, payload: dict):
    return await request.app.state.solus_http.command(request, payload)


@router.get("/solus/api/v1/receipts/{key}")
async def receipt(request: Request, key: str):
    return await request.app.state.solus_http.receipt(request, key)


@router.post("/solus/api/v1/receipts/{key}/resolve")
async def resolve_receipt(request: Request, key: str):
    return await request.app.state.solus_http.resolve_receipt(request, key)


@router.get("/solus/api/v1/books")
async def books(request: Request):
    return await request.app.state.solus_http.read(request, "books")


@router.get("/solus/api/v1/legacy")
async def legacy(request: Request):
    return await request.app.state.solus_http.read(request, "legacy")


@router.get("/solus/api/v1/books/{book_id}/{resource}")
async def report(request: Request, book_id: str, resource: str):
    return await request.app.state.solus_http.read(request, resource, book_id)
