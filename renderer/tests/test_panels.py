from datetime import datetime, timezone

import pytest

from eink_renderer.panels import (
    KIND_STAT, KIND_TIMESERIES, KIND_UNSUPPORTED,
    build_panel, build_panels, parse_relative, time_range_millis,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _frame(fields, columns):
    return {"schema": {"fields": fields}, "data": {"values": columns}}


TIME_FRAME = _frame(
    [{"name": "Time", "type": "time"}, {"name": "Reihe", "type": "number"}],
    [[1785024000000, 1785110400000], [2, 5]],
)


def test_relative_ranges_are_understood():
    assert parse_relative("now", NOW) == NOW
    assert (NOW - parse_relative("now-30d", NOW)).days == 30
    assert (NOW - parse_relative("now-6h", NOW)).seconds == 6 * 3600


def test_an_unparseable_range_raises_instead_of_guessing():
    with pytest.raises(ValueError):
        parse_relative("now-1M/M", NOW)


def test_time_range_comes_from_the_dashboard():
    a, b = time_range_millis({"time": {"from": "now-30d", "to": "now"}}, NOW)
    assert round((b - a) / 86_400_000) == 30


def test_stat_panel_reduces_to_the_last_non_null_value():
    panel = build_panel(
        {"id": 3, "type": "stat", "title": "Plays",
         "gridPos": {"x": 0, "y": 9, "w": 6, "h": 6},
         "fieldConfig": {"defaults": {"unit": "short"}}},
        [_frame([{"name": "n", "type": "number"}], [[None, 338]])],
    )
    assert panel.kind == KIND_STAT
    assert panel.value == 338
    assert panel.unit == "short"


def test_timeseries_panel_keeps_every_numeric_column_as_its_own_series():
    panel = build_panel(
        {"id": 7, "type": "timeseries", "title": "Trend",
         "gridPos": {"x": 0, "y": 0, "w": 24, "h": 9}},
        [_frame(
            [{"name": "Time", "type": "time"},
             {"name": "A", "type": "number"},
             {"name": "B", "type": "number"}],
            [[1, 2], [10, 20], [1, 2]],
        )],
    )
    assert panel.kind == KIND_TIMESERIES
    assert [s.name for s in panel.series] == ["A", "B"]
    assert panel.series[0].times == (1, 2)


def test_a_timeseries_without_a_time_column_is_marked_unsupported():
    panel = build_panel(
        {"id": 8, "type": "timeseries", "title": "No time column",
         "gridPos": {"x": 0, "y": 0, "w": 24, "h": 9}},
        [_frame([{"name": "n", "type": "number"}], [[1]])],
    )
    assert panel.kind == KIND_UNSUPPORTED
    assert panel.note


def test_unknown_panel_types_become_a_visible_placeholder():
    panel = build_panel(
        {"id": 9, "type": "geomap", "title": "Map",
         "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}}, [])
    assert panel.kind == KIND_UNSUPPORTED
    assert "geomap" in panel.note


def test_a_failed_query_does_not_take_the_whole_image_down():
    panels = build_panels(
        {"panels": [
            {"id": 1, "type": "timeseries", "title": "Broken",
             "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}},
            {"id": 2, "type": "stat", "title": "Fine",
             "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8}},
        ]},
        {"P1": {"status": 500, "error": "db gone"},
         "P2": {"status": 200, "frames": [
             _frame([{"name": "n", "type": "number"}], [[7]])]}},
    )
    by_id = {p.id: p for p in panels}
    assert by_id[1].kind == KIND_UNSUPPORTED and "db gone" in by_id[1].note
    assert by_id[2].value == 7


def test_row_panels_are_skipped():
    panels = build_panels({"panels": [{"id": 1, "type": "row", "title": "Row"}]}, {})
    assert panels == []


def test_panels_come_back_in_reading_order():
    panels = build_panels(
        {"panels": [
            {"id": 2, "type": "stat", "gridPos": {"x": 12, "y": 0, "w": 12, "h": 4}},
            {"id": 3, "type": "stat", "gridPos": {"x": 0, "y": 4, "w": 24, "h": 4}},
            {"id": 1, "type": "stat", "gridPos": {"x": 0, "y": 0, "w": 12, "h": 4}},
        ]},
        {},
    )
    assert [p.id for p in panels] == [1, 2, 3]


def test_a_thirty_day_range_snaps_to_whole_days():
    from eink_renderer.panels import DAY_MS, snap_range
    t_to = 1787571234567          # some point mid-day
    a, b = snap_range(t_to - 30 * DAY_MS, t_to)
    assert b % DAY_MS == 0
    assert (b - a) == 30 * DAY_MS


def test_snapping_holds_the_axis_still_for_a_whole_day():
    # This is the property that keeps the cron from burning a panel
    # refresh: same day in, same axis out.
    from eink_renderer.panels import DAY_MS, HOUR_MS, snap_range
    base = 1787571234567
    first = snap_range(base - 30 * DAY_MS, base)
    for hours in range(1, 24):
        t = base + hours * HOUR_MS
        if (t // DAY_MS) != (base // DAY_MS):
            break
        assert snap_range(t - 30 * DAY_MS, t) == first


def test_a_short_range_is_not_collapsed_onto_days():
    from eink_renderer.panels import HOUR_MS, snap_range
    t_to = 1787571234567
    a, b = snap_range(t_to - 6 * HOUR_MS, t_to)
    assert (b - a) == 6 * HOUR_MS
    assert b % HOUR_MS == 0
