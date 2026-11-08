from datetime import date

import pytest

from eink_renderer import agenda


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    # Fake data: the real values are personal and live in the deployment.
    monkeypatch.setenv("AGENDA_ANNE_CALENDARS", "Anne privat, Anne Firma")
    monkeypatch.setenv("AGENDA_ANNE_EMAILS", "anne@example.org")
    monkeypatch.setenv("AGENDA_CHRISTOPH_EMAILS", "chris@example.org, chris@firma.example.org")

DAY = date(2026, 11, 10)  # a Tuesday

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:a@test
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T123000
DTEND;TZID=Europe/Berlin:20261110T133000
SUMMARY:Mittagessen mit Kunde
LOCATION:Restaurant am Markt
END:VEVENT
BEGIN:VEVENT
UID:b@test
DTSTAMP:20261101T000000Z
DTSTART:20261110T060000Z
DTEND:20261110T063000Z
SUMMARY:Standup (UTC)
END:VEVENT
BEGIN:VEVENT
UID:c@test
DTSTAMP:20261101T000000Z
DTSTART;VALUE=DATE:20261110
DTEND;VALUE=DATE:20261111
SUMMARY:Feiertag
END:VEVENT
BEGIN:VEVENT
UID:d@test
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261103T090000
DTEND;TZID=Europe/Berlin:20261103T093000
RRULE:FREQ=WEEKLY;BYDAY=TU
SUMMARY:Wochentermin
END:VEVENT
BEGIN:VEVENT
UID:e@test
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T150000
DTEND;TZID=Europe/Berlin:20261110T160000
STATUS:CANCELLED
SUMMARY:Abgesagt
END:VEVENT
BEGIN:VEVENT
UID:f@test
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261111T090000
DTEND;TZID=Europe/Berlin:20261111T100000
SUMMARY:Morgen
END:VEVENT
BEGIN:VEVENT
UID:g@test
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261109T200000
DTEND;TZID=Europe/Berlin:20261110T080000
SUMMARY:Nachtschicht
END:VEVENT
BEGIN:VEVENT
UID:h@test
DTSTAMP:20261101T000000Z
DTSTART;VALUE=DATE:20261109
DTEND;VALUE=DATE:20261110
SUMMARY:Gestern ganztaegig
END:VEVENT
END:VCALENDAR
"""


def test_entries_of_the_day_sorted_all_day_first():
    entries = agenda.todays_entries(ICS, DAY)
    assert [e.title for e in entries] == [
        "Feiertag", "Nachtschicht", "Standup (UTC)", "Wochentermin",
        "Mittagessen mit Kunde",
    ]


def test_cancelled_and_other_days_are_left_out():
    titles = {e.title for e in agenda.todays_entries(ICS, DAY)}
    assert "Abgesagt" not in titles
    assert "Morgen" not in titles
    assert "Gestern ganztaegig" not in titles


def test_utc_times_are_shown_in_berlin_time():
    standup = next(e for e in agenda.todays_entries(ICS, DAY) if e.title.startswith("Standup"))
    assert agenda.time_label(standup) == "07:00–07:30"


def test_time_labels():
    by_title = {e.title: e for e in agenda.todays_entries(ICS, DAY)}
    assert agenda.time_label(by_title["Feiertag"]) == "ganztägig"
    assert agenda.time_label(by_title["Nachtschicht"]) == "…–08:00"
    assert agenda.time_label(by_title["Wochentermin"]) == "09:00–09:30"


def test_date_label():
    assert agenda.date_label(DAY) == "Di, 10.11.2026"
    assert agenda.date_label(date(2026, 11, 9)) == "Mo, 09.11.2026"


def test_same_input_yields_same_bytes():
    entries = agenda.todays_entries(ICS, DAY)
    assert agenda.render_png(1200, 825, DAY, entries) == agenda.render_png(1200, 825, DAY, entries)


def test_empty_day_and_overflow_render():
    assert agenda.render_png(1200, 825, DAY, [])
    many = [agenda.Entry(None, None, f"Termin {i}", "") for i in range(30)]
    assert agenda.render_png(1200, 825, DAY, many)


def test_printable_drops_emoji_the_font_cannot_draw():
    from eink_renderer.fonts import font
    f = font(30)
    assert agenda.printable("\U0001F6E1 \U0001F371 Lunch", f) == "Lunch"
    assert agenda.printable("Focus time", f) == "Focus time"


def _timed(h1, m1, h2, m2, title="x", optional=False):
    from datetime import datetime
    return agenda.Entry(datetime(2026, 11, 10, h1, m1, tzinfo=agenda.TZ),
                        datetime(2026, 11, 10, h2, m2, tzinfo=agenda.TZ),
                        title, "", optional=optional)


def test_event_starting_an_hour_later_lies_on_top_of_the_longer_one():
    blocks = agenda.layout_blocks([_timed(9, 0, 10, 35, "a"), _timed(10, 0, 10, 40, "b"),
                                   _timed(12, 0, 13, 0, "c")], DAY)
    by = {b.entry.title: b for b in blocks}
    assert by["b"].parent is by["a"] and by["a"].parent is None
    assert by["a"].w == 1.0 and by["c"].w == 1.0
    assert by["b"].x > by["a"].x
    assert abs((by["b"].x + by["b"].w) - 1.0) < 1e-9


def test_events_starting_close_together_share_the_width():
    blocks = agenda.layout_blocks([_timed(9, 0, 10, 0, "a"), _timed(9, 30, 10, 30, "b")], DAY)
    by = {b.entry.title: b for b in blocks}
    assert by["a"].parent is None and by["b"].parent is None
    assert (by["a"].w, by["b"].w) == (0.5, 0.5)
    assert by["a"].x != by["b"].x


def test_short_events_are_shown_at_least_one_slot_long():
    (b,) = agenda.layout_blocks([_timed(10, 40, 10, 55)], DAY)
    assert b.end - b.start == agenda.SLOT


def test_grid_range_is_8_to_18_and_widens_for_early_and_late_events():
    assert agenda.grid_range(agenda.layout_blocks([_timed(9, 0, 10, 0)], DAY)) == (480, 1080)
    wide = agenda.layout_blocks([_timed(5, 30, 6, 0), _timed(21, 0, 22, 15)], DAY)
    assert agenda.grid_range(wide) == (300, 1380)
    only_late = agenda.layout_blocks([_timed(17, 30, 19, 10)], DAY)
    assert agenda.grid_range(only_late) == (480, 1200)


def test_free_events_are_optional():
    ics = ICS.replace(b"SUMMARY:Wochentermin", b"TRANSP:TRANSPARENT\nSUMMARY:Wochentermin")
    by = {e.title: e for e in agenda.todays_entries(ics, DAY)}
    assert by["Wochentermin"].optional is True
    assert by["Mittagessen mit Kunde"].optional is False


RECLAIM_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:reclaim0focus0o020o0356@mein-kalender
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T083000
DTEND;TZID=Europe/Berlin:20261110T120000
SUMMARY:Focus time
END:VEVENT
BEGIN:VEVENT
UID:e9im6r31d5miqobj@mein-kalender
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T104000
DTEND;TZID=Europe/Berlin:20261110T105500
SUMMARY:Decompress
DESCRIPTION:<i>This event was created by <a href="https://app.reclaim.ai/landing">Reclaim.ai</a>.</i>
END:VEVENT
BEGIN:VEVENT
UID:abc@mein-kalender
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T143000
DTEND;TZID=Europe/Berlin:20261110T150000
SUMMARY:30 Min. mit Kunde
DESCRIPTION:Gebucht ueber einen Link
END:VEVENT
END:VCALENDAR
"""


