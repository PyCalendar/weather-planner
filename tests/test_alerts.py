from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from weather_planner.alerts import evaluate, nth_worst
from weather_planner.config import AlertRule, parse_alert
from weather_planner.openmeteo import Ensemble, Forecast
from weather_planner.places import parse_place

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
OPEN = parse_place("open", {"position": [0, 0]})
NORTH_SHELTERED = parse_place("bay", {"position": [0, 0], "shelter": {"N": 0.9}})


def forecast(hours: int = 24, **models: dict[str, Any]) -> Forecast:
    """Models given as {variable: constant or list}; wind speed defaults to 1, direction to N."""
    out: dict[str, dict[str, list[float | None]]] = {}
    for name, variables in models.items():
        series = {"wind_speed_10m": 1.0, "wind_direction_10m": 0.0, **variables}
        out[name] = {k: list(v) if isinstance(v, list) else [v] * hours for k, v in series.items()}
    return Forecast(times=[T0 + timedelta(hours=h) for h in range(hours)], grid_point=(0, 0), models=out)


def rule(**data: Any) -> AlertRule:
    return parse_alert("test", data, horizon=timedelta(hours=48))


def test_nth_worst() -> None:
    assert nth_worst([1, 5, None, 3], 1) == 5
    assert nth_worst([1, 5, None, 3], 2) == 3
    assert nth_worst([1, 5, None, 3], 2, highest=False) == 3
    assert nth_worst([1, 5], 3) == 1  # fewer models than N: the least bad one
    assert nth_worst([None, None], 1) is None


def test_effective_gust_combines_worst_gust_with_least_sheltered_direction() -> None:
    fc = forecast(
        a={"wind_gusts_10m": 20.0, "wind_direction_10m": 0.0},  # strong, but from the sheltered north
        b={"wind_gusts_10m": 10.0, "wind_direction_10m": 180.0},  # weaker, from the open south
    )
    [alert] = evaluate(rule(effective_gust=15), NORTH_SHELTERED, fc, None, T0)
    assert alert.values["effective_gust"] == pytest.approx(20)
    assert alert.direction == 180
    # second worst: 10 m/s gust with the second least shelter (0.9) -> 1 m/s
    assert evaluate(rule(effective_gust=0.5, worst=2), NORTH_SHELTERED, fc, None, T0)[0].values[
        "effective_gust"
    ] == pytest.approx(1)
    assert evaluate(rule(effective_gust=15, worst=2), NORTH_SHELTERED, fc, None, T0) == []


def test_worst_n_ignores_one_outlier_model() -> None:
    fc = forecast(a={"wind_gusts_10m": 30.0}, b={"wind_gusts_10m": 10.0}, c={"wind_gusts_10m": 12.0})
    assert evaluate(rule(gust=20), OPEN, fc, None, T0)
    assert not evaluate(rule(gust=20, worst=2), OPEN, fc, None, T0)


def test_consecutive_windows_merge_and_gaps_split() -> None:
    gusts = [5.0] * 24
    for h in (2, 3, 4, 5, 11):  # gusts are stamped at the end of their hour
        gusts[h] = 25.0
    fc = forecast(a={"wind_gusts_10m": gusts})
    alerts = evaluate(rule(gust=20, task="reef", lead="2h"), OPEN, fc, None, T0)
    assert [(a.start, a.end) for a in alerts] == [
        (T0, T0 + timedelta(hours=6)),
        (T0 + timedelta(hours=9), T0 + timedelta(hours=12)),
    ]
    assert alerts[0].task == "reef"
    assert alerts[0].task_due == T0 - timedelta(hours=2)


def test_horizon_limits_the_search() -> None:
    gusts = [5.0] * 24
    gusts[20] = 25.0
    fc = forecast(a={"wind_gusts_10m": gusts})
    assert evaluate(rule(gust=20), OPEN, fc, None, T0)
    assert not evaluate(rule(gust=20, horizon="12h"), OPEN, fc, None, T0)


def test_windows_start_at_the_current_hour() -> None:
    fc = forecast(a={"wind_gusts_10m": 25.0})
    [alert] = evaluate(rule(gust=20, horizon="6h"), OPEN, fc, None, T0 + timedelta(minutes=40))
    assert alert.start == T0
    assert alert.end == T0 + timedelta(hours=6)


def test_all_conditions_must_hold() -> None:
    fc = forecast(a={"wind_gusts_10m": 25.0, "temperature_2m": 5.0})
    assert evaluate(rule(gust=20), OPEN, fc, None, T0)
    assert not evaluate(rule(gust=20, temperature_below=0), OPEN, fc, None, T0)


def test_temperature_below_uses_the_coldest_models() -> None:
    fc = forecast(a={"temperature_2m": -3.0}, b={"temperature_2m": 2.0})
    [alert] = evaluate(rule(temperature_below=0), OPEN, fc, None, T0)
    assert alert.values["temperature_below"] == -3
    assert not evaluate(rule(temperature_below=0, worst=2), OPEN, fc, None, T0)


