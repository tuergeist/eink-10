from eink_renderer.grays import LEVELS, level_lut, nearest_level


def test_levels_match_the_panel_spec():
    # From docs/renderer-spec.md — 3 bit, evenly spread over 0..255.
    assert LEVELS == (0, 36, 73, 109, 146, 182, 219, 255)


def test_nearest_level_snaps_to_the_closer_neighbour():
    assert nearest_level(0) == 0
    assert nearest_level(255) == 255
    assert nearest_level(17) == 0      # closer to 0 than to 36
    assert nearest_level(19) == 36     # the midpoint of 0 and 36 is 18.2
    assert nearest_level(200) == 182   # the midpoint of 182 and 219 is 200.5
    assert nearest_level(201) == 219


def test_nearest_level_rejects_values_outside_a_byte():
    for bad in (-1, 256):
        try:
            nearest_level(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} should have been rejected")


def test_lut_has_one_entry_per_byte_value_and_only_uses_levels():
    lut = level_lut()
    assert len(lut) == 256
    assert set(lut) == set(LEVELS)
