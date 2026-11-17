"""Put alerts in a calendar, as events and tasks, and keep them in step with the forecast.

Each run sees a new forecast, so the items written last time are found again
and updated rather than added anew: an item belongs to an alert when it is
at the same place (its ``LOCATION`` is the place's name and its ``GEO`` is
near the place's position), was written for the same rule and its time
overlaps the alert's.  Items for alerts
that are gone are deleted, as long as they lie wholly in the future and, for
a task, it is still open.

The user, or the calendar system, may move a task's deadline: the deadline
weather-planner last wrote is kept in ``X-PYCAL-WEATHER-PLANNER-DUE``, and a task
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import icalendar
from calendaring import Component, SyncItem, SyncTask, Workspace
from icalendar_searcher import Searcher

from .alerts import Alert
from .config import OWN_PREFIX
from .places import Place, distance_km

# Marks every item we write, so that we only ever search for (and delete) our own.
CATEGORY = "weather-planner"

# Private iCalendar properties, stored on each item so the next run can find it again.
KEY = OWN_PREFIX  # the alert's name
START = f"{OWN_PREFIX}-START"  # the alert's span; also set on tasks, which have no DTSTART
END = f"{OWN_PREFIX}-END"
DUE = f"{OWN_PREFIX}-DUE"  # the deadline last written (tasks only)

# An item whose GEO is further than this from the place's position is not at that place.
PLACE_TOLERANCE_KM = 5.0
# A GEO this close to the place's position is as written (servers may round coordinates).
GEO_PRECISION_KM = 0.01

CLOSED = {"COMPLETED", "CANCELLED"}  # STATUS values of a task nobody should touch any more

# The two kinds of calendar item, as the `kind` argument of the helpers below.
EVENT = "event"
TODO = "todo"


@dataclass
class Report:
    """What a sync changed in the calendars, for the user to see.

    Each entry is an item's summary, prefixed with "event: " or "task: ".
    The ``alerts --write`` command prints them, one line per item (to
    stderr with ``--json``, so as not to spoil the JSON); under the
    systemd timer they end up in the journal.  Items left as they were
    are not mentioned.
    """

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


@dataclass
class Calendars:
    """Where new events and tasks go (the same calendar if it takes both), and where ours are looked for."""

    events: Any
    tasks: Any
    searched: list[Any] = field(default_factory=list)  # every calendar of the section; ours may be in any


def _first_taking(calendars: list[Any], component: Component) -> Any:
    """The first of *calendars* that takes *component*."""
    for calendar in calendars:
        if component in calendar.components:
            return calendar
    what = "events" if component == Component.EVENT else "tasks"
    raise ValueError(f"no calendar takes {what}")


@contextmanager
def open_calendars(section: str | None = None, path: str | Path | None = None) -> Iterator[Calendars]:
    """The calendars for events and for tasks in *section* (default: the default section).

    New items go to the first calendar that takes that kind, so events and
    tasks may go to the same one.  Items we wrote earlier are looked for in
    all the calendars, so that a change of which one is first leaves none
    behind.  Raises ValueError when there is no calendar for a kind.
    """
    with Workspace.from_config(path, section=section) as workspace:
        listed = workspace.collections()
        if listed.errors:
            raise ValueError("; ".join(f"{k}: {v}" for k, v in listed.errors.items()))
        yield Calendars(
            events=_first_taking(listed.items, Component.EVENT),
            tasks=_first_taking(listed.items, Component.TASK),
            searched=listed.items,
        )


# --- Reading what is already in the calendar -------------------------------


def _rule(c: icalendar.Component) -> str:
    """The name of the alert rule the item was written for."""
    return str(c.get(KEY, ""))


def _geo(c: icalendar.Component) -> tuple[float, float] | None:
    """The item's GEO, or None when it has none (calendaring refuses a malformed one when reading)."""
    return c.decoded("GEO") if "GEO" in c else None


def _at_place(c: icalendar.Component, place: Place) -> bool:
    """Whether the item was written for *place*: same LOCATION, and a GEO (if any) not far off."""
    if str(c.get("LOCATION", "")) != place.name:
        return False
    geo = _geo(c)
    return geo is None or distance_km(geo, place.position) <= PLACE_TOLERANCE_KM


def _own_for_place(collections: list[Any], place: Place, kind: str) -> list[SyncItem]:
    """All events (kind "event") or tasks (kind "todo") we have written for *place*, closed ones included."""
    searcher = Searcher(event=(kind == EVENT), todo=(kind == TODO), include_completed=True)
    searcher.add_property_filter("categories", CATEGORY)
    return [
        i
        for collection in collections
        for i in collection.search(searcher)
        if KEY in i.component and _at_place(i.component, place)
    ]


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


def _moved_by_user(task: SyncTask) -> bool:
    """Whether the task's DUE is no longer the one weather-planner last wrote (compared as instants)."""
    c = task.component
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


