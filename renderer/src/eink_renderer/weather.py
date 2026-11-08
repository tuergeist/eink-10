"""Weather of the day from Bright Sky (DWD open data), for the agenda header.

One call to ``/weather`` for the hourly forecast of the day: the temperature
at a few fixed hours, the precipitation sum and the highest precipitation
probability.

The weather is an extra. If Bright Sky does not answer, the agenda is drawn
without it — a missing weather line must not keep the calendar off the panel.

The fixed hours (not "now") keep the image the same between runs unless the
forecast itself changes, which saves panel refreshes.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://api.brightsky.dev"
TZ = "Europe/Berlin"
TIMEOUT = 15

# Hours shown in the header; 24 is midnight at the end of the day.
HOURS = (8, 12, 16, 24)


@dataclass(frozen=True)
class Weather:
    temps: tuple[tuple[int, float | None], ...]  # (hour, temperature)
    precip_mm: float                             # sum over the day
    precip_prob: int | None                      # highest hourly probability, percent
    kind: str = "Regen"                          # Regen, Schnee or Schneeregen


def summarize(records: list[dict], day: date) -> Weather | None:
    """Reduce hourly records to the figures of the local calendar `day`."""
    tz = ZoneInfo(TZ)
    local = [(datetime.fromisoformat(r["timestamp"]).astimezone(tz), r) for r in records]
    midnight = datetime.combine(day, time.min, tzinfo=tz)

    temps = []
    for hour in HOURS:
        at = midnight + timedelta(hours=hour)
        value = next((r.get("temperature") for ts, r in local if ts == at), None)
        temps.append((hour, value))
    if all(v is None for _, v in temps):
        return None

    of_day = [r for ts, r in local if ts.date() == day]
    probs = [r["precipitation_probability"] for r in of_day
             if r.get("precipitation_probability") is not None]
    precip = sum(r.get("precipitation") or 0.0 for r in of_day)
    wet = {r.get("condition") for r in of_day if (r.get("precipitation") or 0.0) > 0}
    return Weather(tuple(temps), round(precip, 1), max(probs) if probs else None,
                   _kind(wet))


def _kind(conditions: set) -> str:
    """The word for the precipitation, from Bright Sky's `condition` of the wet hours."""
    if "sleet" in conditions or ("snow" in conditions and "rain" in conditions):
        return "Schneeregen"
    if "snow" in conditions:
        return "Schnee"
    return "Regen"


def fetch_weather(lat: float, lon: float, day: date) -> Weather | None:
    """Weather for `day`; None if the service is unreachable or has no data."""
    try:
        r = requests.get(f"{BASE_URL}/weather", timeout=TIMEOUT, params={
            "lat": lat, "lon": lon, "tz": TZ,
            "date": day.isoformat(),
            "last_date": (day + timedelta(days=1)).isoformat(),
        })
        r.raise_for_status()
        records = r.json()["weather"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"[warn] weather unavailable: {type(exc).__name__}", file=sys.stderr)
        return None
    return summarize(records, day)


def degrees(value: float | None) -> str:
    return "–" if value is None else f"{math.floor(value + 0.5):.0f}°"


# Below this the sum counts as dry: 0,0 mm would be false precision.
DRY_BELOW_MM = 0.1


def _amount(mm: float) -> str:
    """Litres per square metre; 1 mm of precipitation is 1 l/m²."""
    litres = math.floor(mm + 0.5)
    return "unter 1 l/m²" if litres < 1 else f"{litres:.0f} l/m²"


def precip_parts(w: Weather) -> tuple[str | None, str]:
    """(kind, text): kind is "Regen"/"Schnee"/"Schneeregen" for a symbol, or
    None on a dry day, where the text alone says it: "Kein Regen"."""
    if w.precip_mm < DRY_BELOW_MM:
        return None, "Kein Regen"
    head = f"{w.precip_prob}%  ·  " if w.precip_prob is not None else ""
    return w.kind, f"{head}{_amount(w.precip_mm)}"
