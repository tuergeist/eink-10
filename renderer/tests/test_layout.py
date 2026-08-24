from eink_renderer.layout import Rect, place
from eink_renderer.panels import GridPos, Panel

BOX = Rect(24, 100, 1152, 677)


def _panel(pid, x, y, w, h):
    return Panel(pid, f"P{pid}", "stat", "short", None, GridPos(x, y, w, h))


def test_two_panels_side_by_side_split_the_width():
    rects = place([_panel(1, 0, 0, 12, 9), _panel(2, 12, 0, 12, 9)], BOX, gap=14)
    left, right = rects[1], rects[2]
    assert abs(left.w - right.w) <= 1
    assert left.right < right.x            # they do not overlap
    assert right.x - left.right == 14      # exactly one gap between them


def test_the_grid_fills_the_box_without_spilling_over():
    panels = [_panel(1, 0, 0, 12, 9), _panel(2, 12, 0, 12, 9),
              _panel(3, 0, 9, 24, 6)]
    rects = place(panels, BOX, gap=14)
    assert min(r.x for r in rects.values()) >= BOX.x
    assert max(r.right for r in rects.values()) <= BOX.right
    assert max(r.bottom for r in rects.values()) <= BOX.bottom


def test_row_heights_follow_the_grid_units():
    # 9 grid units tall against 6 — the ratio has to carry through.
    rects = place([_panel(1, 0, 0, 24, 9), _panel(2, 0, 9, 24, 6)], BOX, gap=14)
    assert rects[1].h > rects[2].h
    assert abs((rects[1].h + 14) / (rects[2].h + 14) - 9 / 6) < 0.05


def test_no_panels_means_no_rectangles():
    assert place([], BOX) == {}


def test_inset_shrinks_on_all_four_sides():
    assert Rect(0, 0, 100, 50).inset(10) == Rect(10, 10, 80, 30)
