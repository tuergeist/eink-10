from datetime import date

from eink_renderer import agenda, weather

DAY = date(2026, 11, 10)


def rec(ts, temp, mm=0.0, prob=None, condition="dry"):
    return {"timestamp": ts, "temperature": temp, "precipitation": mm,
            "precipitation_probability": prob, "condition": condition}


RECORDS = [
    rec("2026-11-09T23:00:00+01:00", -9.0, 5.0, 90),   # the day before
    rec("2026-11-10T00:00:00+01:00", 2.3, 0.0, 2),
    rec("2026-11-10T08:00:00+01:00", 2.4, 0.0, 1),
    rec("2026-11-10T12:00:00+01:00", 5.5, 0.4, 40, "rain"),
    rec("2026-11-10T14:00:00+01:00", 6.1, 1.0, 20),
    rec("2026-11-10T16:00:00+01:00", 4.4, 0.0, 10),
    rec("2026-11-10T20:00:00+01:00", 1.8, 1.0, 80, "rain"),
    rec("2026-11-11T00:00:00+01:00", 1.6, 0.0, 2),      # midnight at the end
]


def test_temperatures_at_the_fixed_hours_with_midnight_at_the_end():
    w = weather.summarize(RECORDS, DAY)
    assert w.temps == ((8, 2.4), (12, 5.5), (16, 4.4), (24, 1.6))


def test_precipitation_covers_only_the_local_day():
    w = weather.summarize(RECORDS, DAY)
    assert (w.precip_mm, w.precip_prob, w.kind) == (2.4, 80, "Regen")


def test_summary_without_the_hours_is_none():
    assert weather.summarize([rec("2026-11-10T03:00:00+01:00", 1.0)], DAY) is None


def test_degrees_are_rounded_to_whole_numbers():
    assert [weather.degrees(v) for v in (-0.4, 11.5, 7.2, None)] == ["0°", "12°", "7°", "–"]


def test_missing_hour_gives_a_dash():
    only = [r for r in RECORDS if "T12:00" not in r["timestamp"]]
    assert weather.summarize(only, DAY).temps[1] == (12, None)


def test_rain_parts_with_probability_and_litres():
    w = weather.Weather(((8, 2.0),), 10.4, 21)
    assert weather.precip_parts(w) == ("Regen", "21%  ·  10 l/m²")


def test_dry_day_has_no_symbol_and_says_no_rain():
    assert weather.precip_parts(weather.Weather(((8, 2.0),), 0.04, 9)) == (None, "Kein Regen")


def test_small_amount_and_missing_probability():
    assert weather.precip_parts(weather.Weather(((8, 2.0),), 0.4, None)) == ("Regen", "unter 1 l/m²")


def test_word_follows_the_condition_of_the_wet_hours():
    snow = [rec("2026-11-10T08:00:00+01:00", -1.0, 0.8, 70, "snow")]
    mixed = snow + [rec("2026-11-10T09:00:00+01:00", 0.5, 0.5, 70, "rain")]
    end = [rec("2026-11-11T00:00:00+01:00", -2.0)]
    assert weather.summarize(snow + end, DAY).kind == "Schnee"
    assert weather.summarize(mixed + end, DAY).kind == "Schneeregen"
    w = weather.Weather(((8, -1.0),), 3.0, 70, "Schnee")
    assert weather.precip_parts(w) == ("Schnee", "70%  ·  3 l/m²")


def test_agenda_renders_with_and_without_weather():
    w = weather.Weather(((8, 2.4), (12, 5.5), (16, 4.4), (24, 1.6)), 10.4, 21)
    with_w = agenda.render_png(1200, 825, DAY, [], w)
    without = agenda.render_png(1200, 825, DAY, [])
    assert with_w and without and with_w != without
    assert with_w == agenda.render_png(1200, 825, DAY, [], w)


def test_header_with_sun_times_and_without_weather_renders():
    w = weather.Weather(((8, 2.4), (12, 5.5), (16, 4.4), (24, 1.6)), 10.4, 21)
    both = agenda.render_png(1200, 825, DAY, [], w, ("07:20", "16:31"))
    sun_only = agenda.render_png(1200, 825, DAY, [], None, ("07:20", "16:31"))
    plain = agenda.render_png(1200, 825, DAY, [])
    assert len({both, sun_only, plain}) == 3


def test_longest_date_still_gets_its_header():
    long_day = date(2026, 9, 30)  # "Mi, 30.09.2026"
    w = weather.Weather(((8, 12.4), (12, 15.5), (16, 14.4), (24, 11.6)), 10.4, 21)
    assert agenda.render_png(1200, 825, long_day, [], w, ("06:58", "19:02"))


def test_each_kind_of_precipitation_gets_its_own_symbol():
    def img(kind):
        w = weather.Weather(((8, 1.0), (12, 2.0), (16, 1.0), (24, 0.0)), 5.0, 60, kind)
        return agenda.render_png(1200, 825, DAY, [], w, ("07:20", "16:31"))
    assert len({img("Regen"), img("Schnee"), img("Schneeregen")}) == 3
