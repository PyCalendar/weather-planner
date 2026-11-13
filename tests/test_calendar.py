from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from calendaring import Backend, Component, SyncEvent, SyncTask, Workspace

from weather_planner.alerts import Alert
from weather_planner.calendar import CATEGORY, open_collection, sync
from weather_planner.config import parse_alert

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
H = timedelta(hours=1)


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
    report = sync(collection, "lazur", [alert()], NOW)
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
    sync(collection, "lazur", [alert()], NOW)
    report = sync(collection, "lazur", [alert()], NOW)
    assert (report.created, report.updated, report.deleted) == ([], [], [])


def test_moved_alert_updates_in_place(collection: Any) -> None:
    sync(collection, "lazur", [alert()], NOW)
    [before] = events(collection)
    report = sync(collection, "lazur", [alert(start=NOW + 7 * H, hours=4)], NOW)
    assert report.created == []
    assert len(report.updated) == 2
    [after] = events(collection)
    assert after.uid == before.uid
    assert after.component.decoded("DTEND") == NOW + 11 * H
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW + 4 * H


def test_vanished_alert_deletes_event_and_open_task_but_not_completed_task(collection: Any) -> None:
    sync(collection, "lazur", [alert(), alert(start=NOW + 20 * H)], NOW)
    done = next(t for t in tasks(collection) if t.component.decoded("DUE") == NOW + 3 * H)
    done.complete()
    report = sync(collection, "lazur", [], NOW)
    assert len(report.deleted) == 3  # two events and the open task
    assert events(collection) == []
    [kept] = tasks(collection)
    assert kept.uid == done.uid


def test_completed_task_is_neither_recreated_nor_changed(collection: Any) -> None:
    sync(collection, "lazur", [alert()], NOW)
    [task] = tasks(collection)
    task.complete()
    report = sync(collection, "lazur", [alert(start=NOW + 5 * H)], NOW)
    assert [t.component.decoded("DUE") for t in tasks(collection)] == [NOW + 3 * H]
    assert all("secure" not in s for s in report.created + report.updated)


def test_deadline_moved_by_the_user_is_kept(collection: Any) -> None:
    sync(collection, "lazur", [alert()], NOW)
    [task] = tasks(collection)
    task.component.DUE = NOW + 1 * H  # the user, or plann, moved it
    task.save()
    sync(collection, "lazur", [alert(start=NOW + 8 * H)], NOW)
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW + 1 * H


def test_leaves_past_items_other_places_and_foreign_items_alone(collection: Any) -> None:
    sync(collection, "lazur", [alert(start=NOW - 2 * H)], NOW - 3 * H)
    sync(collection, "galata", [alert()], NOW)
    collection.add_event(summary="dinner", start=NOW + 6 * H, end=NOW + 7 * H)
    report = sync(collection, "lazur", [], NOW)
    assert report.deleted == []
    assert len(events(collection)) == 3


def test_event_false_and_no_task(collection: Any) -> None:
    sync(collection, "lazur", [alert(event=False), alert(name="cape", task=None)], NOW)
    assert len(events(collection)) == 1
    assert len(tasks(collection)) == 1


def test_open_collection_picks_the_only_one(tmp_path: Path) -> None:
    path = tmp_path / "calendar.conf"
    (tmp_path / "cal").mkdir()
    path.write_text(f'{{"default": {{"url": "{tmp_path / "cal"}"}}}}')
    with Workspace.from_config(path) as ws:
        ws.backends[0].create_collection("one", components={Component.EVENT, Component.TASK})
    with open_collection(None, path) as collection:
        assert collection.name == "one"


def test_open_collection_refuses_to_guess(tmp_path: Path) -> None:
    path = tmp_path / "calendar.conf"
    (tmp_path / "cal").mkdir()
    path.write_text(f'{{"default": {{"url": "{tmp_path / "cal"}"}}}}')
    with Workspace.from_config(path) as ws:
        for name in ("one", "two"):
            ws.backends[0].create_collection(name, components={Component.EVENT, Component.TASK})
    with pytest.raises(ValueError, match="2 calendars"), open_collection(None, path):
        pass


def test_hourly_runs_during_an_alert_keep_its_start_and_deadline(collection: Any) -> None:
    sync(collection, "lazur", [alert(start=NOW + 3 * H, hours=6)], NOW)
    for hour in range(4, 8):  # the alert has begun; each run sees it from the current window on
        report = sync(collection, "lazur", [alert(start=NOW + hour * H, hours=9 - hour)], NOW + hour * H)
        assert report.updated == []
    [event] = events(collection)
    assert event.component.decoded("DTSTART") == NOW + 3 * H
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW


def test_due_rewritten_in_another_timezone_is_not_taken_as_moved(collection: Any) -> None:
    from zoneinfo import ZoneInfo

    sync(collection, "lazur", [alert()], NOW)
    [task] = tasks(collection)
    task.component.DUE = (NOW + 3 * H).astimezone(ZoneInfo("Europe/Sofia"))  # same instant
    task.save()
    sync(collection, "lazur", [alert(start=NOW + 8 * H)], NOW)
    [task] = tasks(collection)
    assert task.component.decoded("DUE") == NOW + 5 * H


@pytest.mark.parametrize("prop", [("COMPLETED", NOW), ("PERCENT-COMPLETE", 100)])
def test_task_closed_without_status_is_left_alone(collection: Any, prop: tuple[str, Any]) -> None:
    sync(collection, "lazur", [alert()], NOW)
    [task] = tasks(collection)
    task.component.add(*prop)
    task.save()
    report = sync(collection, "lazur", [], NOW)
    assert report.deleted == ["event: gale: gusts to 25 m/s from S"]
    assert len(tasks(collection)) == 1