def test_reclaim_events_are_optional_but_booked_meetings_are_not():
    by = {e.title: e for e in agenda.todays_entries(RECLAIM_ICS, DAY)}
    assert by["Focus time"].optional is True
    assert by["Decompress"].optional is True
    assert by["30 Min. mit Kunde"].optional is False


def test_many_overlapping_and_early_late_events_render():
    many = [_timed(9, 0, 11, 0, f"t{i}") for i in range(8)] + [_timed(5, 0, 6, 0), _timed(22, 0, 23, 30)]
    assert agenda.render_png(1200, 825, DAY, many)


OWNER_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:1@t
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T090000
DTEND;TZID=Europe/Berlin:20261110T100000
SUMMARY:privat
X-MEINKALENDER-CALENDAR:Anne privat
END:VEVENT
BEGIN:VEVENT
UID:2@t
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T090000
DTEND;TZID=Europe/Berlin:20261110T100000
SUMMARY:pdr
X-MEINKALENDER-CALENDAR:Anne Firma
END:VEVENT
BEGIN:VEVENT
UID:3@t
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T090000
DTEND;TZID=Europe/Berlin:20261110T100000
SUMMARY:firma
X-MEINKALENDER-CALENDAR:chris@firma.example.org
END:VEVENT
BEGIN:VEVENT
UID:4@t
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T090000
DTEND;TZID=Europe/Berlin:20261110T100000
SUMMARY:ohne
END:VEVENT
END:VCALENDAR
"""


def test_owner_anne_for_two_calendars_christoph_for_everything_else():
    by = {e.title: e.owner for e in agenda.todays_entries(OWNER_ICS, DAY)}
    assert by == {"privat": "anne", "pdr": "anne", "firma": "christoph", "ohne": "christoph"}


def test_events_of_different_owners_do_not_share_a_column():
    entries = agenda.todays_entries(OWNER_ICS, DAY)
    assert agenda.render_png(1200, 825, DAY, entries)


# --- split view (today's rest above, tomorrow condensed below) ---------------

from datetime import datetime  # noqa: E402

NEXT = date(2026, 11, 11)


def _at(h, m=0):
    return datetime(2026, 11, 10, h, m, tzinfo=agenda.TZ)


def _owners(blocks):
    return {"christoph": blocks, "anne": []}


def test_no_split_before_13_oclock():
    tomorrow = (NEXT, [agenda.Entry(None, None, "Morgen", "")])
    entries = [_timed(9, 0, 10, 0, "a")]
    plain = agenda.render_png(1200, 825, DAY, entries)
    assert agenda.render_png(1200, 825, DAY, entries, now=_at(12, 59), tomorrow=tomorrow) == plain
    assert agenda.render_png(1200, 825, DAY, entries, now=_at(17), tomorrow=tomorrow) != plain


def test_upper_part_wanders_with_the_hour_and_covers_three_hours():
    p = agenda.plan_split(_at(18, 20), _owners([]), _owners([]), 176, 777)
    assert p.upper == (18 * 60, 21 * 60)
    p = agenda.plan_split(_at(16, 5), _owners([]), _owners([]), 176, 777)
    assert p.upper == (16 * 60, 19 * 60)


def test_upper_part_grows_while_events_run_on():
    today = _owners(agenda.layout_blocks([_timed(17, 0, 21, 30)], DAY))
    p = agenda.plan_split(_at(16, 10), today, _owners([]), 176, 777)
    assert p.upper == (16 * 60, 22 * 60)


def test_no_split_when_the_lower_part_would_get_less_than_150_pixels():
    today = _owners(agenda.layout_blocks([_timed(17, 0, 23, 59)], DAY))
    assert agenda.plan_split(_at(16), today, _owners([]), 176, 777) is None


def test_lower_range_starts_at_8_and_reaches_earlier_for_early_events():
    p = agenda.plan_split(_at(17), _owners([]), _owners([]), 176, 777)
    assert p.lower == (8 * 60, 18 * 60)
    early = _owners(agenda.layout_blocks([_timed(6, 30, 7, 30)], DAY))
    p = agenda.plan_split(_at(17), _owners([]), early, 176, 777)
    assert p.lower[0] == 6 * 60


def test_split_view_with_an_empty_tomorrow_renders():
    assert agenda.render_png(1200, 825, DAY, [_timed(16, 0, 17, 0)], now=_at(16, 30),
                             tomorrow=(NEXT, []))


# --- organizer, answer to the invitation, free/busy from the feed ------------

def _one(body: str) -> bytes:
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\nBEGIN:VEVENT\nUID:x@t\n"
        "DTSTAMP:20261101T000000Z\nDTSTART;VALUE=DATE:20261110\nDTEND;VALUE=DATE:20261111\n"
        "SUMMARY:Test\nX-MEINKALENDER-CALENDAR:chris@example.org\n" + body +
        "END:VEVENT\nEND:VCALENDAR\n"
    ).encode()


def _entry(body: str):
    entries = agenda.todays_entries(_one(body), DAY)
    return entries[0] if entries else None


def test_event_organised_by_anne_goes_to_annes_column_even_in_christophs_calendar():
    e = _entry("ORGANIZER;CN=Anne:mailto:anne@example.org\n")
    assert e.owner == "anne"
    assert _entry("ORGANIZER:mailto:someone@else.de\n").owner == "christoph"
    assert _entry("").owner == "christoph"


def test_unanswered_and_tentative_invitations_look_optional():
    def mine(partstat):
        return _entry(f"ATTENDEE;PARTSTAT={partstat}:mailto:chris@example.org\n")
    assert mine("NEEDS-ACTION").optional is True
    assert mine("TENTATIVE").optional is True
    assert mine("ACCEPTED").optional is False


def test_declined_invitation_is_left_out():
    assert _entry("ATTENDEE;PARTSTAT=DECLINED:mailto:chris@example.org\n") is None


def test_only_christophs_answer_counts_not_annes():
    body = ("ATTENDEE;PARTSTAT=ACCEPTED:mailto:anne@example.org\n"
            "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:chris@example.org\n")
    assert _entry(body).optional is True
    body = ("ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:anne@example.org\n"
            "ATTENDEE;PARTSTAT=ACCEPTED:mailto:chris@example.org\n")
    assert _entry(body).optional is False


def test_freiburg_case_organiser_anne_not_answered_and_free():
    e = _entry("ORGANIZER:mailto:anne@example.org\n"
               "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:chris@example.org\n"
               "ATTENDEE;PARTSTAT=ACCEPTED:mailto:anne@example.org\nTRANSP:TRANSPARENT\n")
    assert (e.owner, e.optional, e.start) == ("anne", True, None)


def test_optional_all_day_event_is_drawn_differently():
    solid = agenda.Entry(None, None, "Freiburg", "", optional=False)
    dashed = agenda.Entry(None, None, "Freiburg", "", optional=True)
    assert agenda.render_png(1200, 825, DAY, [solid]) != agenda.render_png(1200, 825, DAY, [dashed])


# --- "[Sync]" copies of a meeting -------------------------------------------------

def _meeting(title, h1=14, m1=30, h2=15, m2=0):
    return _timed(h1, m1, h2, m2, title)


def test_sync_copy_is_dropped_when_the_original_exists():
    kept = agenda.drop_sync_twins([_meeting("30\xa0Min. mit Kunde (Max)"),
                                   _meeting("[Sync] 30 Min. mit Kunde (Max)")])
    assert [e.title for e in kept] == ["30\xa0Min. mit Kunde (Max)"]


def test_sync_event_without_twin_or_at_another_time_stays():
    sync = _meeting("[Sync] Test")
    assert agenda.drop_sync_twins([sync]) == [sync]
    other_time = [_meeting("Test", 9, 0, 10, 0), sync]
    assert agenda.drop_sync_twins(other_time) == other_time
    other_title = [_meeting("Etwas anderes"), sync]
    assert agenda.drop_sync_twins(other_title) == other_title


def test_twins_are_dropped_from_the_days_entries():
    body = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//t//EN
BEGIN:VEVENT
UID:1@t
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T143000
DTEND;TZID=Europe/Berlin:20261110T150000
SUMMARY:Meeting
END:VEVENT
BEGIN:VEVENT
UID:2@t
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Berlin:20261110T143000
DTEND;TZID=Europe/Berlin:20261110T150000
SUMMARY:[Sync] Meeting
END:VEVENT
END:VCALENDAR
"""
    assert [e.title for e in agenda.todays_entries(body, DAY)] == ["Meeting"]


