"""Today's calendar from an ICS feed, as a day grid for the panel.

Time axis on the left, one row per 30 minutes, one column per person
(Christoph, Anne), events as blocks at their time (overlapping ones side by
side within a column). All-day events sit in a strip above the grid. Optional events (created by Reclaim.ai, or marked free with
TRANSP:TRANSPARENT) get a dashed outline and a lighter background.

Runs as its own CronJob in the morning, midday and evening windows and pushes
into the same channel as the Grafana renderer. The channel holds one image; the
board shows whichever was pushed last.

Same invariant as the dashboard: identical events give identical bytes.
Nothing in the image depends on the time of the run, only on the date and
the events — no "now" marker, no struck-through past entries.

The ICS URL is a secret link (anyone with it can read the calendar). It is
never printed, and request errors are reported without it.
"""

from __future__ import annotations

import argparse
import io
import math
import os
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from icalendar import Calendar
from PIL import Image, ImageDraw

from .draw import BATTERY_ZONE, CLOCK_ZONE, HEADER_HEIGHT, dashed_line, snap_to_levels
from .fonts import font, text_size
from .grays import DARK, FILL, GRID, INK, PAPER
from .push import push_png
from . import weather as weather_mod
from .weather import Weather
from . import sun as sun_mod

TZ = ZoneInfo("Europe/Berlin")
FETCH_TIMEOUT = 30

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

# Who owns an event. The addresses and calendar names are personal data and
# this repository is public, so they come from the environment (comma-separated,
# compared case-insensitively), set in the deployment:
#   AGENDA_ANNE_CALENDARS   X-MEINKALENDER-CALENDAR values that are Anne's;
#                           everything else, including events without the
#                           property, is Christoph's
#   AGENDA_ANNE_EMAILS      ORGANIZER addresses that make an event Anne's, even
#                           when it sits in Christoph's calendar (takes precedence)
#   AGENDA_CHRISTOPH_EMAILS Christoph's own addresses, to find his answer
#                           (PARTSTAT) among the attendees
# Not accepted yet or only tentatively: shown like an optional event.
UNSURE = {"NEEDS-ACTION", "TENTATIVE"}


def _env_set(name: str) -> set[str]:
    return {part.strip().lower() for part in os.environ.get(name, "").split(",")
            if part.strip()}


OWNERS = ("christoph", "anne")
OWNER_NAMES = {"christoph": "Christoph", "anne": "Anne"}

MARGIN = 24
LABEL_W = 76             # time axis
SLOT = 30                # minutes per grid row
OVERLAY_MIN = 60         # an event starting this long after another overlaps
                         # it (drawn on top, indented) instead of sharing width
OVERLAY_INDENT = 0.3     # share of the underlying block's width
DEFAULT_START = 8 * 60   # grid range; widened when an event lies outside
DEFAULT_END = 18 * 60
MIN_ROW_H = 16           # below this the range falls back to the default
ALLDAY_H = 34

# Split view: from SPLIT_FROM_HOUR on, today's rest goes in the upper part and a
# condensed preview of tomorrow (half the row height) in the lower part -- if
# the lower part gets at least MIN_LOWER_H pixels, else the normal view stays.
SPLIT_FROM_HOUR = 13
UPPER_MIN_MINUTES = 3 * 60   # the upper part covers at least three hours
ROW_H = 30                   # pixels per 30 minutes, normal
ROW_H_COMPACT = 15           # ... in the condensed preview of tomorrow
SPLIT_GAP = 12
HEADING_H = 34               # tomorrow's label and all-day strip
MIN_LOWER_H = 150
COLHEAD_H = 30           # "Christoph" / "Anne" above the columns


@dataclass(frozen=True)
class Entry:
    start: datetime | None      # None: all-day
    end: datetime | None
    title: str
    location: str
    starts_before: bool = False  # began on an earlier day
    ends_after: bool = False     # continues on a later day
    optional: bool = False       # Reclaim.ai event or TRANSP:TRANSPARENT
    owner: str = "christoph"


