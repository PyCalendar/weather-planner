"""Alerts: find where the forecast meets an alert rule's conditions.

Models disagree, so each condition looks at the ``worst``-th worst model
(1 = the worst one, 2 = ignore the single most pessimistic model, ...).
Rain comes from the ensemble instead, as a probability, and waves from the
marine forecast, which has a single model.

Nothing here touches a calendar; an :class:`Alert` says what the calendar
should get.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import DIRECTIONAL, WIND_CONDITIONS, AlertRule
from .openmeteo import Ensemble, Forecast, Marine, value_at, window_indices
from .places import Place, Sector, compass_name
from .probability import probability_exceeds
from .units import format_speed

# Condition -> (forecast variable, whether higher is worse)
SERIES = {
    "wind": ("wind_speed_10m", True),
    "gust": ("wind_gusts_10m", True),
    "effective_gust": ("wind_gusts_10m", True),
    "temperature_below": ("temperature_2m", False),
    "temperature_above": ("temperature_2m", True),
    "cape": ("cape", True),
}
DIRECTION = "wind_direction_10m"


def _nth(items: Sequence[Any], n: int) -> Any:
    """The *n*-th item, or the last one when there are fewer than *n*."""
    return items[min(n, len(items)) - 1]


def nth_worst(values: Iterable[float | None], n: int, highest: bool = True) -> float | None:
    """The *n*-th worst value; with fewer than *n* values, the least bad one."""
    present = sorted((v for v in values if v is not None), reverse=highest)
    return _nth(present, n) if present else None


def hourly(
    place: Place, fc: Forecast, condition: str, i: int, n: int, sector: Sector | None = None
) -> tuple[float | None, float | None]:
    """The *n*-th worst model's value for *condition* at index *i*, and its wind direction.

    The effective gust combines the *n*-th worst gust with the *n*-th least
    sheltered direction, even when they come from different models: the
    models disagree on both, and either can make the place uncomfortable.

    With a *sector*, a model whose wind comes from outside it counts as
    calm for a wind condition: it still counts towards *n*, so ``worst: 2``
    still ignores a lone model that has the wind from the sector.
    """
    variable, highest = SERIES[condition]
    models = list(fc.models.values())
    pairs = [(value_at(m.get(variable), i), value_at(m.get(DIRECTION), i)) for m in models]
    if sector is not None and condition in WIND_CONDITIONS:
        pairs = [(v if d is not None and sector.contains(d) else 0.0, d) for v, d in pairs]
    if condition == "effective_gust":
        gust = nth_worst((v for v, _ in pairs), n)
        directions = sorted(
            (d for _, d in pairs if d is not None and (sector is None or sector.contains(d))), key=place.shelter_at
        )
        if gust is None or not directions:
            return None, None
        direction: float = _nth(directions, n)
        return place.effective_wind(gust, direction), direction
    present = sorted(((v, d) for v, d in pairs if v is not None), key=lambda p: p[0], reverse=highest)
    if not present:
        return None, None
    value, direction_ = _nth(present, n)
    return value, direction_


@dataclass
class Alert:
    rule: AlertRule
    start: datetime
    end: datetime
    values: dict[str, float]  # per condition, the worst over the alert (rain: the highest probability)
    direction: float | None = None
    wind_unit: str = "m/s"

    @property
    def name(self) -> str:
        return self.rule.name

    @property
    def summary(self) -> str | None:
        """The event's summary, or None for no event."""
        if isinstance(self.rule.event, str):
            return self.rule.event
        return self.describe(self.wind_unit) if self.rule.event else None

    @property
    def task(self) -> str | None:
        return self.rule.task

    @property
    def task_due(self) -> datetime | None:
        return self.start - self.rule.lead if self.rule.task is not None else None

    def describe(self, wind_unit: str = "m/s") -> str:
        source = _source(self.values)
        parts = []
        for condition, value in self.values.items():
            if condition in WIND_CONDITIONS:
                text = f"{'wind' if condition == 'wind' else 'gusts'} to {format_speed(value, wind_unit)}"
                if condition == source and self.direction is not None:
                    text += f" from {compass_name(self.direction)}"
                if condition == "effective_gust":
                    text += " (after shelter)"
            elif condition == "rain":
                hours = self.rule.window // timedelta(hours=1)
                text = f"rain over {self.rule.conditions['rain']:g} mm per {hours}h, {value:.0%} likely"
            elif condition == "waves":
                text = f"waves to {value:.1f} m"
                if condition == source and self.direction is not None:
                    text += f" from {compass_name(self.direction)}"
            elif condition == "temperature_below":
                text = f"down to {value:.0f} °C"
            elif condition == "temperature_above":
                text = f"up to {value:.0f} °C"
            else:
                text = f"CAPE to {value:.0f} J/kg"
            parts.append(text)
        text = ", ".join(parts)
        if text.lower().startswith(f"{self.name.lower()} "):
            return text  # "gusts: gusts to ..." says it twice
        return f"{self.name}: {text}"

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "values": self.values,
            "direction": self.direction,
            "summary": self.summary,
            "task": self.task,
            "task_due": self.task_due.isoformat() if self.task_due else None,
        }