def test_every_style_renders():
    entries = [_timed(9, 0, 10, 35, "a"), _timed(10, 0, 10, 40, "b"),
               _timed(12, 0, 13, 0, "c", optional=True),
               agenda.Entry(None, None, "Ganztags", "", optional=False),
               agenda.Entry(None, None, "Vielleicht", "", optional=True)]
    images = set()
    for style in ("bar", "bracket", "tint", "classic"):
        old = agenda.BLOCK_STYLE
        agenda.BLOCK_STYLE = style
        try:
            images.add(agenda.render_png(1200, 825, DAY, entries))
        finally:
            agenda.BLOCK_STYLE = old
    assert len(images) == 4


# --- nothing left today -------------------------------------------------------

def test_nothing_left_today_gives_a_thin_band_and_more_room_for_tomorrow():
    gone = _owners(agenda.layout_blocks([_timed(16, 0, 17, 0)], DAY))      # over before 18:00
    p = agenda.plan_split(_at(18, 20), gone, _owners([]), 176, 777)
    assert p.upper_empty is True
    busy = agenda.plan_split(_at(18, 20), _owners(agenda.layout_blocks([_timed(17, 30, 18, 30)], DAY)),
                             _owners([]), 176, 777)
    assert busy.upper_empty is False
    assert p.lower_top < busy.lower_top        # the band is thinner than three hours


