# eink-10 Renderer Specification

A **renderer** is anything that produces a dashboard PNG and pushes it
to this service. The Inkplate 10 board polls the service and displays
whatever is currently stored. This document is the full contract between
renderers and the service. If you are an LLM building a renderer, you
should not need any other file in this repo.

> Service base URL: provided by the operator (e.g.
> `https://eink.ein-service.de`).
> Push token: provided by the operator out of band. Treat as password.

---

## TL;DR

- Render a **1200×825 grayscale PNG**.
- `POST /image` with header `Authorization: Bearer <PUSH_TOKEN>` and body =
  raw PNG bytes (`Content-Type: image/png`).
- Optionally append `?dither=floyd-steinberg` to have the server quantize
  onto the panel's 8 grays. Otherwise send already-quantized pixels
  yourself.
- Push **only when content actually changes** — pushes with identical
  pixels are no-ops, but pushes with trivially different pixels (e.g. a
  live timestamp) cost the panel a real refresh each time.

---

## Image format

| Property | Required value |
|---|---|
| Width × height | **1200 × 825 px** exactly (smaller renders at top-left, larger gets clipped) |
| Color mode | 8-bit grayscale (PIL `"L"`, PNG color type 0) — RGB is accepted but converted |
| Bit depth | 8 bits per channel |
| Interlacing | **non-interlaced** (Adam7 PNG is not supported) |
| File size | ≤ 8 MB per push |
| Animation | not supported (only first frame is read) |

The Inkplate 10 panel is **3-bit grayscale, 8 levels**. The exact gray
values are evenly spaced across 0–255:

```
0, 36, 73, 109, 146, 182, 219, 255
```

For best output (sharp text, no color rounding artifacts), the pixels
landing on the panel must be one of those eight values. You have two
options:

1. **Server-side quantization** — append `?dither=floyd-steinberg` to
   the POST URL. The server applies Floyd–Steinberg dithering with a
   palette pinned to the 8 levels. Your PNG can be anything 8-bit
   grayscale.
2. **Client-side quantization** — quantize yourself, then push without
   `?dither` (default behavior is passthrough). Useful if you want a
   different dithering algorithm or need exact pixel control.

---

## API

### `GET /healthz`

No auth. Returns `{"status":"ok"}`. Use for liveness checks.

### `POST /image`

Replace the current dashboard. Atomic — readers always see either the
old or the new image, never a partial one.

**Headers**

```
Authorization: Bearer <PUSH_TOKEN>
Content-Type: image/png
```

**Query params**

| Param | Values | Default | Effect |
|---|---|---|---|
| `dither` | `none` \| `floyd-steinberg` | `none` | If `floyd-steinberg`, server quantizes the PNG onto the panel's 8 grays before storing |

**Body**

Raw PNG bytes. **Not** multipart, **not** base64.

**Response 200**

```json
{
  "last_modified": "467689173f81497e",
  "size": 5316,
  "width": 1200,
  "height": 825,
  "content_type": "image/png",
  "dither": "floyd-steinberg",
  "pushed_at": "2026-04-27T12:36:51.967069Z"
}
```

`last_modified` is a 16-hex-char content hash (sha256 prefix). The
Inkplate compares this against its own stored copy and refreshes the
display only when it differs.

**Error responses**

| Status | Cause |
|---|---|
| `400` | empty body or invalid PNG |
| `401` | missing or malformed `Authorization` header |
| `403` | wrong token |
| `413` | body > 8 MB |
| `415` | `Content-Type` is set and isn't `image/png` |

### `DELETE /image`

Same auth as POST. Clears the stored image; subsequent `GET /dashboard.png`
will 404 and the board will skip its refresh on next wake. Useful for
testing — does **not** trigger a panel refresh by itself.

### Endpoints you should not call from a renderer

- `GET /config.json` — read by the Inkplate, requires the read token.
- `GET /dashboard.png` — same.

If you find yourself wanting either, you're probably writing the wrong
side of this contract.

---

## Push semantics & cadence

- The service stores **one** current image. Each successful POST
  replaces it.
- The board polls `/config.json` every `refresh_interval_seconds`
  (default 300, set by the operator). The display only refreshes when
  the image's content hash changed since the previous wake.
