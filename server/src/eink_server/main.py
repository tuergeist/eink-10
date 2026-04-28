"""FastAPI app: bearer-auth-protected dashboard image cache, channel-aware.

A *channel* is a named slot for one stored PNG (e.g. ``inkplate10``,
``inkplate6``, ``kueche``). Each board has its own channel; the renderer
addresses one channel per push. This lets a single service feed multiple
displays without touching the schema between them.

Endpoints all live under ``/c/{channel}/`` plus a small set of
unauthenticated globals:

  * ``GET  /healthz``                  no auth, liveness probe
  * ``GET  /renderer-spec.md``         no auth, returns the contract doc

  * ``POST   /c/{channel}/image``      push token; ``?dither=`` optional
  * ``DELETE /c/{channel}/image``      push token; clears stored bytes
  * ``GET    /c/{channel}/config.json``read token; what the board polls
  * ``GET    /c/{channel}/dashboard.png`` read token; the bytes themselves

State lives on disk under ``DATA_DIR/<channel>/``. Channel names are
validated; arbitrary user-supplied strings can't escape into the FS.
"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Path as PathParam, Query, Request, Response
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

# Known panels — used to surface dimensions in /config.json so renderers
# can adapt without out-of-band knowledge. Unknown channels work fine,
# they just don't get a `panel` block in their config.
PANEL_SPECS: dict[str, dict[str, int]] = {
    "inkplate10": {"width": 1200, "height": 825, "gray_levels": 8},
    "ink6":       {"width": 800,  "height": 600, "gray_levels": 8},
}

# Channel name format: lowercase letters, digits, dash, underscore.
# Must start with alphanumeric. 1–32 chars. Excludes anything that could
# escape the data dir or confuse downstream tooling.
CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


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

# Per-channel Storage instances are created lazily and cached.
_storage_cache: dict[str, Storage] = {}


def _storage_for(channel: str) -> Storage:
    if channel not in _storage_cache:
        _storage_cache[channel] = Storage(DATA_DIR / channel)
    return _storage_cache[channel]


def _validated_channel(channel: str) -> str:
    if not CHANNEL_RE.match(channel):
        # Use 404 rather than 400 so we don't help an attacker
        # distinguish "exists but invalid name" vs "no such channel".
        raise HTTPException(status_code=404, detail="channel not found")
    return channel


app = FastAPI(title="Inkplate Dashboard Cache")


# --- auth helpers -------------------------------------------------------------
def _check_token(authorization: Optional[str], expected: str) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    presented = authorization[len("Bearer "):]
    if not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=403, detail="invalid token")


# --- public endpoints ---------------------------------------------------------
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


# --- per-channel endpoints ----------------------------------------------------
@app.post("/c/{channel}/image")
async def push_image(
    request: Request,
    channel: str = PathParam(...),
    authorization: Optional[str] = Header(None),
    dither: str = Query("none", pattern="^(none|floyd-steinberg)$"),
) -> JSONResponse:
    _check_token(authorization, PUSH_TOKEN)
    channel = _validated_channel(channel)

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
        meta = _storage_for(channel).store(png_bytes, dither=dither)
    except Exception as e:  # bad PNG, IO, etc.
        raise HTTPException(status_code=400, detail=f"store failed: {e}") from e

    payload = meta.as_dict()
    payload["channel"] = channel
    return JSONResponse(payload, status_code=200)


@app.delete("/c/{channel}/image", status_code=204)
def delete_image(
    channel: str = PathParam(...),
    authorization: Optional[str] = Header(None),
) -> Response:
    _check_token(authorization, PUSH_TOKEN)
    channel = _validated_channel(channel)
    _storage_for(channel).delete()
    return Response(status_code=204)


@app.get("/c/{channel}/config.json")
def config(
    channel: str = PathParam(...),
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    _check_token(authorization, READ_TOKEN)
    channel = _validated_channel(channel)
    meta = _storage_for(channel).load_meta()
    body: dict[str, object] = {
        "channel": channel,
        "image_url": f"{PUBLIC_BASE_URL}/c/{channel}/dashboard.png",
        "last_modified": meta.last_modified if meta else "",
        "refresh_interval_seconds": REFRESH_INTERVAL_S,
        "config_url_override": CONFIG_URL_OVERRIDE,
        "overlay_clock": OVERLAY_CLOCK,
    }
    if channel in PANEL_SPECS:
        body["panel"] = PANEL_SPECS[channel]
    return JSONResponse(body)


@app.get("/c/{channel}/dashboard.png")
def dashboard(
    channel: str = PathParam(...),
    authorization: Optional[str] = Header(None),
) -> Response:
    _check_token(authorization, READ_TOKEN)
    channel = _validated_channel(channel)
    storage = _storage_for(channel)
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
