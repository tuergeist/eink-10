"""Send the finished PNG to the eink service.

Without ``?dither``: the pixels are already snapped onto the eight panel
gray levels (see ``grays``), and the server should store them unchanged. A
second dithering pass would roughen the crisp glyph edges again.
"""

from __future__ import annotations

import requests

TIMEOUT = 30


class PushError(RuntimeError):
    pass


def push_png(base_url: str, token: str, channel: str, data: bytes) -> dict:
    url = f"{base_url.rstrip('/')}/c/{channel}/image"
    r = requests.post(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "image/png"},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise PushError(f"push failed: HTTP {r.status_code} {r.text[:200]}")
    return r.json()
