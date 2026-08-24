"""Draw the image.

Everything happens in an 8-bit grayscale image using nothing but the eight
panel gray levels from ``grays``. At the end ``snap_to_levels`` also pulls
the soft glyph edges onto those levels, so the server has nothing left to
dither.

Two areas stay empty because the firmware draws there itself: the clock
bottom right and the low-battery symbol top right. The measurements come
from ``firmware/src/main.cpp`` (drawClockOverlay, drawPowerStatusOverlay)
and are rounded up generously here.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .fonts import fit_font, font, text_size
from .grays import BORDER, DARK, FILL, GRID, INK, MID, PAPER, level_lut
from .layout import Rect
from .panels import KIND_STAT, KIND_TIMESERIES, Panel
from .scale import nice_ticks, project
from . import fmt

# Corners claimed by the firmware (width × height, from that edge).
CLOCK_ZONE = (230, 48)     # bottom right: "YYYY-MM-DD HH:MM" at textSize 2
BATTERY_ZONE = (70, 44)    # top right: battery symbol

HEADER_HEIGHT = 76

# Line styles for several series in one chart. On eight gray levels the
# pattern carries the distinction, not the shade — two similarly light
# lines are the same line from two metres away.
SERIES_STYLES = [
    {"gray": INK, "width": 4, "dash": None},
    {"gray": INK, "width": 3, "dash": (16, 9)},
    {"gray": DARK, "width": 3, "dash": (4, 7)},
    {"gray": MID, "width": 3, "dash": (18, 6, 4, 6)},
]


def style_for(index: int) -> dict:
    return SERIES_STYLES[index % len(SERIES_STYLES)]


def snap_to_levels(img: Image.Image) -> Image.Image:
    """Snap every pixel onto the nearest of the eight panel gray levels."""
    return img.point(level_lut())


def dashed_line(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]],
                pattern: tuple[int, ...] | None, width: int, gray: int) -> None:
    """Draw a polyline, optionally with a dash pattern.

    Pillow has no dashed lines, so the pattern is walked along the arc
    length. That keeps it even across corners instead of restarting at
    every vertex.
    """
    if len(points) < 2:
        if points:
            x, y = points[0]
            r = width / 2
            draw.ellipse([x - r, y - r, x + r, y + r], fill=gray)
        return
    if not pattern:
        draw.line(points, fill=gray, width=width, joint="curve")
        return

    idx, remaining, painting = 0, pattern[0], True
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        seg = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if seg == 0:
            continue
        pos = 0.0
        while pos < seg:
            take = min(remaining, seg - pos)
            a = (x0 + (x1 - x0) * pos / seg, y0 + (y1 - y0) * pos / seg)
            b = (x0 + (x1 - x0) * (pos + take) / seg,
                 y0 + (y1 - y0) * (pos + take) / seg)
            if painting:
                draw.line([a, b], fill=gray, width=width)
            pos += take
            remaining -= take
            if remaining <= 1e-9:
                idx = (idx + 1) % len(pattern)
                remaining = pattern[idx]
                painting = not painting


def _text(draw: ImageDraw.ImageDraw, xy, text: str, f, gray: int, anchor: str = "la"):
    draw.text(xy, text, font=f, fill=gray, anchor=anchor)


def _ellipsize(text: str, f, max_w: int) -> str:
    if text_size(text, f)[0] <= max_w:
        return text
    while text and text_size(text + "…", f)[0] > max_w:
        text = text[:-1]
    return text + "…"


def wrap_lines(text: str, f, max_w: int, max_lines: int = 2) -> list[str]:
    """Wrap text onto at most ``max_lines`` lines, at word boundaries.

    Panel titles like "Verschiedene POIs (30 Tage)" do not fit a
    quarter-width tile on one line. Truncating would swallow exactly the
    distinguishing part; two lines keep it.
    """
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if text_size(candidate, f)[0] <= max_w:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    else:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    # Whatever no longer fits ends as an ellipsis on the last line.
    used = sum(len(l.split()) for l in lines)
    if used < len(words):
        lines[-1] = _ellipsize(lines[-1] + " " + " ".join(words[used:]), f, max_w)
    return [_ellipsize(l, f, max_w) for l in lines]


def draw_header(draw: ImageDraw.ImageDraw, box: Rect, title: str, subtitle: str) -> None:
    """Title left, time range right, a rule underneath.

    The width of the battery symbol stays free on the right — it sits top
    right and would otherwise cover the range as soon as a cell weakens.
    """
    f_sub = font(20)
    sub_w = text_size(subtitle, f_sub)[0]
    right = box.right - BATTERY_ZONE[0]
    f_title = fit_font(title, box.w - BATTERY_ZONE[0] - sub_w - 40, 40,
                       bold=True, largest=38)
    baseline = box.y + 44
    _text(draw, (box.x, baseline), title, f_title, INK, anchor="ls")
    _text(draw, (right, baseline), subtitle, f_sub, DARK, anchor="rs")
    y = box.y + HEADER_HEIGHT - 20
    draw.rectangle([box.x, y, box.right, y + 3], fill=INK)


def draw_stat(draw: ImageDraw.ImageDraw, rect: Rect, panel: Panel) -> None:
    draw.rectangle([rect.x, rect.y, rect.right, rect.bottom], outline=BORDER, width=2)
    inner = rect.inset(16, 12)

    f_label = font(19)
    lines = wrap_lines(panel.title, f_label, inner.w, max_lines=2)
    line_h = text_size("Ag", f_label)[1] + 6
    label_h = line_h * len(lines)

    text = fmt.format_value(panel.value, panel.unit, panel.decimals)
    f_value = fit_font(text, inner.w, inner.h - label_h - 18, bold=True, largest=110)

    vh = text_size(text, f_value)[1]
    cx = inner.x + inner.w / 2
    top = inner.y + (inner.h - label_h - 14 - vh) / 2
    _text(draw, (cx, top), text, f_value, INK, anchor="ma")
    for i, line in enumerate(lines):
        _text(draw, (cx, inner.bottom - label_h + i * line_h), line,
              f_label, DARK, anchor="ma")


def draw_timeseries(draw: ImageDraw.ImageDraw, rect: Rect, panel: Panel,
                    t_from: float, t_to: float) -> None:
    draw.rectangle([rect.x, rect.y, rect.right, rect.bottom], outline=BORDER, width=2)
    inner = rect.inset(16, 12)

    f_title = font(21, bold=True)
    title = _ellipsize(panel.title, f_title, inner.w)
    _text(draw, (inner.x, inner.y), title, f_title, INK)
    top = inner.y + text_size(title, f_title)[1] + 14

    multi = len(panel.series) > 1
    f_legend = font(17)
    legend_h = (text_size("Ag", f_legend)[1] + 10) if multi else 0
    f_axis = font(16)
    axis_h = text_size("00.00.", f_axis)[1] + 10

    # Value range across all series; zero stays in, so the height of a
    # curve reflects its magnitude rather than the chosen crop.
    values = [v for s in panel.series for v in s.values if v is not None]
    lo, hi, ticks = nice_ticks(min(values + [0.0]), max(values + [0.0]))

    label_w = max(text_size(fmt.format_axis_value(t), f_axis)[0] for t in ticks)
    plot = Rect(inner.x + label_w + 10, top,
                inner.w - label_w - 10,
                inner.bottom - legend_h - axis_h - top)
    if plot.w < 40 or plot.h < 40:
        return

    # Grid and value labels.
    for t in ticks:
        y = round(project(t, lo, hi, plot.bottom, plot.y))
        draw.line([plot.x, y, plot.right, y], fill=GRID, width=1)
        _text(draw, (plot.x - 8, y), fmt.format_axis_value(t), f_axis, DARK, anchor="rm")
    draw.line([plot.x, plot.y, plot.x, plot.bottom], fill=INK, width=2)
    draw.line([plot.x, plot.bottom, plot.right, plot.bottom], fill=INK, width=2)

    # Time axis: roughly five labels, aligned to whole days.
    span_days = max(1, round((t_to - t_from) / 86_400_000))
    step = max(1, -(-span_days // 5))
    day = 86_400_000
    for i in range(0, span_days + 1, step):
        ts = t_from + i * day
        if ts > t_to:
            break
        x = round(project(ts, t_from, t_to, plot.x, plot.right))
        draw.line([x, plot.bottom, x, plot.bottom + 5], fill=INK, width=2)
        label = fmt.format_day(ts)
        half = text_size(label, f_axis)[0] / 2
        # Pull the outermost labels inwards, otherwise half of them sit
        # outside the tile.
        anchor, lx = "ma", x
        if x + half > inner.right:
            anchor, lx = "ra", inner.right
        elif x - half < inner.x:
            anchor, lx = "la", inner.x
        _text(draw, (lx, plot.bottom + 8), label, f_axis, DARK, anchor=anchor)

    # Series.
    for i, s in enumerate(panel.series):
        st = style_for(i)
        pts = [
            (project(t, t_from, t_to, plot.x, plot.right),
             project(v, lo, hi, plot.bottom, plot.y))
            for t, v in zip(s.times, s.values) if v is not None
        ]
        if not pts:
            continue
        if not multi:
            # Single series: fill the area underneath. With several, the
            # fills would cover each other.
            base = project(max(lo, 0.0), lo, hi, plot.bottom, plot.y)
            draw.polygon([(pts[0][0], base)] + pts + [(pts[-1][0], base)], fill=FILL)
        dashed_line(draw, pts, st["dash"], st["width"], st["gray"])

    if multi:
        x = plot.x
        y = inner.bottom - legend_h + 4
        for i, s in enumerate(panel.series):
            st = style_for(i)
            sample_w = 34
            if x + sample_w + 8 + text_size(s.name, f_legend)[0] > inner.right:
                break
            dashed_line(draw, [(x, y + 6), (x + sample_w, y + 6)],
                        st["dash"], st["width"], st["gray"])
            _text(draw, (x + sample_w + 8, y), s.name, f_legend, DARK)
            x += sample_w + 8 + text_size(s.name, f_legend)[0] + 26


def draw_unsupported(draw: ImageDraw.ImageDraw, rect: Rect, panel: Panel) -> None:
    draw.rectangle([rect.x, rect.y, rect.right, rect.bottom], outline=BORDER, width=2)
    inner = rect.inset(16, 12)
    f_title = font(21, bold=True)
    _text(draw, (inner.x, inner.y), _ellipsize(panel.title, f_title, inner.w),
          f_title, INK)
    f_note = font(17)
    _text(draw, (inner.x, inner.y + 34), _ellipsize(panel.note, f_note, inner.w),
          f_note, DARK)


def render(width: int, height: int, title: str, panels: list[Panel],
           rects: dict[int, Rect], t_from: float, t_to: float) -> Image.Image:
    img = Image.new("L", (width, height), PAPER)
    draw = ImageDraw.Draw(img)
    margin = 24

    header = Rect(margin, margin, width - 2 * margin, HEADER_HEIGHT)
    draw_header(draw, header, title, fmt.format_range(t_from, t_to))

    for p in panels:
        rect = rects.get(p.id)
        if rect is None:
            continue
        if p.kind == KIND_STAT:
            draw_stat(draw, rect, p)
        elif p.kind == KIND_TIMESERIES:
            draw_timeseries(draw, rect, p, t_from, t_to)
        else:
            draw_unsupported(draw, rect, p)

    return snap_to_levels(img)


def content_box(width: int, height: int, margin: int = 24) -> Rect:
    """The area below the header and above the firmware's clock."""
    top = margin + HEADER_HEIGHT
    return Rect(margin, top, width - 2 * margin, height - top - CLOCK_ZONE[1])
