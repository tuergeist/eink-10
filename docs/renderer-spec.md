# eink Renderer Specification

A **renderer** is anything that produces a dashboard PNG and pushes it
to this service. One or more Inkplate boards poll the service for their
own slot — called a **channel** — and display whatever is currently
stored for that channel. This document is the full contract between
renderers and the service. If you are an LLM building a renderer, you
should not need any other file in this repo.

> Service base URL: provided by the operator (e.g.
> `https://eink.ein-service.de`).
> Push token: provided by the operator out of band. Treat as password.
>
> **This file is also served live at `<base-url>/renderer-spec.md`** — no
> auth required. If you're an LLM agent and you only have the base URL,
> fetch that path to get the latest contract.

---

## TL;DR

- Pick (or learn from the operator) the **channel** you target — e.g.
  `inkplate10`, `ink6`.
- `GET /c/<channel>/config.json` (with the read token) tells you your
  panel's exact dimensions in the `panel` block.
- Render a grayscale PNG at exactly those dimensions.
- `POST /c/<channel>/image` with header `Authorization: Bearer <PUSH_TOKEN>`
  and body = raw PNG bytes (`Content-Type: image/png`).
- Optionally append `?dither=floyd-steinberg` to have the server quantize
  onto the panel's 8 grays. Otherwise send already-quantized pixels
  yourself.
- Push **only when content actually changes** — pushes with identical
  pixels are no-ops, but pushes with trivially different pixels (e.g. a
  live timestamp) cost the panel a real refresh each time.

---

## Channels

A channel is a named slot, holding exactly one current image. Each
Inkplate board is configured with one channel and only fetches from that
channel; you can run many boards off one service without them stepping
on each other.

Channel names match `^[a-z0-9][a-z0-9_-]{0,31}$`: lowercase letters,
digits, dash, underscore. Anything else returns 404.

### Known panels

The service knows the dimensions of these channels and surfaces them in
`config.json`:

| Channel | Panel | Dimensions | Gray levels |
|---|---|---|---|
| `inkplate10` | Inkplate 10 (9.7") | 1200 × 825 | 8 |
| `ink6`       | Inkplate 6 (6")    | 800 × 600  | 8 |

Other channel names work too (the service stores whatever you push), they
just don't get a `panel` block in `config.json` — the renderer is
expected to know the target dimensions out of band.

---

## Image format

| Property | Required value |
|---|---|
| Width × height | **exactly the panel's dimensions** (see channel's `panel.width` / `panel.height`) — smaller renders at top-left, larger gets clipped |
| Color mode | 8-bit grayscale (PIL `"L"`, PNG color type 0) — RGB is accepted but converted |
| Bit depth | 8 bits per channel |
| Interlacing | **non-interlaced** (Adam7 PNG is not supported) |
| File size | ≤ 8 MB per push |
| Animation | not supported (only first frame is read) |

The Inkplate 6 / 10 panels are **3-bit grayscale, 8 levels**. The exact
gray values are evenly spaced across 0–255:

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

### `GET /renderer-spec.md`

No auth. Returns this document. Use it to bootstrap LLM-driven
renderers from just the base URL.

### `POST /c/{channel}/image`

Replace the current dashboard for one channel. Atomic — readers always
see either the old or the new image, never a partial one.

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
  "channel": "inkplate6",
  "last_modified": "467689173f81497e",
  "size": 5316,
  "width": 800,
  "height": 600,
  "content_type": "image/png",
  "dither": "floyd-steinberg",
  "pushed_at": "2026-04-28T13:00:37.881306Z"
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
| `404` | invalid channel name (regex violation) |
| `413` | body > 8 MB |
| `415` | `Content-Type` is set and isn't `image/png` |

### `DELETE /c/{channel}/image`

Same auth as POST. Clears the stored image for one channel; subsequent
`GET .../dashboard.png` returns 404 and the affected board skips its
refresh on the next wake. Useful for testing — does **not** trigger a
panel refresh by itself.

### Endpoints you should not call from a renderer

These exist for the Inkplate firmware and require the read token:

- `GET /c/{channel}/config.json`
- `GET /c/{channel}/dashboard.png`

You can hit `config.json` once at renderer startup to discover panel
dimensions; that's fine. Don't poll it.

---

## Push semantics & cadence

- Each channel stores **one** current image. Each successful POST
  replaces it for that channel only.
- Each board polls its own `config.json` every `refresh_interval_seconds`
  (default 300, set by the operator). The display only refreshes when
  that channel's content hash changed since the previous wake.
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

BASE = os.environ["EINK_BASE_URL"]            # e.g. https://eink.ein-service.de
TOKEN = os.environ["EINK_PUSH_TOKEN"]
CHANNEL = os.environ.get("EINK_CHANNEL", "inkplate10")

# Discover dimensions for our channel.
# Note: this requires the read token — the renderer typically already
# knows the target panel and can hardcode WIDTH/HEIGHT instead.
WIDTH, HEIGHT = 1200, 825   # inkplate10
# WIDTH, HEIGHT = 800, 600  # ink6

img = Image.new("L", (WIDTH, HEIGHT), 255)
draw = ImageDraw.Draw(img)
font = ImageFont.truetype("DejaVuSans.ttf", 72)
draw.text((60, 60), "Hello, e-ink", fill=0, font=font)

buf = io.BytesIO()
img.save(buf, format="PNG", optimize=True)

resp = requests.post(
    f"{BASE}/c/{CHANNEL}/image",
    params={"dither": "floyd-steinberg"},
    data=buf.getvalue(),
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "image/png",
    },
    timeout=30,
)
resp.raise_for_status()
print(resp.json())
```

### curl

```bash
curl -X POST "$EINK_BASE_URL/c/$CHANNEL/image?dither=floyd-steinberg" \
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

# `img` is your 8-bit grayscale render at the channel's dimensions
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
- **Don't** push to channels you weren't told you own. Channels are
  global per service and will overwrite whatever's there.
- **Don't** rely on `GET .../dashboard.png` succeeding before your first
  push — it returns 404 until something is stored for that channel.

---

## Security model

- **Push token** authenticates POST/DELETE on every channel. Anyone with
  it can overwrite any channel. Rotate via redeploy if leaked.
- **Read token** is separate, lives on the Inkplate firmware, only
  authorizes GET. It cannot push.
- TLS is terminated by the cluster's reverse proxy. The pod itself
  speaks plain HTTP internally.
- The boards accept the server's TLS cert without verification today
  (`setInsecure()` in firmware), so the bearer token is the primary
  authentication, not certificate pinning. Future hardening: pin the
  operator's Let's Encrypt root.

---

## Operator-controlled knobs (for context)

These live in the server's environment, not in the API. Renderers can't
set them directly:

| Env var | Purpose |
|---|---|
| `EINK_PUSH_TOKEN` | required; secret renderers authenticate with |
| `EINK_READ_TOKEN` | required; baked into firmware |
| `EINK_PUBLIC_BASE_URL` | base URL the boards build their image_url from |
| `EINK_REFRESH_INTERVAL_S` | how often the boards poll (default 300) |
| `EINK_OVERLAY_CLOCK` | boards overlay `HH:MM` on each new image (default false) |
| `EINK_DATA_DIR` | where channel storage lives (default `/data`) |
| `EINK_MAX_UPLOAD_BYTES` | upper bound for POST body (default 8 MiB) |

If you need one of these changed, talk to the operator.
