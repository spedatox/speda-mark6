"""Authenticated image loading for chat and Canvas."""

from fastapi import APIRouter, HTTPException, Query, Response

from app.services.media import ImageUnavailable, MAX_URL_LENGTH, fetch_image

router = APIRouter(tags=["media"])


@router.get("/media/proxy")
async def image_proxy(url: str = Query(min_length=1, max_length=MAX_URL_LENGTH)) -> Response:
    try:
        data, media_type = await fetch_image(url)
    except ImageUnavailable:
        raise HTTPException(status_code=404, detail="Image unavailable") from None
    return Response(data, media_type=media_type, headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })
