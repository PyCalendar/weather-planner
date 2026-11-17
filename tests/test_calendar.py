from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from calendaring import Backend, Component, SyncEvent, SyncTask, Workspace

from weather_planner.alerts import Alert
from weather_planner.calendar import CATEGORY, Calendars, open_calendars, sync
from weather_planner.config import parse_alert
from weather_planner.places import Place

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
H = timedelta(hours=1)
LAZUR = Place("lazur", (42.69, 27.72), (42.69, 27.72))
GALATA = Place("galata", (43.19, 27.93), (43.19, 27.93))


def both(collection: Any) -> Calendars:
    """One calendar that takes events as well as tasks."""
    return Calendars(events=collection, tasks=collection)


@pytest.fixture
def collection(tmp_path: Path) -> Any:
    backend = Backend.open(str(tmp_path))
    with Workspace([backend]):
        yield backend.create_collection("weather", components={Component.EVENT, Component.TASK})


def alert(start: datetime = NOW + 6 * H, hours: int = 3, name: str = "gale", **rule: Any) -> Alert:
    data = {"gust": 20, "task": "secure the deck", "lead": "3h", **rule}
    return Alert(parse_alert(name, data), start, start + hours * H, {"gust": 25.0}, 180.0)


def events(collection: Any) -> list[SyncEvent]:
    return list(collection.events())


def tasks(collection: Any) -> list[SyncTask]:
    return list(collection.tasks(include_completed=True))


def test_creates_transparent_event_and_task(collection: Any) -> None:
    report = sync(both(collection), LAZUR, [alert()], NOW)
    assert len(report.created) == 2
    [event] = events(collection)
    c = event.component
    assert c["TRANSP"] == "TRANSPARENT"
    assert c.decoded("DTSTART") == NOW + 6 * H
    assert c.decoded("DTEND") == NOW + 9 * H
    assert "gusts to 25 m/s from S" in str(c["SUMMARY"])
    assert CATEGORY in [str(x) for x in c.categories]
    [task] = tasks(collection)
    t = task.component
    assert str(t["SUMMARY"]) == "secure the deck"
    assert t.decoded("DUE") == NOW + 3 * H
    assert "gusts to 25 m/s" in str(t["DESCRIPTION"])


