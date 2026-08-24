"""Translate a Grafana dashboard plus its query results into a draw model.

Pure functions: no network traffic here. What ``grafana.py`` fetched is
turned into values ``draw.py`` can render, which makes the whole
translation testable without Grafana.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Panel types this renderer can draw. Anything else ends up as a
# placeholder tile on the image — visible, rather than silently missing.
KIND_STAT = "stat"
KIND_TIMESERIES = "timeseries"
KIND_UNSUPPORTED = "unsupported"

_GRAFANA_TYPE_TO_KIND = {
    "stat": KIND_STAT,
    "gauge": KIND_STAT,
    "timeseries": KIND_TIMESERIES,
    "graph": KIND_TIMESERIES,
    "barchart": KIND_TIMESERIES,
}

_RELATIVE_RE = re.compile(r"^now(?:-(\d+)([smhdwMy]))?$")

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
_UNIT_SECONDS = {
    "s": 1, "m": 60, "h": 3600, "d": 86400,
    "w": 604800, "M": 2592000, "y": 31536000,
}


@dataclass(frozen=True)
class GridPos:
    """Position in Grafana's 24-column grid."""
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Series:
    name: str
    times: tuple[float, ...]           # milliseconds since the epoch
    values: tuple[float | None, ...]


@dataclass(frozen=True)
class Panel:
    id: int
    title: str
    kind: str
    unit: str
    decimals: int | None
    grid: GridPos
    series: tuple[Series, ...] = ()
    value: float | None = None         # only set for KIND_STAT
    note: str = ""                     # reason, when kind == unsupported


def parse_relative(expr: str, now: datetime) -> datetime:
    """``now-30d`` → a point in time. Absolute ISO stamps pass through.

    Grafana's range language can do more (``now/d``, ``now-1M/M``); this
    renderer covers the forms that actually occur in dashboards and raises
    on anything else instead of quietly computing the wrong thing.
    """
    m = _RELATIVE_RE.match(expr)
    if m:
        if m.group(1) is None:
            return now
        return now - timedelta(seconds=int(m.group(1)) * _UNIT_SECONDS[m.group(2)])
    try:
        return datetime.fromisoformat(expr.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"time range not understood: {expr!r}") from exc


def time_range_millis(dashboard: dict, now: datetime | None = None
                      ) -> tuple[float, float]:
    """The dashboard's time range as (from, to) in milliseconds."""
    now = now or datetime.now(tz=timezone.utc)
    tr = dashboard.get("time") or {}
    a = parse_relative(str(tr.get("from", "now-6h")), now)
    b = parse_relative(str(tr.get("to", "now")), now)
    return a.timestamp() * 1000, b.timestamp() * 1000


def snap_range(t_from: float, t_to: float) -> tuple[float, float]:
    """Round a time range down onto a stable grid, for drawing only.

    A dashboard range of ``now-30d`` slides continuously, so every data
    point drifts along the time axis — measured at 0.69 px per hour on a
    500 px plot. The picture would then differ on every run, the image hash
    would change, and the panel would burn a full refresh hourly even when
    no number moved.

    Snapping to whole days makes the axis stand still for 24 hours; the
    image changes when the data changes, plus once a day when the window
    rolls over. Short ranges get a finer grid, because a six-hour dashboard
    snapped to days would collapse to nothing.

    The *query* keeps the exact range — this only governs the axis, so what
    Grafana would return is unchanged.
    """
    span = t_to - t_from
    if span >= 2 * DAY_MS:
        unit = DAY_MS
    elif span >= 2 * HOUR_MS:
        unit = HOUR_MS
    else:
        unit = MINUTE_MS
    end = (t_to // unit) * unit
    steps = max(1, round(span / unit))
    return end - steps * unit, end


def _split_frame(frame: dict) -> tuple[tuple[float, ...] | None, list[tuple[str, tuple]]]:
    """Split one frame into its time column and its value columns."""
    fields = frame.get("schema", {}).get("fields", [])
    columns = frame.get("data", {}).get("values", [])
    times: tuple[float, ...] | None = None
    numeric: list[tuple[str, tuple]] = []
    for field, column in zip(fields, columns):
        if field.get("type") == "time" or field.get("name") == "Time":
            times = tuple(column)
        else:
            numeric.append((field.get("name", "?"), tuple(column)))
    return times, numeric


def _last_not_null(column) -> float | None:
    for v in reversed(column):
        if v is not None:
            return float(v)
    return None


def build_panel(panel_json: dict, frames: list[dict]) -> Panel:
    """Translate one Grafana panel plus its frames into a draw model."""
    gp = panel_json.get("gridPos", {})
    grid = GridPos(int(gp.get("x", 0)), int(gp.get("y", 0)),
                   int(gp.get("w", 24)), int(gp.get("h", 8)))
    defaults = panel_json.get("fieldConfig", {}).get("defaults", {})
    unit = defaults.get("unit", "short")
    decimals = defaults.get("decimals")
    title = panel_json.get("title", "")
    pid = int(panel_json.get("id", 0))
    kind = _GRAFANA_TYPE_TO_KIND.get(panel_json.get("type", ""), KIND_UNSUPPORTED)

    if kind == KIND_UNSUPPORTED:
        return Panel(pid, title, kind, unit, decimals, grid,
                     note=f"panel type {panel_json.get('type','?')!r} is not drawn")

    series: list[Series] = []
    for frame in frames:
        times, numeric = _split_frame(frame)
        for name, column in numeric:
            values = tuple(None if v is None else float(v) for v in column)
            series.append(Series(name, times or (), values))

    if kind == KIND_STAT:
        # Grafana reduces via reduceOptions.calcs, in practice always
        # lastNotNull. Other reductions are not guessed at but treated the
        # same — these queries return a single row.
        value = _last_not_null(series[0].values) if series else None
        return Panel(pid, title, kind, unit, decimals, grid, tuple(series), value)

    if not series or not any(s.times for s in series):
        return Panel(pid, title, KIND_UNSUPPORTED, unit, decimals, grid,
                     note="no time series in the response")
    return Panel(pid, title, kind, unit, decimals, grid, tuple(series))


def build_panels(dashboard: dict, results: dict[str, dict]) -> list[Panel]:
    """Translate every panel of the dashboard, in grid order.

    ``results`` is the ``results`` block of a ``/api/ds/query`` response,
    keyed by the refId ``grafana.py`` assigned per panel.
    """
    panels: list[Panel] = []
    for panel_json in dashboard.get("panels", []):
        if panel_json.get("type") == "row":
            continue
        ref = f"P{panel_json.get('id')}"
        result = results.get(ref, {})
        if result.get("status", 200) != 200 or "error" in result:
            gp = panel_json.get("gridPos", {})
            panels.append(Panel(
                int(panel_json.get("id", 0)), panel_json.get("title", ""),
                KIND_UNSUPPORTED, "short", None,
                GridPos(int(gp.get("x", 0)), int(gp.get("y", 0)),
                        int(gp.get("w", 24)), int(gp.get("h", 8))),
                note=str(result.get("error", "query failed"))[:80],
            ))
            continue
        panels.append(build_panel(panel_json, result.get("frames", [])))
    panels.sort(key=lambda p: (p.grid.y, p.grid.x))
    return panels
