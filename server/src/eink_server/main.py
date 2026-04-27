"""FastAPI app: bearer-auth-protected dashboard image cache.

Two roles:
  * Pusher (PUSH_TOKEN): POST /image with PNG bytes, optionally with
    ?dither=floyd-steinberg to quantize onto the Inkplate's 8 gray levels.
  * Reader (READ_TOKEN): GET /config.json and GET /dashboard.png — used by
    the Inkplate firmware.

State lives on disk (DATA_DIR), surviving pod restarts. TLS is expected to
be terminated by a reverse proxy / Ingress in front of us.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from .quantize import floyd_steinberg
from .storage import Storage

# --- config from env ----------------------------------------------------------
PUSH_TOKEN = os.environ.get("EINK_PUSH_TOKEN", "")
READ_TOKEN = os.environ.get("EINK_READ_TOKEN", "")
DATA_DIR = Path(os.environ.get("EINK_DATA_DIR", "/data"))
PUBLIC_BASE_URL = os.environ.get(
    "EINK_PUBLIC_BASE_URL", "http://172.16.2.158:8989"
).rstrip("/")
REFRESH_INTERVAL_S = int(os.environ.get("EINK_REFRESH_INTERVAL_S", "300"))
CONFIG_URL_OVERRIDE: Optional[str] = (
    os.environ.get("EINK_CONFIG_URL_OVERRIDE") or None
)
MAX_UPLOAD_BYTES = int(os.environ.get("EINK_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
OVERLAY_CLOCK = os.environ.get("EINK_OVERLAY_CLOCK", "false").strip().lower() in (
    "1", "true", "yes", "on",
)


def _resolve_spec_path() -> Optional[Path]:
    """Find the renderer spec on disk. Container puts it at /app/...; for
    local dev we fall back to docs/renderer-spec.md relative to this file."""
    env = os.environ.get("EINK_RENDERER_SPEC_PATH")
    candidates = [Path(env)] if env else []
    candidates.append(Path("/app/renderer-spec.md"))
    here = Path(__file__).resolve().parent
    candidates.append(here.parent.parent.parent / "docs" / "renderer-spec.md")
    for p in candidates:
        if p.is_file():
            return p
    return None


SPEC_PATH = _resolve_spec_path()

if not PUSH_TOKEN or not READ_TOKEN:
    raise RuntimeError(
        "EINK_PUSH_TOKEN and EINK_READ_TOKEN must be set (use long random strings)"
    )

storage = Storage(DATA_DIR)
app = FastAPI(title="Inkplate Dashboard Cache")


# --- auth helpers -------------------------------------------------------------
def _check_token(authorization: Optional[str], expected: str) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    presented = authorization[len("Bearer "):]
    if not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=403, detail="invalid token")


# --- endpoints ----------------------------------------------------------------
@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/renderer-spec.md")
def renderer_spec() -> Response:
    """Public, unauthenticated copy of the renderer contract — so an LLM
    agent that only knows the service URL can fetch its operating manual."""
    if SPEC_PATH is None:
        raise HTTPException(status_code=404, detail="renderer spec not bundled")
    return Response(
        content=SPEC_PATH.read_bytes(),
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/image")
async def push_image(
    request: Request,
    authorization: Optional[str] = Header(None),
    dither: str = Query("none", pattern="^(none|floyd-steinberg)$"),
) -> JSONResponse:
    _check_token(authorization, PUSH_TOKEN)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty body")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    if content_type and content_type != "image/png":
        raise HTTPException(
            status_code=415, detail=f"expected image/png, got {content_type}"
        )

    png_bytes = floyd_steinberg(body) if dither == "floyd-steinberg" else body

    try:
        meta = storage.store(png_bytes, dither=dither)
    except Exception as e:  # bad PNG, IO, etc.
        raise HTTPException(status_code=400, detail=f"store failed: {e}") from e

    return JSONResponse(meta.as_dict(), status_code=200)


@app.delete("/image", status_code=204)
def delete_image(authorization: Optional[str] = Header(None)) -> Response:
    _check_token(authorization, PUSH_TOKEN)
    storage.delete()
    return Response(status_code=204)


@app.get("/config.json")
def config(authorization: Optional[str] = Header(None)) -> JSONResponse:
    _check_token(authorization, READ_TOKEN)
    meta = storage.load_meta()
    return JSONResponse(
        {
            "image_url": f"{PUBLIC_BASE_URL}/dashboard.png",
            "last_modified": meta.last_modified if meta else "",
            "refresh_interval_seconds": REFRESH_INTERVAL_S,
            "config_url_override": CONFIG_URL_OVERRIDE,
            "overlay_clock": OVERLAY_CLOCK,
        }
    )


@app.get("/dashboard.png")
def dashboard(authorization: Optional[str] = Header(None)) -> Response:
    _check_token(authorization, READ_TOKEN)
    data = storage.load_image()
    if data is None:
        raise HTTPException(status_code=404, detail="no image pushed yet")
    meta = storage.load_meta()
    headers = {"Cache-Control": "no-store"}
    if meta is not None:
        headers["ETag"] = meta.last_modified
    return Response(content=data, media_type="image/png", headers=headers)


def run() -> None:
    import uvicorn

    uvicorn.run(
        "eink_server.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("EINK_PORT", "8989")),
        reload=False,
    )
