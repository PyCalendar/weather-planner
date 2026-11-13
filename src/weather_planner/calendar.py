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

# Marks every item we write, so that we only ever search for (and delete) our own.
CATEGORY = "weather-planner"

# Private iCalendar properties, stored on each item so the next run can find it again.
KEY = "X-WEATHER-PLANNER"  # "<place>/<alert name>"
START = "X-WEATHER-PLANNER-START"  # the alert's span; also set on tasks, which have no DTSTART
END = "X-WEATHER-PLANNER-END"
DUE = "X-WEATHER-PLANNER-DUE"  # the deadline last written (tasks only)

CLOSED = {"COMPLETED", "CANCELLED"}  # STATUS values of a task nobody should touch any more

# The two kinds of calendar item, as the `kind` argument of the helpers below.
EVENT = "event"
TODO = "todo"


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


# --- Reading what is already in the calendar -------------------------------


def _own(collection: Any, place: str, kind: str) -> list[SyncItem]:
    """All events (kind "event") or tasks (kind "todo") we have written for *place*, closed ones included."""
    searcher = Searcher(event=kind == EVENT, todo=kind == TODO, include_completed=True)
    searcher.add_property_filter("categories", CATEGORY)
    prefix = f"{place}/"
    return [i for i in collection.search(searcher) if str(i.component.get(KEY, "")).startswith(prefix)]


def _span(item: SyncItem) -> tuple[datetime, datetime]:
    """The alert span the item was written with (not necessarily its DTSTART/DUE)."""
    c = item.component
    return datetime.fromisoformat(str(c[START])), datetime.fromisoformat(str(c[END]))


def _overlaps(item: SyncItem, a: Alert) -> bool:
    """Whether the item's span and the alert's overlap (touching at an endpoint does not count)."""
    start, end = _span(item)
    return start < a.end and a.start < end


def _closed(item: SyncItem) -> bool:
    """Whether a task is completed or cancelled, however the calendar client chose to say so."""
    c = item.component
    return str(c.get("STATUS", "")) in CLOSED or "COMPLETED" in c or str(c.get("PERCENT-COMPLETE", "")) == "100"


def _moved_by_user(c: Any) -> bool:
    """Whether the task's DUE is no longer the one weather-planner last wrote (compared as instants)."""
    if DUE not in c or "DUE" not in c:
        # Nothing to compare: a task with neither is new, one with only one of them has been tampered with.
        return DUE in c or "DUE" in c
    due = c.decoded("DUE")
    if not isinstance(due, datetime) or due.tzinfo is None:
        return True  # a date, or floating time: the user's doing
    return due != datetime.fromisoformat(str(c[DUE]))


# --- Writing an alert into an item -------------------------------------------


def _description(a: Alert) -> str:
    return f"{a.describe(a.wind_unit)}\n{a.start:%a %d %H:%M} - {a.end:%a %d %H:%M} UTC"


def _put(c: Any, name: str, value: str) -> bool:
    """Set property *name* to *value* unless it already is; whether it changed."""
    if str(c.get(name, "")) == value:
        return False
    c.pop(name, None)
    c.add(name, value)
    return True


def _set(item: SyncItem, a: Alert, place: str) -> bool:
    """Write *a* into *item*, touching only what differs; whether anything changed.

    Changing nothing matters: an unchanged item is not saved, which keeps
    the calendar's sync tokens and the user's client quiet between runs.
    """
    c: Any = item.component  # icalendar types its properties per component class
    changed = False

    # Common to events and tasks: our bookkeeping properties and the description.
    for name, value in {
        KEY: f"{place}/{a.name}",
        START: a.start.isoformat(),
        END: a.end.isoformat(),
        "DESCRIPTION": _description(a),
    }.items():
        changed |= _put(c, name, value)
    if CATEGORY not in [str(x) for x in c.categories]:
        c.categories = [*c.categories, CATEGORY]
        changed = True

    if isinstance(item, SyncTask):
        assert a.task is not None
        assert a.task_due is not None
        changed |= _put(c, "SUMMARY", a.task)
        # A deadline the user has moved is theirs to keep.
        if not _moved_by_user(c):
            if c.decoded("DUE") != a.task_due:
                c.DUE = a.task_due
                changed = True
            changed |= _put(c, DUE, a.task_due.isoformat())
    else:
        assert a.summary is not None
        changed |= _put(c, "SUMMARY", a.summary)
        changed |= _put(c, "TRANSP", "TRANSPARENT")  # a warning should not mark the user as busy
        if c.get("DTSTART") is None or c.decoded("DTSTART") != a.start:
            c.DTSTART = a.start
            changed = True
        if c.get("DTEND") is None or c.decoded("DTEND") != a.end:
            c.DTEND = a.end
            changed = True
    return changed