def test_temperature_above_and_cape() -> None:
    fc = forecast(a={"temperature_2m": 30.0, "cape": 1500.0})
    [alert] = evaluate(rule(temperature_above=25, cape=1000), OPEN, fc, None, T0)
    assert alert.values == {"temperature_above": 30, "cape": 1500}


def ensemble(per_member_hourly: list[float], hours: int = 24) -> Ensemble:
    return Ensemble(
        times=[T0 + timedelta(hours=h) for h in range(hours)],
        grid_point=(0, 0),
        members={"precipitation": [[p] * hours for p in per_member_hourly]},
    )


def test_rain_uses_ensemble_probability_over_the_window() -> None:
    fc = forecast(a={})
    # 0.4 mm/h is 1.2 mm per 3-hour window: 3 of 4 members exceed 1 mm
    ens = ensemble([0.4, 0.4, 0.4, 0.0])
    [alert] = evaluate(rule(rain=1, probability=0.7), OPEN, fc, ens, T0)
    assert alert.values["rain"] == pytest.approx(0.75)
    assert not evaluate(rule(rain=1, probability=0.8), OPEN, fc, ens, T0)
    # a 1-hour window holds only 0.4 mm
    assert not evaluate(rule(rain=1, window="1h"), OPEN, fc, ens, T0)


def test_rain_without_ensemble_is_an_error() -> None:
    with pytest.raises(ValueError, match="ensemble"):
        evaluate(rule(rain=1), OPEN, forecast(a={}), None, T0)


def test_summary() -> None:
    fc = forecast(a={"wind_gusts_10m": 16.0, "wind_direction_10m": 180.0})
    [alert] = evaluate(rule(effective_gust="25 kn"), OPEN, fc, None, T0, wind_unit="kn")
    assert alert.summary == "test: gusts to 31 kn from S (after shelter)"
    [alert] = evaluate(rule(gust=10, event="Blows"), OPEN, fc, None, T0)
    assert alert.summary == "Blows"
    [alert] = evaluate(rule(gust=10, event=False, task="x"), OPEN, fc, None, T0)
    assert alert.summary is None
    assert alert.describe("bft") == "test: gusts to 7 bft from S"


@pytest.mark.parametrize(
    ("name", "conditions", "summary"),
    [
        ("gusts", {"gust": 10}, "gusts to 31 kn from S"),
        ("CAPE", {"cape": 100}, "CAPE to 500 J/kg"),
        ("cape-high", {"cape": 100}, "cape-high: CAPE to 500 J/kg"),
        ("gale", {"gust": 10}, "gale: gusts to 31 kn from S"),
    ],
)
def test_summary_without_redundant_name(name: str, conditions: dict[str, Any], summary: str) -> None:
    fc = forecast(a={"wind_gusts_10m": 16.0, "wind_direction_10m": 180.0, "cape": 500.0})
    [alert] = evaluate(parse_alert(name, conditions, horizon=timedelta(hours=48)), OPEN, fc, None, T0, "kn")
    assert alert.summary == summary


def test_direction_only_counts_models_with_wind_from_the_sector() -> None:
    fc = forecast(
        a={"wind_speed_10m": 9.0, "wind_direction_10m": 0.0},  # strong, but northerly
        b={"wind_speed_10m": 4.0, "wind_direction_10m": 190.0},  # southerly
    )
    [alert] = evaluate(rule(wind=3, direction="135-225"), OPEN, fc, None, T0)
    assert alert.values["wind"] == pytest.approx(4)
    assert alert.direction == 190
    assert evaluate(rule(wind=5, direction="135-225"), OPEN, fc, None, T0) == []
    # no model from the sector at all: no alert
    assert evaluate(rule(wind=1, direction="90-100"), OPEN, fc, None, T0) == []


def test_direction_with_worst_ignores_a_lone_in_sector_outlier() -> None:
    fc = forecast(
        a={"wind_speed_10m": 8.0, "wind_direction_10m": 0.0},  # the outlier, from the north
        b={"wind_speed_10m": 6.0, "wind_direction_10m": 180.0},
        c={"wind_speed_10m": 6.0, "wind_direction_10m": 190.0},
    )
    assert evaluate(rule(wind=5, direction="315-45"), OPEN, fc, None, T0)
    assert evaluate(rule(wind=5, direction="315-45", worst=2), OPEN, fc, None, T0) == []


@pytest.mark.parametrize("minutes", [0, 50, 110, 170])
def test_windows_sit_on_a_fixed_grid_whatever_the_time_of_the_run(minutes: int) -> None:
    gusts = [0.0] * 6 + [30.0] * 4 + [0.0] * 14  # stamped 06-09: the hours 05:00-09:00
    fc = forecast(a={"wind_gusts_10m": gusts})
    now = T0 + timedelta(hours=3, minutes=minutes)  # all within the 03-06 window
    [alert] = evaluate(rule(gust=20, window="3h"), OPEN, fc, None, now)
    assert (alert.start, alert.end) == (T0 + timedelta(hours=3), T0 + timedelta(hours=9))