def test_band_text_is_drawn_when_nothing_is_left():
    tomorrow = (NEXT, [agenda.Entry(None, None, "Morgen", "")])
    with_event = agenda.render_png(1200, 825, DAY, [_timed(18, 0, 19, 0)], now=_at(18, 20),
                                   tomorrow=tomorrow)
    gone = agenda.render_png(1200, 825, DAY, [_timed(16, 0, 17, 0)], now=_at(18, 20),
                             tomorrow=tomorrow)
    assert with_event != gone


# --- tomorrow fills the room that is there -------------------------------------

def test_tomorrow_fills_the_available_height():
    busy = _owners(agenda.layout_blocks([_timed(17, 30, 18, 30)], DAY))
    for today, bottom in ((busy, 777), (_owners([]), 777)):
        p = agenda.plan_split(_at(18, 20), today, _owners([]), 176, bottom)
        rows = (p.lower[1] - p.lower[0]) // agenda.SLOT
        used = rows * p.lower_row_h
        assert p.lower_top + used <= bottom
        assert bottom - (p.lower_top + used) < rows        # less than a pixel per row left over


def test_more_room_means_taller_rows_and_the_empty_band_gives_the_most():
    busy = _owners(agenda.layout_blocks([_timed(17, 30, 18, 30)], DAY))
    with_events = agenda.plan_split(_at(18, 20), busy, _owners([]), 176, 777)
    nothing_left = agenda.plan_split(_at(18, 20), _owners([]), _owners([]), 176, 777)
    assert agenda.ROW_H_COMPACT <= with_events.lower_row_h < nothing_left.lower_row_h <= agenda.ROW_H
    assert nothing_left.lower_row_h >= agenda.COMPACT_BELOW      # titles with times fit again


