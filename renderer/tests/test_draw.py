"""Checks the properties on which the image would fail on the panel."""

from datetime import datetime, timezone

from PIL import Image

from eink_renderer.draw import BATTERY_ZONE, CLOCK_ZONE, content_box, wrap_lines
from eink_renderer.fonts import font
from eink_renderer.grays import LEVELS, PAPER
from eink_renderer.main import render_png

WIDTH, HEIGHT = 1200, 825
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)

DASHBOARD = {
    "title": "Landstimme – Bestand & Kumuliert",
    "time": {"from": "now-30d", "to": "now"},
    "panels": [
        {"id": 1, "type": "timeseries", "title": "Installations-IDs kumuliert (30 Tage)",
         "gridPos": {"x": 0, "y": 0, "w": 12, "h": 9},
         "fieldConfig": {"defaults": {"unit": "short"}}},
        {"id": 2, "type": "timeseries", "title": "New vs. existing",
         "gridPos": {"x": 12, "y": 0, "w": 12, "h": 9},
         "fieldConfig": {"defaults": {"unit": "short"}}},
        {"id": 3, "type": "stat", "title": "Wiedergaben gesamt (30 Tage)",
         "gridPos": {"x": 0, "y": 9, "w": 12, "h": 6},
         "fieldConfig": {"defaults": {"unit": "short"}}},
        {"id": 4, "type": "stat", "title": "Uniqueness-Quote (30 Tage)",
         "gridPos": {"x": 12, "y": 9, "w": 12, "h": 6},
         "fieldConfig": {"defaults": {"unit": "percent"}}},
    ],
}

T0 = 1785024000000
DAY = 86_400_000
TIMES = [T0 + i * DAY for i in range(30)]

RESULTS = {
    "P1": {"status": 200, "frames": [{
        "schema": {"fields": [{"name": "Time", "type": "time"},
                              {"name": "Installations-IDs", "type": "number"}]},
        "data": {"values": [TIMES, list(range(2, 32))]}}]},
    "P2": {"status": 200, "frames": [{
        "schema": {"fields": [{"name": "Time", "type": "time"},
                              {"name": "Total", "type": "number"},
                              {"name": "New", "type": "number"}]},
        "data": {"values": [TIMES, [i % 17 for i in range(30)],
                            [i % 11 for i in range(30)]]}}]},
    "P3": {"status": 200, "frames": [{
        "schema": {"fields": [{"name": "n", "type": "number"}]},
        "data": {"values": [[338]]}}]},
    "P4": {"status": 200, "frames": [{
        "schema": {"fields": [{"name": "n", "type": "number"}]},
        "data": {"values": [[90.8]]}}]},
}


def _render(dashboard=DASHBOARD, results=RESULTS) -> Image.Image:
    import io
    return Image.open(io.BytesIO(render_png(WIDTH, HEIGHT, dashboard, results, NOW)))


def test_the_image_has_the_panel_dimensions_and_is_grayscale():
    img = _render()
    assert img.size == (WIDTH, HEIGHT)
    assert img.mode == "L"


def test_only_the_eight_panel_grays_appear():
    # Otherwise the server would have to dither and the type would roughen.
    assert set(_render().tobytes()) <= set(LEVELS)


def test_the_clock_corner_stays_empty():
    # The firmware draws there itself (drawClockOverlay). If something is
    # already there, it paints a white box over our content.
    img = _render()
    zone = img.crop((WIDTH - CLOCK_ZONE[0], HEIGHT - CLOCK_ZONE[1], WIDTH, HEIGHT))
    assert set(zone.tobytes()) == {PAPER}


def test_the_battery_corner_stays_empty():
    img = _render()
    zone = img.crop((WIDTH - BATTERY_ZONE[0], 0, WIDTH, BATTERY_ZONE[1]))
    assert set(zone.tobytes()) == {PAPER}


def test_the_same_input_yields_the_same_bytes():
    # Otherwise every run pushes a new hash and the panel refreshes
    # on every run even when no number changed.
    assert render_png(WIDTH, HEIGHT, DASHBOARD, RESULTS, NOW) == \
           render_png(WIDTH, HEIGHT, DASHBOARD, RESULTS, NOW)


def test_a_changed_number_changes_the_image():
    other = {**RESULTS, "P3": {"status": 200, "frames": [{
        "schema": {"fields": [{"name": "n", "type": "number"}]},
        "data": {"values": [[339]]}}]}}
    assert render_png(WIDTH, HEIGHT, DASHBOARD, RESULTS, NOW) != \
           render_png(WIDTH, HEIGHT, DASHBOARD, other, NOW)


def test_an_empty_dashboard_still_produces_a_valid_image():
    img = _render({"title": "Empty", "time": {"from": "now-1d", "to": "now"},
                   "panels": []}, {})
    assert img.size == (WIDTH, HEIGHT)


def test_content_box_leaves_the_clock_room_below():
    box = content_box(WIDTH, HEIGHT)
    assert box.bottom <= HEIGHT - CLOCK_ZONE[1]


def test_long_labels_wrap_instead_of_losing_their_ending():
    lines = wrap_lines("Verschiedene POIs (30 Tage)", font(19), 240, max_lines=2)
    assert len(lines) == 2
    assert "".join(lines).replace(" ", "").endswith("Tage)")


def test_an_hour_later_yields_the_same_image_when_the_data_is_unchanged():
    # The dashboard range is now-30d and slides continuously; without the
    # axis snapping in panels.snap_range every point drifts ~0.69 px per
    # hour and the cron would force a panel refresh on every run.
    later = datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)
    assert render_png(WIDTH, HEIGHT, DASHBOARD, RESULTS, NOW) == \
           render_png(WIDTH, HEIGHT, DASHBOARD, RESULTS, later)


def test_the_next_day_does_move_the_axis():
    tomorrow = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    assert render_png(WIDTH, HEIGHT, DASHBOARD, RESULTS, NOW) != \
           render_png(WIDTH, HEIGHT, DASHBOARD, RESULTS, tomorrow)