def _put(c: icalendar.Component, name: str, value: str) -> bool:
    """Set property *name* to *value* unless it already is; whether it changed."""
    if str(c.get(name, "")) == value:
        return False
    c.pop(name, None)
    c.add(name, value)
    return True


def _set(item: SyncItem, a: Alert, place: Place) -> bool:
    """Write *a* into *item*, touching only what differs; whether anything changed.

    Changing nothing matters: an unchanged item is not saved, which keeps
    the calendar's sync tokens and the user's client quiet between runs.
    """
    c: Any = item.component  # icalendar types its properties per component class
    changed = False

    # Common to events and tasks: our bookkeeping properties and the description.
    for name, value in {
        KEY: a.name,
        "LOCATION": place.name,
        START: a.start.isoformat(),
        END: a.end.isoformat(),
        "DESCRIPTION": _description(a),
    }.items():
        changed |= _put(c, name, value)
    geo = _geo(c)
    if geo is None or distance_km(geo, place.position) > GEO_PRECISION_KM:  # servers may round it
        c.pop("GEO", None)
        c.add("GEO", place.position)
        changed = True
    if CATEGORY not in [str(x) for x in c.categories]:
        c.categories = [*c.categories, CATEGORY]
        changed = True

    if isinstance(item, SyncTask):
        assert a.task is not None
        assert a.task_due is not None
        changed |= _put(c, "SUMMARY", a.task)
        # A deadline the user has moved is theirs to keep.
        if not _moved_by_user(item):
            if c.decoded("DUE") != a.task_due:
                # A DTSTART (set by the user, or plann) moves by as much, keeping its
                # TZID, or staying floating; a date-only one is left alone.
                start = c.decoded("DTSTART", None)
                if isinstance(start, datetime):
                    shift = a.task_due - c.decoded("DUE")
                    if start.tzinfo is None:
                        c.DTSTART = start + shift
                    else:  # in UTC, or a shift across a DST change is an hour off
                        c.DTSTART = (start.astimezone(timezone.utc) + shift).astimezone(start.tzinfo)
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


def _uid(place: Place, a: Alert) -> str:
    """A fresh UID; readable in the calendar's raw data, unique through the UUID."""
    return f"weather-planner-{place.name}-{a.name}-{uuid.uuid4()}"


def _find_match(existing: list[SyncItem], used: set[str], a: Alert) -> SyncItem | None:
    """The not yet claimed item (already known to be at the right place) written for *a*'s rule, overlapping it."""
    return next((i for i in existing if i.uid not in used and _rule(i.component) == a.name and _overlaps(i, a)), None)


def _create(collection: Any, place: Place, a: Alert, kind: str) -> SyncItem:
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
    # The configured properties are written once only: a priority (say) the user changes later is theirs.
    for name, value in (a.rule.task_properties if kind == TODO else a.rule.event_properties).items():
        item.component.add(name, value)
    _set(item, a, place)  # fills in the rest
    item.save()
    return cast(SyncItem, item)


def _sync_kind(
    collection: Any, searched: list[Any], place: Place, alerts: list[Alert], now: datetime, kind: str, report: Report
) -> None:
    """Bring the events (or tasks) for *place* in line with *alerts*.

    Every alert is matched to an existing item (updated), or gets a new one
    (created in *collection*).  Existing items are looked for in all of
    *searched*; those left unmatched are deleted if still in the future.
    """
    label = "event" if kind == EVENT else "task"
    existing = _own_for_place(searched, place, kind)
    used: set[str] = set()  # UIDs of the items matched so far; each item serves one alert only

    for alert in alerts:
        match = _find_match(existing, used, alert)
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


def _searched(calendars: Calendars, target: Any, component: Component) -> list[Any]:
    """The calendars to look for our items of this kind in: the target, and the others that take the kind."""
    others = [c for c in calendars.searched if c is not target and component in c.components]
    return [target, *others]


def sync(calendars: Calendars, place: Place, alerts: list[Alert], now: datetime) -> Report:
    """Bring weather-planner's events and tasks for *place* in line with *alerts*, the latest forecast.

    Each alert should have an event (when its rule gives one a ``summary``)
    and a task (when its rule has a ``task``).  Items written on earlier runs
    are updated to match; missing ones are created; ours that no alert
    accounts for any more are deleted, within the limits described in the
    module docstring.  Items for other places, and items weather-planner did
    not write, are left alone.
    """
    report = Report()
    events, tasks = calendars.events, calendars.tasks
    _sync_kind(
        events,
        _searched(calendars, events, Component.EVENT),
        place,
        [a for a in alerts if a.summary is not None],
        now,
        EVENT,
        report,
    )
    _sync_kind(
        tasks,
        _searched(calendars, tasks, Component.TASK),
        place,
        [a for a in alerts if a.task is not None],
        now,
        TODO,
        report,
    )
    return report
