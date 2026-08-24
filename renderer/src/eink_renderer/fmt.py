"""Number and date formatting — pure functions.

Output is German (comma decimal separator, dot thousands separator): the
dashboard is German and so is whoever walks past the panel. Only the
prose here is English.

Deliberate deviation from Grafana: its ``short`` unit abbreviates above
1000 (``1.28 K``). On a wall display read in passing, the full number with
thousands separators is more useful than a rounded short form. Only from a
million upwards do we abbreviate, because otherwise the tile would have to
shrink the type past legibility.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

# Europe/Berlin without a tzdata dependency would be wrong (daylight
# saving), so the zone comes from zoneinfo; the container installs tzdata.
try:  # pragma: no cover - environment dependent
    from zoneinfo import ZoneInfo

    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover
    BERLIN = timezone(timedelta(hours=1))


def de_group(text: str) -> str:
    """Insert thousands dots into a digit string (integer part only)."""
    neg = text.startswith("-")
    if neg:
        text = text[1:]
    out = []
    for i, ch in enumerate(reversed(text)):
        if i and i % 3 == 0:
            out.append(".")
        out.append(ch)
    return ("-" if neg else "") + "".join(reversed(out))


def format_value(value: float | None, unit: str = "short",
                 decimals: int | None = None) -> str:
    """Format one measurement for display.

    ``unit`` follows Grafana's unit keys; handled are ``percent`` (value is
    already 0..100), ``percentunit`` (0..1), and everything else as a plain
    number.
    """
    if value is None:
        return "–"

    suffix = ""
    if unit == "percent":
        suffix = " %"
        if decimals is None:
            decimals = 1
    elif unit == "percentunit":
        value = value * 100
        suffix = " %"
        if decimals is None:
            decimals = 1

    if decimals is None:
        # Whole numbers without a fraction, fractional ones with one digit.
        decimals = 0 if float(value).is_integer() else 1

    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}".replace(".", ",") + " Mio." + suffix

    text = f"{value:.{decimals}f}"
    if "." in text:
        whole, frac = text.split(".")
        return de_group(whole) + "," + frac + suffix
    return de_group(text) + suffix


def format_axis_value(value: float) -> str:
    """Short form for axis labels — there, space beats precision."""
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:g}".replace(".", ",") + " M"
    if abs(value) >= 10_000:
        return f"{value / 1000:g}".replace(".", ",") + "k"
    if float(value).is_integer():
        return de_group(f"{int(value)}")
    return f"{value:g}".replace(".", ",")


def from_millis(ms: float) -> datetime:
    """Grafana reports timestamps as milliseconds since the epoch, UTC."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(BERLIN)


def format_day(ms: float) -> str:
    """``24.08.`` — day label on the time axis."""
    return from_millis(ms).strftime("%d.%m.")


def format_range(from_ms: float, to_ms: float) -> str:
    """``26.07. – 24.08.2026`` — the period, shown in the header."""
    a, b = from_millis(from_ms), from_millis(to_ms)
    return f"{a.strftime('%d.%m.')} – {b.strftime('%d.%m.%Y')}"