def fetch_ics(url: str) -> bytes:
    try:
        r = requests.get(url, timeout=FETCH_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        # The exception text would contain the secret URL.
        raise SystemExit(f"fetching the ICS feed failed: {type(exc).__name__}")
    return r.content


def _local(value: date | datetime) -> datetime:
    """A datetime in Berlin time. Floating times count as Berlin time."""
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def _email(value) -> str:
    return str(value).strip().lower().removeprefix("mailto:")


def _as_list(value) -> list:
    return value if isinstance(value, list) else ([] if value is None else [value])


def owner_of(ev) -> str:
    if _email(ev.get("ORGANIZER", "")) in _env_set("AGENDA_ANNE_EMAILS"):
        return "anne"
    calendar = str(ev.get("X-MEINKALENDER-CALENDAR", "")).strip().lower()
    return "anne" if calendar in _env_set("AGENDA_ANNE_CALENDARS") else "christoph"


def partstats(ev) -> list[str]:
    """Christoph's answers to the invitation: PARTSTAT of his ATTENDEE lines
    (standard iCalendar); a PARTSTAT property on the event itself counts too."""
    found = []
    mine = _env_set("AGENDA_CHRISTOPH_EMAILS")
    for attendee in _as_list(ev.get("ATTENDEE")):
        params = getattr(attendee, "params", {})
        if _email(attendee) in mine and params.get("PARTSTAT"):
            found.append(str(params["PARTSTAT"]).upper())
    own = ev.get("PARTSTAT")
    if own is not None:
        found.append(str(own).strip().upper())
    return found


_RECLAIM_DESC = re.compile(r"created by <a[^>]*reclaim", re.IGNORECASE)


def is_reclaim(ev) -> bool:
    """Reclaim.ai creates focus time, lunch, decompress etc. itself.

    Two markers, measured in the feed on 2026-11-08: the UID starts with
    "reclaim" (34 of 115 events), or the description says "created by
    Reclaim.ai" (5 more). Meetings booked through a Reclaim link carry
    neither and count as real appointments.
    """
    if str(ev.get("UID", "")).lower().startswith("reclaim"):
        return True
    return bool(_RECLAIM_DESC.search(str(ev.get("DESCRIPTION", ""))))


def todays_entries(ics: bytes, day: date) -> list[Entry]:
    cal = Calendar.from_ical(ics)
    day_start = datetime.combine(day, time.min, tzinfo=TZ)
    day_end = day_start + timedelta(days=1)

    entries: list[Entry] = []
    # between(), not at(day): at() drops an event that began the evening
    # before and runs into today (measured with recurring-ical-events 3.8.2).
    for ev in recurring_ical_events.of(cal).between(day_start, day_end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        answers = partstats(ev)
        if answers and all(a == "DECLINED" for a in answers):
            continue
        title = str(ev.get("SUMMARY", "")).strip() or "(ohne Titel)"
        location = str(ev.get("LOCATION", "")).strip()
        optional = (str(ev.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT"
                    or is_reclaim(ev)
                    or (bool(answers) and all(a in UNSURE for a in answers)))
        start, end = ev.start, ev.end

        if not isinstance(start, datetime):
            # All-day: DTEND is exclusive, so a one-day event ends the next day.
            entries.append(Entry(None, None, title, location, optional=optional,
                                 owner=owner_of(ev)))
            continue

        s, e = _local(start), _local(end)
        entries.append(Entry(s, e, title, location,
                             starts_before=s < day_start,
                             ends_after=e > day_end, optional=optional,
                             owner=owner_of(ev)))

    far = datetime.max.replace(tzinfo=TZ)
    entries.sort(key=lambda x: (x.start is not None, x.start or far, x.title))
    return entries


def time_label(e: Entry) -> str:
    if e.start is None:
        return "ganztägig"
    start = "…" if e.starts_before else e.start.strftime("%H:%M")
    end = "…" if e.ends_after else e.end.strftime("%H:%M")
    return f"{start}–{end}"


def date_label(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]}, {day.day:02d}.{day.month:02d}.{day.year}"


@lru_cache(maxsize=None)
def _notdef_mask(f) -> bytes:
    # The box DejaVu draws for a codepoint it does not have.
    return bytes(f.getmask("\U0010ffff"))


def printable(text: str, f) -> str:
    """Drop the characters the font cannot draw (mostly emoji).

    Without this they show up as empty boxes on the panel.
    """
    notdef = _notdef_mask(f)
    kept = [ch for ch in text
            if ch.isspace() or bytes(f.getmask(ch)) != notdef]
    return " ".join("".join(kept).split())


def _ellipsize(text: str, f, max_w: int) -> str:
    if text_size(text, f)[0] <= max_w:
        return text
    while text and text_size(text + "…", f)[0] > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


@dataclass
class Block:
    entry: Entry
    start: int        # minutes since midnight
    end: int          # display end: at least one slot long
    col: int = 0
    ncols: int = 1
    x: float = 0.0               # left edge, as a share of the column width
    w: float = 1.0               # width, as a share of the column width
    parent: "Block | None" = None  # block this one is drawn on top of


def _wall_minutes(dt: datetime, day: date) -> int:
    """Wall-clock minutes since midnight of `day`, clipped to 0..1440."""
    if dt.date() < day:
        return 0
    if dt.date() > day:
        return 24 * 60
    return dt.hour * 60 + dt.minute


def layout_blocks(entries: list[Entry], day: date) -> list[Block]:
    """Timed entries as blocks, overlapping ones assigned to side-by-side columns."""
    items = []
    for e in entries:
        if e.start is None:
            continue
        s = _wall_minutes(e.start, day)
        if s >= 24 * 60:
            continue
        end = max(_wall_minutes(e.end, day), s + SLOT)
        items.append(Block(e, s, min(end, 24 * 60)))
    items.sort(key=lambda b: (b.start, -b.end, b.entry.title))

    # An event that starts at least OVERLAY_MIN after another one, while that
    # one is still running, lies on top of it: the earlier block keeps its
    # header visible, the later one reaches into it. Events that start close
    # together would hide each other's title and share the width instead.
    for i, b in enumerate(items):
        under = [a for a in items[:i]
                 if a.start <= b.start - OVERLAY_MIN and a.end > b.start]
        if under:
            b.parent = max(under, key=lambda a: a.start)

    def finish(cluster: list[Block]) -> None:
        col_end: list[int] = []
        for b in cluster:
            for c, until in enumerate(col_end):
                if until <= b.start:
                    b.col, col_end[c] = c, b.end
                    break
            else:
                b.col = len(col_end)
                col_end.append(b.end)
        for b in cluster:
            b.ncols = len(col_end)

    cluster: list[Block] = []
    cluster_end = -1
    for b in (b for b in items if b.parent is None):
        if cluster and b.start >= cluster_end:
            finish(cluster)
            cluster, cluster_end = [], -1
        cluster.append(b)
        cluster_end = max(cluster_end, b.end)
    if cluster:
        finish(cluster)

    for b in items:  # parents start earlier, so their position is known
        if b.parent is None:
            b.x, b.w = b.col / b.ncols, 1 / b.ncols
        else:
            indent = b.parent.w * OVERLAY_INDENT
            b.x, b.w = b.parent.x + indent, b.parent.w - indent
    return items


def grid_range(blocks: list[Block]) -> tuple[int, int]:
    """Start and end of the grid in minutes, whole hours."""
    lo, hi = DEFAULT_START, DEFAULT_END
    for b in blocks:
        lo = min(lo, b.start // 60 * 60)
        hi = max(hi, -(-b.end // 60) * 60)
    return lo, hi


def _clip(blocks: list[Block], lo: int, hi: int) -> list[Block]:
    out = []
    for b in blocks:
        if b.end <= lo or b.start >= hi:
            continue
        out.append(replace(b, start=max(b.start, lo), end=min(b.end, hi)))
    return out


def _draw_block(draw: ImageDraw.ImageDraw, b: Block, x: int, y: int,
                w: int, h: int, compact: bool = False) -> None:
    e = b.entry
    ink = INK
    if e.optional:
        # Lighter background and a dashed outline.
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=FILL)
        pts = [(x, y), (x + w - 1, y), (x + w - 1, y + h - 1), (x, y + h - 1), (x, y)]
        dashed_line(draw, pts, (4, 3) if compact else (6, 4), 1 if compact else 2, DARK)
    else:
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=GRID)
        draw.rectangle([x, y, x + (1 if compact else 3), y + h - 1], fill=INK)

    if compact:
        # Condensed: the title only; the time shows in the row it sits in.
        f = font(12, bold=True)
        title = printable(e.title, f) or "(ohne Titel)"
        # In the block's top row, so an overlapping block below cannot cover it.
        draw.text((x + 6, y + max(0, (min(h, ROW_H_COMPACT) - 14) // 2)),
                  _ellipsize(title, f, w - 10), font=f, fill=ink)
        return

    inner_x, inner_w = x + 10, w - 14
    if h >= 40:
        tf, sf = font(18, bold=True), font(15)
        title = printable(e.title, tf) or "(ohne Titel)"
        draw.text((inner_x, y + 4), _ellipsize(title, tf, inner_w), font=tf, fill=ink)
        sub = time_label(e)
        loc = printable(e.location, sf)
        if loc:
            sub += "  " + loc
        draw.text((inner_x, y + 27), _ellipsize(sub, sf, inner_w), font=sf, fill=DARK)
    else:
        f = font(16, bold=True)
        title = printable(e.title, f) or "(ohne Titel)"
        label = title if e.starts_before else f"{e.start.strftime('%H:%M')} {title}"
        ty = y + max(0, (h - 19) // 2)
        draw.text((inner_x, ty), _ellipsize(label, f, inner_w), font=f, fill=ink)


def _draw_allday(draw: ImageDraw.ImageDraw, entries: list[Entry], y: int,
                 x_start: int, x_end: int) -> None:
    f = font(20, bold=True)
    x = x_start
    for i, e in enumerate(entries):
        title = printable(e.title, f) or "(ohne Titel)"
        w = text_size(title, f)[0] + 28
        more = len(entries) - i - 1
        if x + w > x_end - (60 if more else 0):
            draw.text((x, y + 4), f"+ {len(entries) - i}", font=f, fill=DARK)
            return
        if e.optional:
            draw.rectangle([x, y, x + w - 1, y + ALLDAY_H - 1], fill=FILL)
            pts = [(x, y), (x + w - 1, y), (x + w - 1, y + ALLDAY_H - 1),
                   (x, y + ALLDAY_H - 1), (x, y)]
            dashed_line(draw, pts, (6, 4), 2, DARK)
        else:
            draw.rectangle([x, y, x + w - 1, y + ALLDAY_H - 1], fill=GRID)
            draw.rectangle([x, y, x + 3, y + ALLDAY_H - 1], fill=INK)
        draw.text((x + 14, y + 3), title, font=f, fill=INK)
        x += w + 10


# Header pieces, drawn right-aligned in one row:
#   ("text", string, font, fill) | ("gap", pixels) | ("icon", rising, size)
Piece = tuple


def _info_rows(size: int, weather: Weather | None,
               sun: tuple[str, str] | None) -> list[list[Piece]]:
    """Row 1: temperatures (hours dark gray and small, temperatures bold).
    Row 2: precipitation, sunrise and sunset."""
    hour_font, temp_font, text_font = font(size - 4), font(size + 2, bold=True), font(size)
    rows: list[list[Piece]] = []
    if weather is not None:
        row: list[Piece] = []
        for i, (hour, value) in enumerate(weather.temps):
            if i:
                row.append(("gap", 22))
            row += [("text", f"{hour % 24:02d}:00", hour_font, DARK), ("gap", 6),
                    ("text", weather_mod.degrees(value), temp_font, INK)]
        rows.append(row)
    row2: list[Piece] = []
    if weather is not None:
        kind, text = weather_mod.precip_parts(weather)
        if kind is not None:
            row2 += [("precip", kind, size), ("gap", 8)]
        row2.append(("text", text, text_font, DARK))
    if sun is not None:
        if row2:
            row2.append(("gap", 28))
        row2 += [("icon", True, size), ("gap", 6), ("text", sun[0], text_font, DARK),
                 ("gap", 18),
                 ("icon", False, size), ("gap", 6), ("text", sun[1], text_font, DARK)]
    if row2:
        rows.append(row2)
    return rows


def _piece_width(piece: Piece) -> float:
    if piece[0] == "text":
        return piece[2].getlength(piece[1])
    if piece[0] == "gap":
        return piece[1]
    if piece[0] == "precip":
        return _precip_width(piece[1], piece[2])
    return piece[2] * 1.5      # sun icon


RAIN_W, SNOW_W = 1.25, 1.0     # icon widths as multiples of the font size


def _precip_width(kind: str, size: int) -> float:
    rain = kind in ("Regen", "Schneeregen")
    snow = kind in ("Schnee", "Schneeregen")
    return (RAIN_W * size if rain else 0) + (SNOW_W * size if snow else 0) + (4 if rain and snow else 0)


def _rain_icon(draw: ImageDraw.ImageDraw, x: float, baseline: float, s: int) -> None:
    """A cloud with three slanted raindrops."""
    cb = baseline - s * 0.42                        # bottom edge of the cloud
    draw.rectangle([x + 0.12 * s, cb - 0.2 * s, x + 1.12 * s, cb], fill=DARK)
    for cx, cy, r in ((0.40, 0.28, 0.25), (0.72, 0.40, 0.33), (1.0, 0.24, 0.20)):
        draw.ellipse([x + (cx - r) * s, cb - cy * s - r * s * 0.6,
                      x + (cx + r) * s, cb - cy * s + r * s * 1.4], fill=DARK)
    for i in range(3):
        dx = x + (0.32 + 0.32 * i) * s
        draw.line([(dx + 0.1 * s, cb + 0.1 * s), (dx - 0.05 * s, baseline - 0.02 * s)],
                  fill=DARK, width=2)


def _snow_icon(draw: ImageDraw.ImageDraw, x: float, baseline: float, s: int) -> None:
    """A six-armed snowflake with small side branches."""
    cx, cy, r = x + 0.5 * s, baseline - 0.5 * s, 0.46 * s
    for k in range(6):
        ang = math.radians(90 + 60 * k)
        ex, ey = cx + r * math.cos(ang), cy - r * math.sin(ang)
        draw.line([(cx, cy), (ex, ey)], fill=DARK, width=2)
        bx, by = cx + 0.62 * r * math.cos(ang), cy - 0.62 * r * math.sin(ang)
        for side in (-1, 1):
            a2 = ang + side * math.radians(50)
            draw.line([(bx, by), (bx + 0.34 * r * math.cos(a2), by - 0.34 * r * math.sin(a2))],
                      fill=DARK, width=2)


def _precip_icon(draw: ImageDraw.ImageDraw, x: float, baseline: float, size: int,
                 kind: str) -> None:
    if kind in ("Regen", "Schneeregen"):
        _rain_icon(draw, x, baseline, size)
        x += RAIN_W * size + 4
    if kind in ("Schnee", "Schneeregen"):
        _snow_icon(draw, x, baseline, size)


def _sun_icon(draw: ImageDraw.ImageDraw, x: float, baseline: float, size: int,
              rising: bool) -> None:
    """Half sun on the horizon with a small arrow: up = sunrise, down = sunset."""
    w = size * 1.5
    horizon = baseline - size * 0.15
    cx, r = x + size * 0.55, size * 0.36
    draw.line([(x, horizon), (x + size * 1.1, horizon)], fill=DARK, width=2)
    draw.pieslice([cx - r, horizon - r, cx + r, horizon + r], 180, 360, fill=DARK)
    for ang in (90, 45, 135):                       # rays: up, up-right, up-left
        dx, dy = math.cos(math.radians(ang)), -math.sin(math.radians(ang))
        draw.line([(cx + dx * (r + 3), horizon + dy * (r + 3)),
                   (cx + dx * (r + 7), horizon + dy * (r + 7))], fill=DARK, width=2)
    ax = x + w - size * 0.2                          # arrow right of the sun
    top, bottom, half = baseline - size * 0.85, baseline - size * 0.2, size * 0.2
    if rising:
        draw.polygon([(ax, top), (ax - half, top + half * 1.6), (ax + half, top + half * 1.6)],
                     fill=DARK)
    else:
        draw.polygon([(ax, bottom), (ax - half, bottom - half * 1.6), (ax + half, bottom - half * 1.6)],
                     fill=DARK)
    draw.line([(ax, top + half), (ax, bottom - half)], fill=DARK, width=2)


def _info_fit(width: int, date_right: int, weather: Weather | None,
              sun: tuple[str, str] | None) -> int | None:
    """Largest font size at which the header info fits beside the date."""
    right = width - MARGIN - BATTERY_ZONE[0] - 16
    for size in (24, 22, 20, 18):
        rows = _info_rows(size, weather, sun)
        widest = max((sum(_piece_width(p) for p in row) for row in rows), default=0)
        if right - widest > date_right + 24:
            return size
    return None


def _draw_info(draw: ImageDraw.ImageDraw, width: int, size: int,
               weather: Weather | None, sun: tuple[str, str] | None) -> None:
    """Right-aligned, left of the firmware's battery symbol."""
    right = width - MARGIN - BATTERY_ZONE[0] - 16
    for i, row in enumerate(_info_rows(size, weather, sun)):
        baseline = MARGIN + size + 4 + i * (size + 8)
        x = right - sum(_piece_width(p) for p in row)
        for piece in row:
            if piece[0] == "text":
                draw.text((x, baseline), piece[1], font=piece[2], fill=piece[3], anchor="ls")
            elif piece[0] == "precip":
                _precip_icon(draw, x, baseline, piece[2], piece[1])
            elif piece[0] == "icon":
                _sun_icon(draw, x, baseline, piece[2], piece[1])
            x += _piece_width(piece)


def _draw_day(draw: ImageDraw.ImageDraw, *, x0: int, x1: int,
              col_x: dict[str, tuple[int, int]], grid_top: int, lo: int, hi: int,
              row_h: int, per_owner: dict[str, list[Block]],
              compact: bool = False) -> int:
    """Time axis, grid lines and blocks of one day; returns the grid's bottom."""
    rows = (hi - lo) // SLOT
    label_font = font(12 if compact else 15)
    for i in range(rows + 1):
        y = grid_top + i * row_h
        minute = lo + i * SLOT
        hour_line = minute % 60 == 0
        if hour_line or not compact:
            draw.line([(x0, y), (x1, y)], fill=GRID if hour_line else FILL, width=1)
        if hour_line and i < rows:
            draw.text((x0 - 10, y - (7 if compact else 9)), f"{minute // 60:02d}:00",
                      font=label_font, fill=DARK, anchor="ra")
    grid_bottom = grid_top + rows * row_h
    for x in [x0] + [col_x[o][1] for o in OWNERS]:
        draw.line([(x, grid_top), (x, grid_bottom)], fill=GRID, width=1)

    for o in OWNERS:
        cx0, cx1 = col_x[o]
        clipped = _clip(per_owner[o], lo, hi)
        # Blocks underneath first, then the ones lying on top of them.
        for b in sorted(clipped, key=lambda b: b.parent is not None):
            bx = cx0 + round(b.x * (cx1 - cx0)) + 2
            bw = round(b.w * (cx1 - cx0)) - 4
            by = grid_top + round((b.start - lo) / SLOT * row_h) + 1
            bh = max(1, round((b.end - b.start) / SLOT * row_h) - 2)
            if b.parent is not None:
                draw.rectangle([bx - 3, by - 2, bx + bw + 1, by + bh + 1], fill=PAPER)
            _draw_block(draw, b, bx, by, bw, bh, compact)
    return grid_bottom


@dataclass(frozen=True)
class SplitPlan:
    upper: tuple[int, int]     # today's range in minutes
    lower: tuple[int, int]     # tomorrow's range in minutes
    lower_top: int             # y of tomorrow's grid
    heading_y: int


def plan_split(now: datetime, today: dict[str, list[Block]],
               tomorrow: dict[str, list[Block]], grid_top: int,
               bottom: int) -> SplitPlan | None:
    """Where to cut the calendar part, or None when there is no room.

    Upper part: from the hour of the run (it wanders with the time) for at
    least three hours, longer while today's events run on. Lower part:
    tomorrow at half the row height, from 08:00 (earlier if needed) as far as
    the space goes.
    """
    if now.hour < SPLIT_FROM_HOUR:
        return None
    start = now.hour * 60
    latest = max((b.end for bl in today.values() for b in bl if b.end > start),
                 default=0)
    end = min(24 * 60, max(start + UPPER_MIN_MINUTES, -(-latest // 60) * 60))
    upper_h = (end - start) // SLOT * ROW_H

    heading_y = grid_top + upper_h + SPLIT_GAP
    lower_top = heading_y + HEADING_H + 4
    lower_h = bottom - lower_top
    if lower_h < MIN_LOWER_H:
        return None

    blocks = [b for bl in tomorrow.values() for b in bl]
    lo = min([DEFAULT_START] + [b.start // 60 * 60 for b in blocks])
    want = max([DEFAULT_END] + [-(-b.end // 60) * 60 for b in blocks])
    fit = lower_h // (2 * ROW_H_COMPACT)          # hours that fit
    hi = min(want, lo + fit * 60, 24 * 60)
    return SplitPlan((start, end), (lo, hi), lower_top, heading_y)


def render_png(width: int, height: int, day: date, entries: list[Entry],
               weather: Weather | None = None,
               sun: tuple[str, str] | None = None,
               now: datetime | None = None,
               tomorrow: tuple[date, list[Entry]] | None = None) -> bytes:
    img = Image.new("L", (width, height), PAPER)
    draw = ImageDraw.Draw(img)

    # Header: the date, left; the weather right of it. The top-right corner
    # belongs to the firmware.
    label = date_label(day)
    info_size = None
    for date_size in (44, 38, 32):
        date_font = font(date_size, bold=True)
        date_right = MARGIN + text_size(label, date_font)[0]
        info_size = _info_fit(width, date_right, weather, sun)
        if info_size is not None or (weather is None and sun is None):
            break
    draw.text((MARGIN, MARGIN), label, font=date_font, fill=INK)
    if info_size is not None:
        _draw_info(draw, width, info_size, weather, sun)
    rule_y = MARGIN + HEADER_HEIGHT - 8
    draw.line([(MARGIN, rule_y), (width - MARGIN, rule_y)], fill=INK, width=2)

    top = MARGIN + HEADER_HEIGHT
    bottom = height - CLOCK_ZONE[1]

    tomorrow_entries = tomorrow[1] if tomorrow else []
    split_possible = (now is not None and tomorrow is not None
                      and now.hour >= SPLIT_FROM_HOUR)
    if not entries and not (split_possible and tomorrow_entries):
        draw.text((MARGIN, top + 20), "Keine Termine heute",
                  font=font(32), fill=DARK)
        return _encode(img)

    x0, x1 = MARGIN + LABEL_W, width - MARGIN
    col_w = (x1 - x0) / len(OWNERS)
    col_x = {o: (round(x0 + i * col_w), round(x0 + (i + 1) * col_w))
             for i, o in enumerate(OWNERS)}

    # Column heads.
    head_font = font(20, bold=True)
    for o in OWNERS:
        draw.text((col_x[o][0] + 10, top + 2), OWNER_NAMES[o], font=head_font, fill=INK)
    grid_top = top + COLHEAD_H

    allday = {o: [e for e in entries if e.start is None and e.owner == o] for o in OWNERS}
    if any(allday.values()):
        for o in OWNERS:
            _draw_allday(draw, allday[o], grid_top + 2, col_x[o][0] + 6, col_x[o][1] - 6)
        grid_top += ALLDAY_H + 12
    grid_h = bottom - grid_top

    per_owner = {o: layout_blocks([e for e in entries if e.owner == o], day) for o in OWNERS}

    plan = None
    if split_possible:
        next_day, next_entries = tomorrow
        next_blocks = {o: layout_blocks([e for e in next_entries if e.owner == o], next_day)
                       for o in OWNERS}
        plan = plan_split(now, per_owner, next_blocks, grid_top, bottom)

    if plan is None:
        lo, hi = grid_range([b for bl in per_owner.values() for b in bl])
        if grid_h // ((hi - lo) // SLOT) < MIN_ROW_H:
            lo, hi = DEFAULT_START, DEFAULT_END
        _draw_day(draw, x0=x0, x1=x1, col_x=col_x, grid_top=grid_top, lo=lo, hi=hi,
                  row_h=grid_h // ((hi - lo) // SLOT), per_owner=per_owner)
        return _encode(img)

    # Split view: today's rest above, tomorrow condensed below.
    _draw_day(draw, x0=x0, x1=x1, col_x=col_x, grid_top=grid_top,
              lo=plan.upper[0], hi=plan.upper[1], row_h=ROW_H, per_owner=per_owner)

    sep_y = plan.heading_y - 4
    draw.line([(MARGIN, sep_y), (x1, sep_y)], fill=INK, width=2)
    wd = WEEKDAYS[next_day.weekday()]
    day_font = font(15, bold=True)
    draw.text((MARGIN, plan.heading_y + 1), wd, font=day_font, fill=INK)
    draw.text((MARGIN, plan.heading_y + 17), f"{next_day.day:02d}.{next_day.month:02d}.",
              font=day_font, fill=INK)
    next_allday = {o: [e for e in next_entries if e.start is None and e.owner == o]
                   for o in OWNERS}
    for o in OWNERS:
        _draw_allday(draw, next_allday[o], plan.heading_y + 1, col_x[o][0] + 6, col_x[o][1] - 6)

    lo, hi = plan.lower
    _draw_day(draw, x0=x0, x1=x1, col_x=col_x, grid_top=plan.lower_top, lo=lo, hi=hi,
              row_h=ROW_H_COMPACT, per_owner=next_blocks, compact=True)
    if not next_entries:
        draw.text((x0 + 12, plan.lower_top + 8), "Keine Termine",
                  font=font(16), fill=DARK)

    return _encode(img)


def _encode(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    snap_to_levels(img).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"environment variable {name} is missing")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render today's calendar onto the Inkplate")
    parser.add_argument("--out", help="also write the PNG to this file")
    parser.add_argument("--no-push", action="store_true",
                        help="do not send it to the eink service")
    parser.add_argument("--ics-file", help="read the feed from a file instead of ICS_URL")
    parser.add_argument("--no-weather", action="store_true",
                        help="leave the weather out of the header")
    parser.add_argument("--date", help="render this day (YYYY-MM-DD) instead of today")
    parser.add_argument("--at", help="pretend it is HH:MM on that day (to try the split view)")
    args = parser.parse_args(argv)

    width = int(os.environ.get("PANEL_WIDTH", "1200"))
    height = int(os.environ.get("PANEL_HEIGHT", "825"))
    day = date.fromisoformat(args.date) if args.date else datetime.now(TZ).date()

    if args.ics_file:
        with open(args.ics_file, "rb") as fh:
            ics = fh.read()
    else:
        ics = fetch_ics(_env("ICS_URL"))

    entries = todays_entries(ics, day)
    # The place is set in the deployment (WEATHER_LAT, WEATHER_LON); without it
    # the header shows neither weather nor sun times.
    weather = sun = None
    if os.environ.get("WEATHER_LAT") and os.environ.get("WEATHER_LON"):
        lat, lon = float(os.environ["WEATHER_LAT"]), float(os.environ["WEATHER_LON"])
        weather = None if args.no_weather else weather_mod.fetch_weather(lat, lon, day)
        sun = sun_mod.labels(lat, lon, day)

    # The split view depends on the time of the run; other days only with --at.
    now = None
    if args.at:
        now = datetime.combine(day, time.fromisoformat(args.at), tzinfo=TZ)
    elif day == datetime.now(TZ).date():
        now = datetime.now(TZ)
    tomorrow = None
    if now is not None:
        next_day = day + timedelta(days=1)
        tomorrow = (next_day, todays_entries(ics, next_day))
    png = render_png(width, height, day, entries, weather, sun, now, tomorrow)
    print(f"[render] agenda {day.isoformat()}: {len(entries)} entries, "
          f"{width}×{height}, {len(png)} bytes")

    if args.out:
        with open(args.out, "wb") as fh:
            fh.write(png)
        print(f"[out] {args.out}")

    if args.no_push:
        return 0

    meta = push_png(_env("EINK_BASE_URL"), _env("EINK_PUSH_TOKEN"),
                    os.environ.get("EINK_CHANNEL", "inkplate10"), png)
    print(f"[push] hash={meta.get('last_modified')} size={meta.get('size')}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
