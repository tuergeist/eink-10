"""From Grafana's grid to pixel rectangles — pure geometry.

Grafana lays panels out in a grid of 24 columns and any number of rows
(``gridPos``). This renderer adopts that split verbatim and scales it onto
the panel area, so a panel moved in the dashboard also moves on the paper
without anything being touched here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .panels import Panel

GRAFANA_COLUMNS = 24


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def inset(self, dx: int, dy: int | None = None) -> "Rect":
        dy = dx if dy is None else dy
        return Rect(self.x + dx, self.y + dy, self.w - 2 * dx, self.h - 2 * dy)


def place(panels: list[Panel], box: Rect, gap: int = 14) -> dict[int, Rect]:
    """Assign each panel its area inside ``box``.

    ``gap`` is the total distance between two neighbours; every tile gives
    up half of it on all four sides, so the outer margin matches the space
    between two tiles.
    """
    if not panels:
        return {}
    rows = max(p.grid.y + p.grid.h for p in panels)
    if rows <= 0:
        return {}
    unit_w = box.w / GRAFANA_COLUMNS
    unit_h = box.h / rows
    half = gap / 2

    out: dict[int, Rect] = {}
    for p in panels:
        x0 = box.x + p.grid.x * unit_w + half
        y0 = box.y + p.grid.y * unit_h + half
        x1 = box.x + (p.grid.x + p.grid.w) * unit_w - half
        y1 = box.y + (p.grid.y + p.grid.h) * unit_h - half
        out[p.id] = Rect(round(x0), round(y0), round(x1 - x0), round(y1 - y0))
    return out
