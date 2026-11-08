"""Sunrise and sunset, computed locally.

Bright Sky has no sunrise endpoint, and a lookup per run is not worth a
network dependency: the times follow from date and position alone. This is
the common "sunrise equation" (mean solar noon, equation of centre, solar
declination, hour angle for the apparent horizon at -0.833°).

Checked on 2026-11-08 against sunrise-sunset.org for a place near Leipzig on three
dates (10 Nov, 21 Jun, 29 Mar): sunrise differs by 0.6 to 2.1 minutes,
sunset by 0.1 to 1.8 minutes, always later here. Which of the two is closer
to the official figure was not checked. For a display of whole minutes that
is acceptable, but the shown minute can be off by one or two.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
J2000 = date(2000, 1, 1)


def sun_times(lat: float, lon: float, day: date) -> tuple[datetime, datetime] | None:
    """Sunrise and sunset of `day` in Berlin time; None in polar day or night."""
    rad = math.radians
    n = (day - J2000).days + 0.0008
    j_star = n - lon / 360.0                       # mean solar time
    m = (357.5291 + 0.98560028 * j_star) % 360     # solar mean anomaly
    c = (1.9148 * math.sin(rad(m)) + 0.0200 * math.sin(rad(2 * m))
         + 0.0003 * math.sin(rad(3 * m)))          # equation of the centre
    lam = (m + c + 180 + 102.9372) % 360           # ecliptic longitude
    j_transit = j_star + 0.0053 * math.sin(rad(m)) - 0.0069 * math.sin(rad(2 * lam))
    sin_dec = math.sin(rad(lam)) * math.sin(rad(23.4397))
    dec = math.asin(sin_dec)

    cos_w = ((math.sin(rad(-0.833)) - math.sin(rad(lat)) * sin_dec)
             / (math.cos(rad(lat)) * math.cos(dec)))
    if not -1 <= cos_w <= 1:
        return None
    w0 = math.degrees(math.acos(cos_w))

    noon_j2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)

    def at(days_since_j2000: float) -> datetime:
        return (noon_j2000 + timedelta(days=days_since_j2000)).astimezone(TZ)

    return at(j_transit - w0 / 360.0), at(j_transit + w0 / 360.0)


def labels(lat: float, lon: float, day: date) -> tuple[str, str] | None:
    """"HH:MM" of sunrise and sunset, rounded to the nearest minute."""
    times = sun_times(lat, lon, day)
    if times is None:
        return None

    def hhmm(dt: datetime) -> str:
        return (dt + timedelta(seconds=30)).strftime("%H:%M")

    return hhmm(times[0]), hhmm(times[1])
