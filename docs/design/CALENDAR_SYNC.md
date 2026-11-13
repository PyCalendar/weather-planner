# Calendar sync: design decisions

How `weather-planner alerts --write` keeps the calendar in step with the
forecast, and why it is built this way. The code is in
`src/weather_planner/calendar.py`; the user-facing rules are in the README
("In the calendar").

## The problem

The tool runs unattended (hourly, from a systemd timer), and every run sees a
new forecast. An alert from the last run may now start an hour later, last
longer, have gone away, or be split in two. Meanwhile the user may have
completed the task, moved its deadline or changed its priority, or another
calendar client may have rewritten the item. The calendar has to end up
showing the current forecast without:

* piling up duplicates,
* deleting and recreating an item every time a number changes, which loses
  the user's edits and triggers change notifications,
* undoing what the user did.

## The calendar is the only state

weather-planner keeps no local database of what it wrote. Everything it needs
to find its items again is stored in the items themselves. A local cache would
go stale as soon as the user edits or deletes an item, would not be shared
between machines, and would be lost together with the laptop. The calendar is
already synchronised and backed up.

## Identity: random UIDs, matched by key and overlap

A UID derived from the alert (place, alert name, start time) looks attractive,
since a run could then find "its" item by UID. But the start time moves between
runs, so a moved alert would get a new UID: the old item would be deleted and a
new one created. That is exactly the churn we want to avoid. A UID without the
start time (place and alert name only) cannot tell two separate gales in the
same 48 hours apart.

So a UID is random (`weather-planner-<place>-<alert>-<uuid4>`, readable only
for debugging) and is never used for matching. Instead, an existing item
belongs to a new alert when:

* its key (`X-WEATHER-PLANNER`, below) is the same place and alert name, and
* its stored span overlaps the alert's span.

Each existing item is matched at most once. When a match is found, the item is
updated in place and keeps its UID.

## The X-properties

iCalendar allows private `X-` properties, and CalDAV servers are required to
store them. weather-planner uses four:

| property | value | why it is needed |
|---|---|---|
| `X-WEATHER-PLANNER` | `<place>/<alert>` | The matching key. It marks an item as weather-planner's, and says which place and alert it belongs to. The category alone cannot do that: it is a user-visible label the user may also put on their own items (the author's project category is literally `weather-planner`), and it carries neither the place nor the alert. The `/` separates the two, so neither name may contain one; otherwise place `a` with alert `b/c` and place `a/b` with alert `c` would share a key, and one place's run could delete the other's items. |
| `X-WEATHER-PLANNER-START`, `-END` | the alert's span, ISO 8601 UTC | Overlap matching needs the span. A task has no span of its own, only a deadline (`DUE` = start − lead), and once the user moves the deadline even the start cannot be worked out from it. An event's `DTSTART`/`DTEND` may be edited by the user, and the start is held fixed once the alert has begun (below), so they are not a reliable record either. Storing the span explicitly gives tasks and events the same matching rule. |
| `X-WEATHER-PLANNER-DUE` | the deadline weather-planner last wrote | Tells a deadline the user (or a planner like plann) moved apart from one weather-planner set. If `DUE` still equals this instant, the tool may move it along with the alert. If not, it has been moved by someone else and is left alone. It is compared as an instant, not as a string, because clients re-serialise `DUE` in local time with a `TZID`. |

The category `weather-planner` is set as well. It is for the user (to see or
filter what the tool made), and it narrows the search the tool runs to find
its own items. The `X-WEATHER-PLANNER` key then decides.

**Risk:** a server or client that drops unknown `X-` properties breaks the
matching. Every run would then see no items of its own, create new ones, and
never delete the old ones. Xandikos and Radicale keep them. Some groupware
servers are known to drop other properties (Zimbra drops `RELATED-TO`), so
this should be watched when supporting more backends.

## Windows on a fixed grid, and a start that holds

Alerts are evaluated in windows (default 3h). The windows sit on a fixed UTC
grid, at multiples of the window length since the epoch. If windows were
aligned to the time of the run instead, the reported span would shift by up
to window − 1 hours from one hourly run to the next, and rain totals would
cover different hours each time, so a probability could flap across its
threshold.

A run evaluates from the window that holds the current time. Once an alert
has begun, a later run therefore sees it starting at the current window. A
matched item whose stored start is already in the past keeps that start, and
the task keeps its deadline. Otherwise both would creep forward every few
hours, and an overdue "prepare for …" task would never look overdue.

## What is updated, what is deleted

* **Matched events** get summary, description, span and `TRANSP` rewritten
  when they differ. Events are `TRANSPARENT` (free time): a weather alert is
  information, not a commitment that should block the user's free/busy.
* **Matched open tasks** get summary, description and span rewritten, and the
  deadline only while the user has not moved it. Priority and other fields
  are never set by the tool, so they are never overwritten.
* **Closed tasks** (`STATUS:COMPLETED`/`CANCELLED`, a `COMPLETED` property, or
  `PERCENT-COMPLETE:100`) are never touched. They hold back a new task only
  while they overlap the alert.
* **Unmatched items** whose span lies wholly in the future are deleted, open
  tasks included. Items that have started, or are in the past, stay as
  history.

Deleting an open task whose alert vanished also deletes whatever the user
changed on it (priority, notes, a moved deadline). That is a known open
question, to be raised as an issue: options are to keep tasks the user
touched, or to require an alert to be gone on two runs in a row.

No `VALARM` is added: notification is left to the user's calendar client or
task planner.

## Which calendar

The `calendar` setting names a section of the shared calendaring-config file.
Without it, the default section is used. That section must hold exactly one
calendar that takes both events and tasks, because weather-planner does not
guess. Choosing one calendar among several, as caldav's `calendar_url` and
`calendar_name` keys do, is not supported by calendaring yet:
https://github.com/pycalendar/calendaring/issues/31.

## Alternatives not taken

* **Deterministic UIDs:** they churn when the start moves (above).
* **Linking task and event with `RELATED-TO`:** it would help clients show
  them together, but matching does not need it, and not every server keeps it.
* **Rewriting everything on each run** (delete all of ours, add afresh):
  simplest, but it throws away every user edit and notifies on every run.
