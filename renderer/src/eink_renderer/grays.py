"""The eight gray levels of the Inkplate panel, and snapping onto them.

The panel does 3-bit gray: eight evenly spaced levels. Anything in
between is rounded either by the server (Floyd-Steinberg) or by the panel
itself, and on text both produce a speckle of stray dots.

So this renderer snaps every pixel to the *nearest* level, without
dithering. Fills and lines are already drawn in level values; only the
soft edges of glyphs are affected, and those read cleaner snapped than
dithered.
"""

from __future__ import annotations

# Exactly the values from docs/renderer-spec.md.
LEVELS: tuple[int, ...] = tuple(round(255 * i / 7) for i in range(8))

# Named levels, so the drawing code carries no bare numbers.
INK = LEVELS[0]        # 0   — text, axes, first series
DARK = LEVELS[2]       # 73  — second series, secondary text
MID = LEVELS[3]        # 109 — third series
BORDER = LEVELS[4]     # 146 — tile outlines
GRID = LEVELS[5]       # 182 — gridlines
FILL = LEVELS[6]       # 219 — area fill under a single curve
PAPER = LEVELS[7]      # 255 — background


def nearest_level(value: int) -> int:
    """Nearest panel gray level for an 8-bit gray value."""
    if value < 0 or value > 255:
        raise ValueError(f"gray value outside 0..255: {value}")
    return LEVELS[round(value * 7 / 255)]


def level_lut() -> list[int]:
    """256-entry table for ``PIL.Image.point`` — one multiply per pixel
    instead of a Python loop over 990 000 pixels."""
    return [nearest_level(v) for v in range(256)]
