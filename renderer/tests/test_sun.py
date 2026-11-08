from datetime import date

from eink_renderer import sun

LAT, LON = 51.3, 12.4   # near Leipzig


def minutes(dt):
    return dt.hour * 60 + dt.minute + dt.second / 60


def clock(text):
    h, m, s = map(int, text.split(":"))
    return h * 60 + m + s / 60


def test_matches_sunrise_sunset_org_within_three_minutes():
    # sunrise-sunset.org, converted to Berlin time, measured 2026-11-08.
    expected = {
        date(2026, 11, 10): ("07:19:10", "16:29:39"),
        date(2026, 6, 21): ("04:54:20", "21:30:19"),
        date(2026, 3, 29): ("06:52:24", "19:38:09"),   # day of the change to summer time
    }
    for day, (rise, set_) in expected.items():
        got = sun.sun_times(LAT, LON, day)
        assert abs(minutes(got[0]) - clock(rise)) < 3, day
        assert abs(minutes(got[1]) - clock(set_)) < 3, day


def test_labels_are_hh_mm_in_berlin_time():
    assert sun.labels(LAT, LON, date(2026, 11, 10)) == ("07:20", "16:31")


def test_polar_night_has_no_times():
    assert sun.sun_times(78.0, 15.0, date(2026, 12, 21)) is None
