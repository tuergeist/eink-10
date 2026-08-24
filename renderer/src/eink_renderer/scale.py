"""Axis scaling — pure functions."""

from __future__ import annotations

import math


def nice_step(rough: float) -> float:
    """Next larger "round" step size (1, 2 or 5 times a power of ten)."""
    if rough <= 0:
        return 1.0
    exp = math.floor(math.log10(rough))
    base = rough / (10 ** exp)
    for candidate in (1, 2, 5, 10):
        if base <= candidate:
            return candidate * (10 ** exp)
    return 10 ** (exp + 1)


def nice_ticks(lo: float, hi: float, target: int = 4) -> tuple[float, float, list[float]]:
    """Axis bounds and tick marks for a value range.

    Returns ``(bottom, top, ticks)``. The bounds sit on multiples of the
    step, so the topmost line carries a label instead of ending somewhere
    in the middle of nowhere.
    """
    if hi < lo:
        lo, hi = hi, lo
    if math.isclose(lo, hi):
        # Constant series: open up a range around the value, otherwise the
        # height would be zero and the curve would lie on the axis.
        span = abs(lo) if lo else 1.0
        lo, hi = lo - span * 0.5, hi + span * 0.5
    step = nice_step((hi - lo) / max(target, 1))
    bottom = math.floor(lo / step) * step
    top = math.ceil(hi / step) * step
    ticks = []
    # Integer loop rather than repeated addition: otherwise floating point
    # error accumulates and the last tick misses the top edge.
    for i in range(int(round((top - bottom) / step)) + 1):
        ticks.append(bottom + i * step)
    return bottom, top, ticks


def project(value: float, lo: float, hi: float, px_lo: float, px_hi: float) -> float:
    """Map a data value onto a pixel coordinate."""
    if math.isclose(hi, lo):
        return (px_lo + px_hi) / 2
    return px_lo + (value - lo) / (hi - lo) * (px_hi - px_lo)
