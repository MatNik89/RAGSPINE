"""Scoped static asset serving — atlas/web/static/ ONLY. No auth: fonts/CSS
must load for the (unauthenticated) login page too. Path-scoped via
realpath+commonpath so no traversal escapes the static dir; extension
allowlist so only known asset types are ever served."""
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from atlas.core import security

STATIC_DIR = (Path(__file__).parent / "static").resolve()

_MEDIA_TYPES = {
    ".woff2": "font/woff2",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/vnd.microsoft.icon",
}

_NOT_FOUND = Response(status_code=404)

router = APIRouter()


@router.get("/static/{path:path}")
def serve_static(path: str):
    media_type = _MEDIA_TYPES.get(os.path.splitext(path)[1].lower())
    if media_type is None:
        return _NOT_FOUND
    target = (STATIC_DIR / path).resolve()
    if not security.path_under(str(target), str(STATIC_DIR)):
        return _NOT_FOUND
    if not target.is_file():
        return _NOT_FOUND
    return FileResponse(
        target,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
