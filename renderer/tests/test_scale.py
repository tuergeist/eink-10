import math

from eink_renderer.scale import nice_step, nice_ticks, project


def test_step_is_one_two_or_five_times_a_power_of_ten():
    for rough in (0.3, 3, 7, 23, 260, 4321):
        step = nice_step(rough)
        mantissa = step / 10 ** math.floor(math.log10(step))
        assert round(mantissa, 6) in (1.0, 2.0, 5.0)
        assert step >= rough


def test_bounds_enclose_the_data_and_sit_on_tick_multiples():
    lo, hi, ticks = nice_ticks(0, 36)
    assert lo <= 0 and hi >= 36
    assert ticks[0] == lo and ticks[-1] == hi
    step = ticks[1] - ticks[0]
    assert all(math.isclose(t % step, 0, abs_tol=1e-9) or
               math.isclose(t % step, step, abs_tol=1e-9) for t in ticks)


def test_a_constant_series_still_gets_a_height():
    lo, hi, ticks = nice_ticks(5, 5)
    assert hi > lo
    assert len(ticks) >= 2


def test_last_tick_hits_the_top_exactly():
    # Repeated addition instead of multiplication would miss here.
    lo, hi, ticks = nice_ticks(0, 0.7)
    assert math.isclose(ticks[-1], hi, rel_tol=0, abs_tol=1e-9)


def test_projection_maps_ends_to_ends_and_is_monotone():
    assert project(0, 0, 10, 100, 0) == 100
    assert project(10, 0, 10, 100, 0) == 0
    assert project(5, 0, 10, 100, 0) == 50
