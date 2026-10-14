# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Credential validation errors must never echo submitted secrets."""
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


def ui_response():
    from pathlib import Path
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    path = Path(__file__).resolve().parents[2] / "static" / "solus" / "index.html"
    if not path.is_file():
        raise HTTPException(503, "Build the Solus UI: cd packages/solus; npm ci; npm run build")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


def asset_response(asset_path):
    from pathlib import Path
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    root = (Path(__file__).resolve().parents[2] / "static" / "solus" / "assets").resolve()
    path = (root / asset_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Asset not found.")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


class SolusRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def sanitized(request):
            length = request.headers.get('Content-Length', '0')
            if not length.isdecimal() or int(length) > 2_000_000:
                return JSONResponse(status_code=413, content={"detail":"Solus request is too large."})
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = [{k: v for k, v in error.items() if k in {"loc", "msg", "type"}} for error in exc.errors()]
                return JSONResponse(status_code=422, content={"detail": errors}, headers={"Cache-Control":"no-store"})
        return sanitized