def test_second_run_changes_nothing(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    report = sync(both(collection), LAZUR, [alert()], NOW)
    assert (report.created, report.updated, report.deleted) == ([], [], [])


def test_moved_alert_updates_in_place(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    [before] = events(collection)
    report = sync(both(collection), LAZUR, [alert(start=NOW + 7 * H, hours=4)], NOW)
    assert report.created == []
    assert len(report.updated) == 2
    [after] = events(collection)
    assert after.uid == before.uid
    assert after.component.decoded("DTEND") == NOW + 11 * H
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW + 4 * H


def test_vanished_alert_deletes_event_and_open_task_but_not_completed_task(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert(), alert(start=NOW + 20 * H)], NOW)
    done = next(t for t in tasks(collection) if t.component.decoded("DUE") == NOW + 3 * H)
    done.complete()
    report = sync(both(collection), LAZUR, [], NOW)
    assert len(report.deleted) == 3  # two events and the open task
    assert events(collection) == []
    [kept] = tasks(collection)
    assert kept.uid == done.uid


def test_completed_task_is_neither_recreated_nor_changed(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    [task] = tasks(collection)
    task.complete()
    report = sync(both(collection), LAZUR, [alert(start=NOW + 5 * H)], NOW)
    assert [t.component.decoded("DUE") for t in tasks(collection)] == [NOW + 3 * H]
    assert all("secure" not in s for s in report.created + report.updated)


def test_configured_properties_are_set_when_created(collection: Any) -> None:
    sync(
        both(collection),
        LAZUR,
        [alert(task={"summary": "secure the deck", "priority": 3}, event={"class": "PRIVATE"})],
        NOW,
    )
    [task] = tasks(collection)
    assert task.component.decoded("PRIORITY") == 3
    [event] = events(collection)
    assert str(event.component["CLASS"]) == "PRIVATE"


def test_configured_properties_survive_an_update(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert(task={"summary": "secure the deck", "priority": 3})], NOW)
    report = sync(
        both(collection), LAZUR, [alert(start=NOW + 7 * H, task={"summary": "secure the deck", "priority": 3})], NOW
    )
    assert report.created == []
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW + 4 * H
    assert task.component.decoded("PRIORITY") == 3


def test_a_priority_changed_by_the_user_is_kept(collection: Any) -> None:
    configured = alert(task={"summary": "secure the deck", "priority": 3})
    sync(both(collection), LAZUR, [configured], NOW)
    [task] = tasks(collection)
    task.component.pop("PRIORITY")
    task.component.add("PRIORITY", 1)
    task.save()
    sync(both(collection), LAZUR, [alert(start=NOW + 7 * H, task={"summary": "secure the deck", "priority": 3})], NOW)
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW + 4 * H  # it was updated, but
    assert task.component.decoded("PRIORITY") == 1  # not its priority


OSLO = ZoneInfo("Europe/Oslo")


@pytest.mark.parametrize(
    ("start", "moved", "tzid"),
    [
        (NOW + 1 * H, NOW + 3 * H, None),
        ((NOW + 1 * H).astimezone(OSLO), NOW + 3 * H, "Europe/Oslo"),  # keeps its TZID
        # across the start of summer time: 01:30 CET, two hours on, is 04:30 CEST
        (datetime(2026, 3, 29, 1, 30, tzinfo=OSLO), datetime(2026, 3, 29, 4, 30, tzinfo=OSLO), "Europe/Oslo"),
        (datetime(2026, 11, 13, 13), datetime(2026, 11, 13, 15), None),  # floating stays floating
        (date(2026, 11, 13), date(2026, 11, 13), None),  # a date is left alone
    ],
)
def test_dtstart_moves_along_with_the_deadline(collection: Any, start: date, moved: date, tzid: str | None) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    [task] = tasks(collection)
    task.component.add("DTSTART", start)  # the user, or plann, planned when to start
    task.save()
    sync(both(collection), LAZUR, [alert(start=NOW + 8 * H)], NOW)
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW + 5 * H
    assert task.component.decoded("DTSTART") == moved  # a floating one only equals a floating one
    assert task.component["DTSTART"].params.get("TZID") == tzid
    report = sync(both(collection), LAZUR, [alert(start=NOW + 8 * H)], NOW)
    assert report.updated == []


def test_dtstart_stays_when_the_user_moved_the_deadline(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    [task] = tasks(collection)
    task.component.DUE = NOW + 1 * H
    task.component.add("DTSTART", NOW)
    task.save()
    sync(both(collection), LAZUR, [alert(start=NOW + 8 * H)], NOW)
    [task] = tasks(collection)
    assert task.component.decoded("DTSTART") == NOW


def test_deadline_moved_by_the_user_is_kept(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    [task] = tasks(collection)
    task.component.DUE = NOW + 1 * H  # the user, or plann, moved it
    task.save()
    sync(both(collection), LAZUR, [alert(start=NOW + 8 * H)], NOW)
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW + 1 * H


def test_leaves_past_items_other_places_and_foreign_items_alone(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert(start=NOW - 2 * H)], NOW - 3 * H)
    sync(both(collection), GALATA, [alert()], NOW)
    collection.add_event(summary="dinner", start=NOW + 6 * H, end=NOW + 7 * H)
    report = sync(both(collection), LAZUR, [], NOW)
    assert report.deleted == []
    assert len(events(collection)) == 3


def test_event_false_and_no_task(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert(event=False), alert(name="cape", task=None)], NOW)
    assert len(events(collection)) == 1
    assert len(tasks(collection)) == 1


def _config(tmp_path: Path, *collections: tuple[str, set[Component]]) -> Path:
    path = tmp_path / "calendar.conf"
    (tmp_path / "cal").mkdir()
    path.write_text(f'{{"default": {{"url": "{tmp_path / "cal"}"}}}}')
    with Workspace.from_config(path) as ws:
        for name, components in collections:
            ws.backends[0].create_collection(name, components=components)
    return path


EVENTS = {Component.EVENT}
TASKS = {Component.TASK}


def test_open_calendars_takes_the_first_one_that_supports_each_kind(tmp_path: Path) -> None:
    path = _config(tmp_path, ("a", TASKS), ("b", EVENTS | TASKS), ("c", EVENTS))
    with open_calendars(None, path) as cals:
        assert (cals.events.name, cals.tasks.name) == ("b", "a")


def test_open_calendars_needs_a_calendar_for_each_kind(tmp_path: Path) -> None:
    path = _config(tmp_path, ("a", EVENTS))
    with pytest.raises(ValueError, match="no calendar takes tasks"), open_calendars(None, path):
        pass


def test_events_and_tasks_can_go_to_different_calendars(tmp_path: Path) -> None:
    path = _config(tmp_path, ("e", EVENTS), ("t", TASKS))
    with open_calendars(None, path) as cals:
        sync(cals, LAZUR, [alert()], NOW)
        assert len(events(cals.events)) == 1
        assert len(tasks(cals.tasks)) == 1
        assert sync(cals, LAZUR, [alert()], NOW).updated == []
        assert len(sync(cals, LAZUR, [], NOW).deleted) == 2


def test_items_carry_the_places_name_and_position(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    for item in (*events(collection), *tasks(collection)):
        c = item.component
        assert str(c["LOCATION"]) == "lazur"
        assert c.decoded("GEO") == LAZUR.position
        assert str(c["X-WEATHER-PLANNER"]) == "gale"  # the rule only; the place is the LOCATION


def test_a_place_with_the_same_name_far_away_is_another_place(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    elsewhere = Place("lazur", (60.39, 5.32), (60.39, 5.32))
    report = sync(both(collection), elsewhere, [], NOW)
    assert report.deleted == []  # the items at lazur are not ours to delete from there
    assert len(events(collection)) == 1


def test_a_position_a_little_off_is_still_the_place(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    moved = Place("lazur", (LAZUR.position[0] + 0.01, LAZUR.position[1]), LAZUR.position)  # ~1 km
    report = sync(both(collection), moved, [alert()], NOW)
    assert report.created == []
    assert len(report.updated) == 2  # GEO follows the configured position
    assert len(events(collection)) == 1


def test_items_written_with_the_place_in_the_key_are_found_and_migrated(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    for item in (*events(collection), *tasks(collection)):  # make them look like the old format
        c = item.component
        for name in ("LOCATION", "GEO"):
            c.pop(name)
        c["X-WEATHER-PLANNER"] = "lazur/gale"
        item.save()
    report = sync(both(collection), LAZUR, [alert()], NOW)
    assert report.created == []
    assert len(report.updated) == 2
    assert all(str(i.component["X-WEATHER-PLANNER"]) == "gale" for i in events(collection))
    assert sync(both(collection), LAZUR, [alert()], NOW).updated == []


def test_hourly_runs_during_an_alert_keep_its_start_and_deadline(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert(start=NOW + 3 * H, hours=6)], NOW)
    for hour in range(4, 8):  # the alert has begun; each run sees it from the current window on
        report = sync(both(collection), LAZUR, [alert(start=NOW + hour * H, hours=9 - hour)], NOW + hour * H)
        assert report.updated == []
    [event] = events(collection)
    assert event.component.decoded("DTSTART") == NOW + 3 * H
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW


def test_due_rewritten_in_another_timezone_is_not_taken_as_moved(collection: Any) -> None:
    from zoneinfo import ZoneInfo

    sync(both(collection), LAZUR, [alert()], NOW)
    [task] = tasks(collection)
    task.component.DUE = (NOW + 3 * H).astimezone(ZoneInfo("Europe/Sofia"))  # same instant
    task.save()
    sync(both(collection), LAZUR, [alert(start=NOW + 8 * H)], NOW)
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW + 5 * H


@pytest.mark.parametrize("prop", [("COMPLETED", NOW), ("PERCENT-COMPLETE", 100)])
def test_task_closed_without_status_is_left_alone(collection: Any, prop: tuple[str, Any]) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    [task] = tasks(collection)
    task.component.add(*prop)
    task.save()
    report = sync(both(collection), LAZUR, [], NOW)
    assert report.deleted == ["event: gale: gusts to 25 m/s from S"]
    assert len(tasks(collection)) == 1


def test_items_in_another_calendar_than_the_first_are_still_ours(tmp_path: Path) -> None:
    path = _config(tmp_path, ("a", EVENTS | TASKS), ("b", EVENTS | TASKS))
    with Workspace.from_config(path) as ws:
        a, b = (ws.collection(n) for n in "ab")
        sync(Calendars(a, a, [a, b]), LAZUR, [alert()], NOW)  # written when "a" was the first
        report = sync(Calendars(b, b, [a, b]), LAZUR, [alert(start=NOW + 7 * H)], NOW)  # now "b" is
        assert report.created == []
        assert len(report.updated) == 2
        assert (len(events(a)), len(events(b)), len(tasks(b))) == (1, 0, 0)
        assert len(sync(Calendars(b, b, [a, b]), LAZUR, [], NOW).deleted) == 2


def test_a_geo_the_server_rounded_is_not_a_change(collection: Any) -> None:
    precise = Place("lazur", (42.123456789, 27.123456789), (42.0, 27.0))
    sync(both(collection), precise, [alert()], NOW)
    for item in (*events(collection), *tasks(collection)):
        item.component.pop("GEO")
        item.component.add("GEO", (42.123457, 27.123457))
        item.save()
    assert sync(both(collection), precise, [alert()], NOW).updated == []


def test_an_item_with_the_location_but_no_geo_is_at_the_place(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    for item in (*events(collection), *tasks(collection)):
        item.component.pop("GEO")
        item.save()
    report = sync(both(collection), LAZUR, [alert()], NOW)
    assert report.created == []
    assert len(report.updated) == 2


def test_old_format_items_of_another_place_are_left_alone(collection: Any) -> None:
    sync(both(collection), GALATA, [alert()], NOW)
    for item in (*events(collection), *tasks(collection)):
        for name in ("LOCATION", "GEO"):
            item.component.pop(name)
        item.component["X-WEATHER-PLANNER"] = "galata/gale"
        item.save()
    report = sync(both(collection), LAZUR, [], NOW)
    assert report.deleted == []
    assert all(str(i.component["X-WEATHER-PLANNER"]) == "galata/gale" for i in events(collection))


def test_a_closed_task_in_the_old_format_is_still_found(collection: Any) -> None:
    sync(both(collection), LAZUR, [alert()], NOW)
    [task] = tasks(collection)
    for name in ("LOCATION", "GEO"):
        task.component.pop(name)
    task.component["X-WEATHER-PLANNER"] = "lazur/gale"
    task.save()
    task.complete()
    report = sync(both(collection), LAZUR, [alert()], NOW)
    assert not any("secure" in s for s in report.created)  # not recreated: it is still the alert's task
    assert len(tasks(collection)) == 1
