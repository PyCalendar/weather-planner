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

**Status:** pre-alpha. Only the `forecast` command exists so far; calendar
alerts, work windows and passage planning are planned.

## Data

All forecasts come from [Open-Meteo](https://open-meteo.com/): the forecast,
ensemble and marine APIs. Open-Meteo's free API is for **non-commercial use
only**. Commercial users need an Open-Meteo subscription.

## Planned features

* First feature - weather alerts: Possibility to create weather events and tasks on the calendar - like 12:00 task: "prepare for strong winds", 14:00 event: "strong winds expected from NNE, 46 knots in the gusts".
* Second feature: various tasks and activities can be weather-dependent, the tool can suggest a time/date for it and pin it to the calendar.

## Usage

```shell
weather-planner forecast 60.39,5.32           # ad hoc coordinates
weather-planner forecast home --hours 120     # a configured place
weather-planner forecast home --json          # for scripts and AI assistants
```

## Configuration

`~/.config/weather-planner/config.yaml`:

```yaml
calendar: weather          # a section in the calendaring-config file (not used yet)
default_place: home
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
period; `eff` is the worst gust after the place's shelter for the wind
direction of that hour. `--json` includes the units and the grid points that
the forecast and marine models actually used.

Shelter values are matched to the nearest compass point within 22.5°.
Directions without a value count as open, so a missing value errs towards an
alert.

## Licence

AGPL-3.0-or-later