- Pushing **identical bytes** produces an identical hash → no display
  refresh. Free.
- Pushing **bytes that differ in even one pixel** produces a new hash →
  the next poll will trigger a full panel refresh (~2 s flash, costs a
  small fraction of the panel's ~1 M-cycle lifetime).
- Therefore: do not embed live timestamps, animated counters, or
  anything that changes every render unless those changes are actually
  worth showing on the display.

If you want a clock on the dashboard, ask the operator to enable
`EINK_OVERLAY_CLOCK=true`. The board itself will then NTP-sync and
overlay `YYYY-MM-DD HH:MM` on each new image. Your PNG should have no
clock of its own.

---

## Examples

### Python (Pillow + requests)

```python
import io
import os

import requests
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 825

img = Image.new("L", (WIDTH, HEIGHT), 255)
draw = ImageDraw.Draw(img)
font = ImageFont.truetype("DejaVuSans.ttf", 72)
draw.text((60, 60), "Hello, e-ink", fill=0, font=font)

buf = io.BytesIO()
img.save(buf, format="PNG", optimize=True)

resp = requests.post(
    f"{os.environ['EINK_BASE_URL']}/image",
    params={"dither": "floyd-steinberg"},
    data=buf.getvalue(),
    headers={
        "Authorization": f"Bearer {os.environ['EINK_PUSH_TOKEN']}",
        "Content-Type": "image/png",
    },
    timeout=30,
)
resp.raise_for_status()
print(resp.json())
```

### curl

```bash
curl -X POST "$EINK_BASE_URL/image?dither=floyd-steinberg" \
  -H "Authorization: Bearer $EINK_PUSH_TOKEN" \
  -H "Content-Type: image/png" \
  --data-binary @dashboard.png
```

### Pre-quantized client-side (skip server dithering)

```python
from PIL import Image

LEVELS = tuple(round(255 * i / 7) for i in range(8))

palette = []
for level in LEVELS:
    palette.extend([level, level, level])
palette.extend([0, 0, 0] * (256 - len(LEVELS)))
palette_img = Image.new("P", (1, 1))
palette_img.putpalette(palette)

# `img` is your 8-bit grayscale render
quantized = img.convert("RGB").quantize(
    palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG
).convert("L")

# Push without ?dither — server stores quantized bytes verbatim
```

---

## Don'ts

- **Don't** write loops that push every second. The panel is e-ink, not
  an LCD. The display can't physically render that fast and you'll wear
  it out.
- **Don't** include color, alpha, or 16-bit depth — they get downconverted
  unpredictably.
- **Don't** rely on `GET /dashboard.png` succeeding before your first
  push — it returns 404 until something is stored.
- **Don't** hardcode dimensions you assume from another e-paper. The
  Inkplate 10 is 1200×825. Other Inkplate models have different sizes.

---

## Security model

- **Push token** authenticates POST/DELETE. Anyone with it can
  overwrite the dashboard. Rotate via redeploy if leaked.
- **Read token** is separate, lives on the Inkplate firmware, only
  authorizes GET. It cannot push.
- TLS is terminated by the cluster's reverse proxy. The pod itself
  speaks plain HTTP internally.
- The board accepts the server's TLS cert without verification today
  (`setInsecure()` in firmware), so the bearer token is the primary
  authentication, not certificate pinning. Future hardening: pin the
  operator's Let's Encrypt root.

---

## Operator-controlled knobs (for context)

These live in the server's environment, not in the API. Renderers can't
set them directly:

| Env var | Purpose |
|---|---|
| `EINK_PUSH_TOKEN` | required; secret you authenticate with |
| `EINK_READ_TOKEN` | required; baked into firmware |
| `EINK_PUBLIC_BASE_URL` | base URL the board uses for image_url |
| `EINK_REFRESH_INTERVAL_S` | how often the board polls (default 300) |
| `EINK_OVERLAY_CLOCK` | board overlays `HH:MM` on each new image (default false) |
| `EINK_DATA_DIR` | where the PNG + meta is persisted (default `/data`) |
| `EINK_MAX_UPLOAD_BYTES` | upper bound for POST body (default 8 MiB) |

If you need one of these changed, talk to the operator.