def test_event_font_in_tomorrows_rows_is_never_below_the_date_labels_size():
    for row_h in range(agenda.ROW_H_COMPACT, agenda.ROW_H + 1):
        assert agenda.compact_font_size(row_h) >= agenda.MIN_EVENT_FONT
    assert agenda.compact_font_size(agenda.ROW_H_COMPACT) == agenda.MIN_EVENT_FONT
    assert agenda.compact_font_size(agenda.COMPACT_BELOW - 1) > agenda.MIN_EVENT_FONT


def test_with_little_room_tomorrow_shows_fewer_hours_instead_of_smaller_rows():
    busy = _owners(agenda.layout_blocks([_timed(17, 30, 18, 30)], DAY))
    p = agenda.plan_split(_at(18, 20), busy, _owners([]), 176, 777)
    assert p.lower_row_h >= agenda.ROW_H_COMPACT
    assert p.lower[1] - p.lower[0] <= (777 - p.lower_top) // agenda.ROW_H_COMPACT * agenda.SLOT


def test_too_long_a_day_drops_empty_hours_before_cutting_into_events():
    # events 09:00-10:00 only; the default range 08-18 is longer than the room allows
    tomorrow = _owners(agenda.layout_blocks([_timed(9, 0, 10, 0)], DAY))
    p = agenda.plan_split(_at(18, 20), _owners(agenda.layout_blocks([_timed(17, 30, 18, 30)], DAY)),
                          tomorrow, 176, 777)
    lo, hi = p.lower
    assert lo <= 9 * 60 and hi >= 10 * 60            # the event is inside
    assert (hi - lo) // agenda.SLOT * p.lower_row_h <= 777 - p.lower_top


def test_late_event_that_does_not_fit_is_kept_by_moving_the_window_not_cut():
    # events 09:00-10:00 and 16:30-17:30: the default 08-18 is ten hours, only nine fit;
    # the empty first hour goes, the late event stays
    tomorrow = _owners(agenda.layout_blocks([_timed(9, 0, 10, 0), _timed(16, 30, 17, 30)], DAY))
    p = agenda.plan_split(_at(18, 20), _owners(agenda.layout_blocks([_timed(17, 30, 18, 30)], DAY)),
                          tomorrow, 176, 777)
    lo, hi = p.lower
    assert (lo, hi) == (9 * 60, 18 * 60)


def test_hint_for_events_that_still_do_not_fit_renders():
    entries = [_timed(9, 0, 10, 0), _timed(17, 30, 18, 20)]
    tomorrow = (NEXT, [agenda.Entry(e.start.replace(day=11), e.end.replace(day=11), e.title, "")
                       for e in entries] + [agenda.Entry(None, None, "x", "")])
    assert agenda.render_png(1200, 825, DAY, [_timed(17, 30, 18, 30)], now=_at(16, 30), tomorrow=tomorrow)
