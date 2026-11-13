# weather-planner

Weather alerts and planning from free forecast data, delivered through your
calendar or task list.

weather-planner is also an **example application utilizing the
[calendaring](https://github.com/pycalendar/calendaring) and
[calendaring-config](https://github.com/pycalendar/calendaring-config)
packages**: weather alerts will be written to whatever calendar server you
already use, as events ("bad forecast") and tasks ("prepare for weather"),
with the connection configured in the shared calendaring-config file.

This application is a side-project which will be mostly vibe-coded.

**Status:** pre-alpha. The `forecast` command shows the forecast, and
`alerts` shows what alerts it gives; nothing is written to a calendar yet.
Work windows and passage planning are planned.

## Personal motivation

I'm a sailor, but I hope this project can also be useful for landlubbers.  Most of the time I'm a nomad, quite often at anchor.  I'm spending lots of time checking the forecast; it's important for planning and preparation.  Like, sometimes a nice and quiet anchorage can turn into a mess in a very short time if the wind direction changes, other times simple things have to be done like "take in the laundry due to rain" or "It's going to be windy, remember to tie up or stow away all loose items on deck".  With this project I hope to:

* Spend less time searching and interpreting relevant information
* Get notified about significant weather events without actively having to search for them (particularly the CAPE index, I rarely check that one - but it may give a hint that it's likely to be rough and unexpected weather changes).
* Plan the day, like some maintenance tasks can only be done in dry or warm or calm weather

## Implemented feature: Alerts

The forecast is checked hourly by a systemd timer and compared with the configured thresholds.  Weather alerts are issued through the calendar - an event describing the alert as the weather is supposed to happen, and/or a task with a DUE before the event advising the user to prepare for the weather.

Some example use-cases:

* Temperature: Ensuring water pipes (e.g. the garden outlet) are closed before the frost comes, reminding people to change the tires on the car/bike before the roads get icy, reminder to turn on cooling systems or heating systems.
* Rain: Reminder to take in the air-dried laundry or protect other things that shouldn't get wet.  Reminder to locate the umbrella and/or rain clothes.
* Wind: Tie up or stow away anything that can fly away before the wind picks up, and (personally most important), remind me to change anchorage before a cosy cove turns nasty due to change of wind/wave direction.
* CAPE: High CAPE means unpredictable and often violent weather.  For me, that means checking the anchor sits well, ensure things like engine, rigging ropes and dinghy are prepared in case I need to leave an anchorage in a hurry, and preparing for possible rainfall and strong wind gusts even if none of the forecast models predicts it.

## Planned features

* First feature - weather alerts: Possibility to create weather events and tasks on the calendar - like 12:00 task: "prepare for strong winds", 14:00 event: "strong winds expected from NNE, 46 knots in the gusts".
* Second feature: various tasks and activities can be weather-dependent, the tool should be able to suggest a time/date for a task and pin it to the calendar.
* Route planning is also something I'm considering that could fit here.  Sometimes the destination is a variable that can be tuned according to the forecast, other times the date and time can be tuned, yet other times one just has to make the best of the situation there is.

## Data

All forecasts come from [Open-Meteo](https://open-meteo.com/): the forecast,
ensemble and marine APIs. Open-Meteo's free API is for **non-commercial use
only**. Commercial users need an Open-Meteo subscription.

## Usage

```shell
weather-planner forecast 60.39,5.32           # ad hoc coordinates
weather-planner forecast home --hours 120     # a configured place
weather-planner forecast home --json          # for scripts and AI assistants
weather-planner alerts                        # alerts for the default place
weather-planner alerts --json
```

## Configuration

`~/.config/weather-planner/config.yaml`:

```yaml
calendar: weather          # a section in the calendaring-config file (not used yet)
default_place: home
units: {wind: kn}          # display unit for wind: m/s (default), kn, km/h or bft
places:
  home:
    position: [60.39, 5.32]
    forecast_point: [60.35, 5.10]   # optional, defaults to position
    shelter: {N: 0.9, NE: 0.8, E: 0.5, S: 0.0}   # 0 = open, 1 = fully sheltered
    # parsed, but not used yet:
    swell_exposure: ["150-240"]     # directions swell can reach the place from
    comfortable_with: fine in strong northerlies
    holding: good
    depth: 4.5
```

Times are UTC. Wind, gusts and waves in a row are the maxima over its
period; `eff` is the worst effective gust (see below). `--json` is always
in m/s, whatever the display unit. `--json` includes the units and the grid points that the forecast and
marine models actually used.

Shelter values are matched to the nearest compass point within 22.5°.
Directions without a value count as open, so a missing value errs towards an
alert.

## Alerts

Alerts are checked for one place: the default place, or the one given on
the command line.

```yaml
horizon: 48h                  # how far ahead to look; default for all alerts
alerts:
  strong-wind:
    effective_gust: 25 kn     # gust after this place's shelter
    task: prepare for strong winds
    lead: 6h                  # the task is due this long before the alert starts
  rain:
    rain: 1 mm                # per window (default 3h) ...
    probability: 0.6          # ... in at least 60% of the ensemble members
    task: close hatches, take in the laundry
    lead: 2h
    event: false              # a task only, no event
  frost:
    temperature_below: 0
    event: frost              # the event's summary instead of a generated one
    horizon: 4d
```

Conditions, all of which must hold within a window (make two alerts for
"either"): `wind`, `gust`, `effective_gust` (a number in m/s, or with a
unit: `25 kn`, `90 km/h`, `7 bft` meaning force 7 or more), `rain` (mm,
with `probability`, default 0.5), `temperature_below`, `temperature_above`
(°C) and `cape` (J/kg). Thresholds are inclusive.

Settings: `window` (1h-24h, default 3h), `horizon` (up to 16d), `lead`,
`event` (true by default, false, or a summary), `task` (a summary; no task
without it), and `worst`.

The forecast models disagree. Each condition looks at the `worst`-th worst
model: 1 (the default) alerts if any model says so, 2 ignores the single
most pessimistic model, and so on. With fewer models than that, the least
bad one counts. The effective gust combines the `worst`-th strongest gust
with the `worst`-th least sheltered direction, even when they come from
different models. Rain is taken from the ensemble instead, as the share of
members with more than the given amount in the window.

Consecutive windows that meet the conditions merge into one alert, which
reports the worst values.

Not handled here, on purpose:

* **Task deadlines at awkward hours.** A task due at 03:00 is a
  general calendar problem, not a weather problem, so it belongs in
  the calendar system (plann has the "panic planning mode",
  combined with a recurring "bedtime" event it will give a warning if
  some task should be done before bedtime). weather-planner must instead tolerate the user
  (or the calendar system) moving deadlines and priorities of the
  tasks it creates.
* **Cancelled tasks and events,** e.g. the user cancelling a task because the
  forecast wasn't that bad, followed by a forecast that got worse. Left for
  a later release, if ever.
* Calendar fields other than the summary; they will become configurable
  later.

## Licence

AGPL-3.0-or-later

That's for the software itself.  As noted above, the software depends on Open-Meteo, and commercial usage requires a subscription with Open-Meteo.
