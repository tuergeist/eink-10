"""Font access.

Both DejaVu faces live inside the package, not in the system. Reason: the
image should be pixel-identical when rendered locally and inside the
container. A system font would be a different one on macOS than in
``python:slim``, and layout mistakes would only show up on the panel.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
REGULAR = FONT_DIR / "DejaVuSans.ttf"
BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"


@lru_cache(maxsize=256)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = BOLD if bold else REGULAR
    if not path.is_file():
        raise FileNotFoundError(f"font missing: {path}")
    return ImageFont.truetype(str(path), size)


def text_size(text: str, f: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """Width and height of a text box, without ascender/descender offset."""
    left, top, right, bottom = f.getbbox(text)
    return right - left, bottom - top


def fit_font(text: str, max_w: int, max_h: int, bold: bool = False,
             largest: int = 120, smallest: int = 8) -> ImageFont.FreeTypeFont:
    """Largest size at which ``text`` still fits into ``max_w × max_h``."""
    lo, hi = smallest, largest
    best = smallest
    while lo <= hi:
        mid = (lo + hi) // 2
        w, h = text_size(text, font(mid, bold))
        if w <= max_w and h <= max_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return font(best, bold)
