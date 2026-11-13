"""Put alerts in a calendar, as events and tasks, and keep them in step with the forecast.

Each run sees a new forecast, so the items written last time are found again
and updated rather than added anew: an item belongs to an alert when it has
the same place and rule and its time overlaps the alert's.  Items for alerts
that are gone are deleted, as long as they lie wholly in the future and, for
a task, it is still open.

The user, or the calendar system, may move a task's deadline: the deadline
weather-planner last wrote is kept in ``X-WEATHER-PLANNER-DUE``, and a task
whose ``DUE`` no longer matches it keeps its deadline.  Completed or
cancelled tasks are never touched.

An alert that has begun keeps the start it was written with: later runs
see it only from the current window on, and moving its start (and with it
the task's deadline) forward every hour would say nothing new.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from calendaring import Component, SyncItem, SyncTask, Workspace
from icalendar_searcher import Searcher

from .alerts import Alert

CATEGORY = "weather-planner"
KEY = "X-WEATHER-PLANNER"  # place/rule
START = "X-WEATHER-PLANNER-START"  # the alert's span, for tasks too
END = "X-WEATHER-PLANNER-END"
DUE = "X-WEATHER-PLANNER-DUE"  # the deadline last written
CLOSED = {"COMPLETED", "CANCELLED"}


@dataclass
class Report:
    """Summaries of the items written, prefixed with "event: " or "task: "."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


@contextmanager
def open_collection(section: str | None = None, path: str | Path | None = None) -> Iterator[Any]:
    """The calendar holding both events and tasks in *section* (default: the default section).

    Raises ValueError when there is none, or more than one to choose from.
    """
    with Workspace.from_config(path, section=section) as workspace:
        listed = workspace.collections()
        if listed.errors:
            raise ValueError("; ".join(f"{k}: {v}" for k, v in listed.errors.items()))
        wanted = {Component.EVENT, Component.TASK}
        found = [c for c in listed.items if wanted <= c.components]
        if len(found) != 1:
            names = ", ".join(c.name or c.id for c in found)
            raise ValueError(
                f"{len(found)} calendars hold both events and tasks in section {section or 'default'}"
                + (f" ({names})" if names else "")
                + "; weather-planner needs exactly one"
            )
        yield found[0]


def _span(item: SyncItem) -> tuple[datetime, datetime]:
    c = item.component
    return datetime.fromisoformat(str(c[START])), datetime.fromisoformat(str(c[END]))


def _overlaps(item: SyncItem, a: Alert) -> bool:
    start, end = _span(item)
    return start < a.end and a.start < end


def _closed(item: SyncItem) -> bool:
    c = item.component
    return str(c.get("STATUS", "")) in CLOSED or "COMPLETED" in c or str(c.get("PERCENT-COMPLETE", "")) == "100"


def _moved_by_user(c: Any) -> bool:
    """Whether the task's DUE is no longer the one weather-planner last wrote (compared as instants)."""
    if DUE not in c or "DUE" not in c:
        return DUE in c or "DUE" in c
    due = c.decoded("DUE")
    if not isinstance(due, datetime) or due.tzinfo is None:
        return True  # a date, or floating time: the user's doing
    return due != datetime.fromisoformat(str(c[DUE]))


def _own(collection: Any, place: str, kind: str) -> list[SyncItem]:
    searcher = Searcher(event=kind == "event", todo=kind == "todo", include_completed=True)
    searcher.add_property_filter("categories", CATEGORY)
    prefix = f"{place}/"
    return [i for i in collection.search(searcher) if str(i.component.get(KEY, "")).startswith(prefix)]


def _description(a: Alert) -> str:
    return f"{a.describe(a.wind_unit)}\n{a.start:%a %d %H:%M} - {a.end:%a %d %H:%M} UTC"


def _set(item: SyncItem, a: Alert, place: str) -> bool:
    """Write *a* into *item*; whether anything changed."""
    c: Any = item.component  # icalendar types its properties per component class
    wanted: dict[str, str] = {
        KEY: f"{place}/{a.name}",
        START: a.start.isoformat(),
        END: a.end.isoformat(),
        "DESCRIPTION": _description(a),
    }
    changed = False
    for name, value in wanted.items():
        if str(c.get(name, "")) != value:
            c.pop(name, None)
            c.add(name, value)
            changed = True
    if CATEGORY not in [str(x) for x in c.categories]:
        c.categories = [*c.categories, CATEGORY]
        changed = True
    if isinstance(item, SyncTask):
        assert a.task is not None
        assert a.task_due is not None
        changed |= _put(c, "SUMMARY", a.task)
        if not _moved_by_user(c):
            if c.decoded("DUE") != a.task_due:
                c.DUE = a.task_due
                changed = True
            changed |= _put(c, DUE, a.task_due.isoformat())
    else:
        assert a.summary is not None
        changed |= _put(c, "SUMMARY", a.summary)
        changed |= _put(c, "TRANSP", "TRANSPARENT")
        if c.get("DTSTART") is None or c.decoded("DTSTART") != a.start:
            c.DTSTART = a.start
            changed = True
        if c.get("DTEND") is None or c.decoded("DTEND") != a.end:
            c.DTEND = a.end
            changed = True
    return changed


def _put(c: Any, name: str, value: str) -> bool:
    if str(c.get(name, "")) == value:
        return False
    c.pop(name, None)
    c.add(name, value)
    return True


def _sync_kind(collection: Any, place: str, alerts: list[Alert], now: datetime, kind: str, report: Report) -> None:
    label = "event" if kind == "event" else "task"
    existing = _own(collection, place, kind)
    used: set[str] = set()
    for a in alerts:
        key = f"{place}/{a.name}"
        match = next(
            (i for i in existing if i.uid not in used and str(i.component[KEY]) == key and _overlaps(i, a)), None
        )
        if match is not None:
            used.add(match.uid)
            if kind == "todo" and _closed(match):
                continue
            began, _ = _span(match)
            if began < a.start and began <= now:
                a = replace(a, start=began)
            if _set(match, a, place):
                match.save()
                report.updated.append(f"{label}: {match.component['SUMMARY']}")
            continue
        item: Any = (
            collection.add_task(uid=_uid(place, a), summary=a.task, categories=[CATEGORY])
            if kind == "todo"
            else collection.add_event(
                uid=_uid(place, a), summary=a.summary, start=a.start, end=a.end, categories=[CATEGORY]
            )
        )
        if kind == "todo":
            assert a.task_due is not None
            item.component.DUE = a.task_due
            item.component.add(DUE, a.task_due.isoformat())
        _set(item, a, place)
        item.save()
        report.created.append(f"{label}: {item.component['SUMMARY']}")
    for i in existing:
        if i.uid in used:
            continue
        start, _ = _span(i)
        if start >= now and not (kind == "todo" and _closed(i)):
            report.deleted.append(f"{label}: {i.component['SUMMARY']}")
            i.delete()


def _uid(place: str, a: Alert) -> str:
    return f"weather-planner-{place}-{a.name}-{uuid.uuid4()}"


def sync(collection: Any, place: str, alerts: list[Alert], now: datetime) -> Report:
    """Make *collection* hold the events and tasks *alerts* give for *place*, and no other ones of ours."""
    report = Report()
    _sync_kind(collection, place, [a for a in alerts if a.summary is not None], now, "event", report)
    _sync_kind(collection, place, [a for a in alerts if a.task is not None], now, "todo", report)
    return report
