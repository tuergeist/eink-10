"""Display strings stay German — the dashboard and its readers are."""

from eink_renderer import fmt


def test_thousands_are_grouped_with_dots():
    assert fmt.de_group("1") == "1"
    assert fmt.de_group("1234") == "1.234"
    assert fmt.de_group("1234567") == "1.234.567"
    assert fmt.de_group("-1234") == "-1.234"


def test_whole_numbers_lose_the_decimal_part():
    assert fmt.format_value(338) == "338"
    assert fmt.format_value(1284) == "1.284"


def test_percent_gets_one_decimal_and_a_comma():
    assert fmt.format_value(90.8, "percent") == "90,8 %"
    assert fmt.format_value(0.907, "percentunit") == "90,7 %"


def test_missing_value_shows_a_dash_not_a_zero():
    # Drawing a missing number as 0 would be a claim.
    assert fmt.format_value(None) == "–"


def test_millions_are_shortened_so_the_tile_stays_readable():
    assert fmt.format_value(2_400_000) == "2,4 Mio."


def test_explicit_decimals_win():
    assert fmt.format_value(12.345, "short", 2) == "12,35"


def test_axis_labels_are_shorter_than_tile_values():
    assert fmt.format_axis_value(10_000) == "10k"
    assert fmt.format_axis_value(40) == "40"
    assert fmt.format_axis_value(2_000_000) == "2 M"


def test_range_is_rendered_in_berlin_time():
    # 2026-07-26 00:00 UTC to 2026-08-24 00:00 UTC
    a, b = 1785024000000, 1787529600000
    assert fmt.format_range(a, b) == "26.07. – 24.08.2026"