def _source(conditions: Iterable[str]) -> str | None:
    """The condition whose direction an alert reports."""
    present = set(conditions)
    return next((c for c in DIRECTIONAL if c in present), None)


def _waves(rule: AlertRule, marine: Marine, t0: datetime, t1: datetime) -> tuple[float, float | None] | None:
    """The highest wave in [t0, t1) and its direction; waves from outside ``direction`` count as none."""
    heights, directions = marine.values.get("wave_height"), marine.values.get("wave_direction")
    hours = []
    for i in window_indices(marine.times, t0, t1, "wave_height"):
        h, d = value_at(heights, i), value_at(directions, i)
        if h is None:
            continue
        if rule.direction is not None and (d is None or not rule.direction.contains(d)):
            h, d = 0.0, None
        hours.append((h, d))
    return max(hours, key=lambda p: p[0]) if hours else None


def wave_data_warning(marine: Marine, now: datetime, horizon: timedelta) -> str | None:
    """Why waves alerts may stay silent within *horizon* from *now*, or None.

    The marine model's grid is coarse: a point near the shore may sit in a
    land cell, which has no waves at all.
    """
    hour = now.replace(minute=0, second=0, microsecond=0)
    heights = marine.values.get("wave_height")
    stamps = [t for i, t in enumerate(marine.times) if value_at(heights, i) is not None and t >= hour]
    lat, lon = marine.grid_point
    if not stamps:
        return f"no wave data at the marine grid point {lat:.3f},{lon:.3f} (land?); waves alerts cannot fire"
    if stamps[-1] < hour + horizon - timedelta(hours=1):
        return f"wave data ends at {stamps[-1]:%a %d %H:%M} UTC, before the alert horizon"
    return None


def _worse(condition: str, a: float, b: float) -> bool:
    """Whether *a* is worse than *b*."""
    if condition in ("rain", "waves"):
        return a > b
    return a > b if SERIES[condition][1] else a < b


def _window(
    rule: AlertRule,
    place: Place,
    fc: Forecast,
    ens: Ensemble | None,
    marine: Marine | None,
    t0: datetime,
    t1: datetime,
) -> tuple[dict[str, float], float | None] | None:
    """The worst values in [t0, t1) if all conditions hold there, and the direction of the wind (or waves)."""
    values: dict[str, float] = {}
    directions: dict[str, float | None] = {}
    for condition, threshold in rule.conditions.items():
        if condition == "rain":
            if ens is None:
                raise ValueError(f"alert {rule.name}: rain needs the ensemble forecast")
            p = probability_exceeds(ens, "precipitation", threshold, t0, t1)
            if p is None or p < rule.probability:
                return None
            values[condition] = p
            continue
        if condition == "waves":
            if marine is None:
                raise ValueError(f"alert {rule.name}: waves need the marine forecast")
            found = _waves(rule, marine, t0, t1)
            if found is None or found[0] < threshold:
                return None
            values[condition], directions[condition] = found
            continue
        variable, highest = SERIES[condition]
        hours = [
            hourly(place, fc, condition, i, rule.worst, rule.direction)
            for i in window_indices(fc.times, t0, t1, variable)
        ]
        present = [(v, d) for v, d in hours if v is not None]
        if not present:
            return None
        value, direction = (max if highest else min)(present, key=lambda p: p[0])
        if (value < threshold) if highest else (value > threshold):
            return None
        values[condition] = value
        directions[condition] = direction
    source = _source(directions)
    return values, directions[source] if source else None


def evaluate(
    rule: AlertRule,
    place: Place,
    fc: Forecast,
    ens: Ensemble | None,
    now: datetime,
    wind_unit: str = "m/s",
    marine: Marine | None = None,
) -> list[Alert]:
    """The alerts *rule* gives within its horizon, from the window holding *now* on.

    Windows sit on a fixed grid (their start is a multiple of the window
    length since the epoch, in UTC), so a run at any time of the hour or
    day sees the same windows.  Thresholds are inclusive.  Consecutive
    windows where all conditions hold merge into one alert, which keeps the
    worst value of each.
    """
    alerts: list[Alert] = []
    hour = now.replace(minute=0, second=0, microsecond=0)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    t0 = hour - (hour - epoch) % rule.window
    end = hour + rule.horizon
    while t0 < end:
        t1 = min(t0 + rule.window, end)
        if not window_indices(fc.times, t0, t1, "wind_speed_10m"):
            break  # past the end of the forecast
        found = _window(rule, place, fc, ens, marine, t0, t1)
        if found is not None:
            values, direction = found
            last = alerts[-1] if alerts else None
            if last is not None and last.end == t0:
                last.end = t1
                for condition, value in values.items():
                    if _worse(condition, value, last.values[condition]):
                        last.values[condition] = value
                        if condition == _source(values):
                            last.direction = direction
            else:
                alerts.append(Alert(rule, t0, t1, values, direction, wind_unit))
        t0 = t1
    return alerts
