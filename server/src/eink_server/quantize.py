"""Optional Floyd–Steinberg quantization onto the Inkplate 10 panel's 8 grays.

Used when the pusher passes ``?dither=floyd-steinberg`` on POST /image.
Default is passthrough (the pusher takes responsibility for quantization).
"""

from __future__ import annotations

import io

from PIL import Image

WIDTH = 1200
HEIGHT = 825

# 3-bit panel: 8 evenly distributed gray levels across 0..255.
GRAY_LEVELS: tuple[int, ...] = tuple(round(255 * i / 7) for i in range(8))


def _palette_image() -> Image.Image:
    palette: list[int] = []
    for level in GRAY_LEVELS:
        palette.extend([level, level, level])
    palette.extend([0, 0, 0] * (256 - len(GRAY_LEVELS)))
    p = Image.new("P", (1, 1))
    p.putpalette(palette)
    return p


_PALETTE = _palette_image()


def floyd_steinberg(png_bytes: bytes) -> bytes:
    """Apply Floyd–Steinberg dithering against the panel's 8 gray levels.

    Returns a re-encoded grayscale PNG whose pixels are exactly one of
    GRAY_LEVELS. Caller is expected to send these bytes to the board with
    on-device ``dither=false``.
    """
    src = Image.open(io.BytesIO(png_bytes))
    rgb = src.convert("RGB")
    quantized = rgb.quantize(palette=_PALETTE, dither=Image.Dither.FLOYDSTEINBERG)
    out = quantized.convert("L")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