# --- Syncing -------------------------------------------------------------------


def _uid(place: str, a: Alert) -> str:
    """A fresh UID; readable in the calendar's raw data, unique through the UUID."""
    return f"weather-planner-{place}-{a.name}-{uuid.uuid4()}"


def _find_match(existing: list[SyncItem], used: set[str], place: str, a: Alert) -> SyncItem | None:
    """The not yet claimed item written for the same place and rule as *a*, with an overlapping span."""
    key = f"{place}/{a.name}"
    return next((i for i in existing if i.uid not in used and str(i.component[KEY]) == key and _overlaps(i, a)), None)


def _create(collection: Any, place: str, a: Alert, kind: str) -> SyncItem:
    """Add a new event or task for *a* to *collection* and save it."""
    item: Any
    if kind == TODO:
        assert a.task_due is not None
        item = collection.add_task(uid=_uid(place, a), summary=a.task, categories=[CATEGORY])
        item.component.DUE = a.task_due
        item.component.add(DUE, a.task_due.isoformat())  # so a later run can tell if the user moves it
    else:
        item = collection.add_event(
            uid=_uid(place, a), summary=a.summary, start=a.start, end=a.end, categories=[CATEGORY]
        )
    _set(item, a, place)  # fills in the rest
    item.save()
    return item


def _sync_kind(collection: Any, place: str, alerts: list[Alert], now: datetime, kind: str, report: Report) -> None:
    """Bring the calendar's events (or tasks) for *place* in line with *alerts*.

    Every alert is matched to an existing item (updated), or gets a new one
    (created); existing items left unmatched are deleted if still in the future.
    """
    label = "event" if kind == EVENT else "task"
    existing = _own(collection, place, kind)
    used: set[str] = set()  # UIDs of the items matched so far; each item serves one alert only

    for alert in alerts:
        match = _find_match(existing, used, place, alert)
        if match is None:
            item = _create(collection, place, alert, kind)
            report.created.append(f"{label}: {item.component['SUMMARY']}")
            continue

        used.add(match.uid)
        if kind == TODO and _closed(match):
            continue  # done or dropped by the user; leave it, but don't create a replacement either

        # Once an alert has begun, keep the start it was written with (see the module docstring).
        began, _ = _span(match)
        if began < alert.start and began <= now:
            alert = replace(alert, start=began)
        if _set(match, alert, place):
            match.save()
            report.updated.append(f"{label}: {match.component['SUMMARY']}")

    # Whatever no alert claimed is stale: the forecast no longer warrants it.
    for item in existing:
        if item.uid in used:
            continue
        start, _ = _span(item)
        # Past and ongoing items are history; open tasks are the only tasks to remove.
        if start >= now and not (kind == TODO and _closed(item)):
            report.deleted.append(f"{label}: {item.component['SUMMARY']}")
            item.delete()


def sync(collection: Any, place: str, alerts: list[Alert], now: datetime) -> Report:
    """Make *collection* hold the events and tasks *alerts* give for *place*, and no other ones of ours.

    An alert gives an event if it has a ``summary`` and a task if it has a ``task``.
    """
    report = Report()
    _sync_kind(collection, place, [a for a in alerts if a.summary is not None], now, EVENT, report)
    _sync_kind(collection, place, [a for a in alerts if a.task is not None], now, TODO, report)
    return report
