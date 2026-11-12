from __future__ import annotations

from datetime import datetime, timedelta, timezone

from weather_planner.openmeteo import Ensemble
from weather_planner.probability import probability_exceeds

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
TIMES = [T0 + timedelta(hours=h) for h in range(7)]


def make(members: list[list[float | None]], variable: str = "wind_gusts_10m") -> Ensemble:
    return Ensemble(times=TIMES, grid_point=(0.0, 0.0), members={variable: members})


def test_any_hour_over_threshold_counts_member() -> None:
    # value at TIMES[i] covers the hour ending at TIMES[i]; TIMES[0] is outside (T0, T0+6h]
    ens = make(
        [
            [0, 0, 0, 0, 0, 0, 0],
            [0, 0, 20, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 20],
            [0, 0, 0, 0, 0, 0, 0],
        ]
    )
    assert probability_exceeds(ens, "wind_gusts_10m", 15, T0, T0 + timedelta(hours=6)) == 0.5
    assert probability_exceeds(ens, "wind_gusts_10m", 15, T0, T0 + timedelta(hours=3)) == 0.25


def test_window_is_preceding_hour_aligned() -> None:
    # a gust stamped T0 happened in the hour *before* T0, so it is outside a window starting at T0;
    # one stamped T0+3h is the last hour of a 3-hour window starting at T0
    ens = make([[20, 0, 0, 0, 0, 0, 0], [0] * 7])
    assert probability_exceeds(ens, "wind_gusts_10m", 15, T0, T0 + timedelta(hours=3)) == 0.0
    ens = make([[0, 0, 0, 20, 0, 0, 0], [0] * 7])
    assert probability_exceeds(ens, "wind_gusts_10m", 15, T0, T0 + timedelta(hours=3)) == 0.5


def test_threshold_is_strict_and_none_members_are_skipped() -> None:
    ens = make(
        [
            [15] * 7,  # exactly at threshold: no
            [None] * 7,  # missing member: not counted at all
            [0, 30, 0, 0, 0, 0, 0],
        ]
    )
    assert probability_exceeds(ens, "wind_gusts_10m", 15, T0, T0 + timedelta(hours=6)) == 0.5


def test_precipitation_is_summed_over_the_window() -> None:
    # 3-hourly ensemble rain spread evenly: 0.05 mm/h for 3 h is 0.15 mm, which is rain
    ens = make([[0, 0.05, 0.05, 0.05, 0, 0, 0], [0] * 7], variable="precipitation")
    assert probability_exceeds(ens, "precipitation", 0.1, T0, T0 + timedelta(hours=6)) == 0.5


def test_window_outside_data_gives_none() -> None:
    ens = make([[0] * 7])
    assert probability_exceeds(ens, "wind_gusts_10m", 0.1, T0 + timedelta(days=5), T0 + timedelta(days=6)) is None
