"""The weather-planner configuration file (YAML).

Calendar credentials are not kept here: ``calendar`` names a section of the
shared calendaring-config file.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from .places import Place, parse_place
from .units import check_wind_unit, parse_amount, parse_duration, parse_speed

MAX_HORIZON = timedelta(days=16)  # what Open-Meteo forecasts
DEFAULT_HORIZON = timedelta(hours=48)
DEFAULT_WINDOW = timedelta(hours=3)

# Condition key -> parser of its threshold (to m/s, mm, °C, J/kg)
CONDITIONS: dict[str, Callable[[Any], float]] = {
    "wind": parse_speed,
    "gust": parse_speed,
    "effective_gust": parse_speed,
    "rain": lambda v: parse_amount(v, "mm"),
    "temperature_below": lambda v: parse_amount(v, "°C"),
    "temperature_above": lambda v: parse_amount(v, "°C"),
    "cape": lambda v: parse_amount(v, "J/kg"),
}
_SETTINGS = {"probability", "worst", "window", "horizon", "lead", "event", "task"}
_TOP_LEVEL = {"calendar", "default_place", "places", "units", "horizon", "alerts"}


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "weather-planner" / "config.yaml"


@dataclass
class AlertRule:
    """When to warn, and how.

    All *conditions* must hold in a *window* for it to count; consecutive
    counting windows within *horizon* make one alert.
    """

    name: str
    conditions: dict[str, float]
    probability: float = 0.5  # share of ensemble members, for rain
    worst: int = 1  # use the N-th worst model
    window: timedelta = DEFAULT_WINDOW
    horizon: timedelta = DEFAULT_HORIZON
    lead: timedelta = timedelta(0)  # the task is due this long before the alert starts
    event: bool | str = True  # a string is the event's summary
    task: str | None = None  # the task's summary; None for no task


def parse_alert(name: str, data: Any, horizon: timedelta = DEFAULT_HORIZON) -> AlertRule:
    if not isinstance(data, dict):
        raise ValueError(f"alert {name}: must be a mapping of conditions and settings")
    unknown = set(data) - set(CONDITIONS) - _SETTINGS
    if unknown:
        raise ValueError(
            f"alert {name}: unknown key(s) {', '.join(sorted(map(str, unknown)))};"
            f" conditions are {', '.join(CONDITIONS)}, settings {', '.join(sorted(_SETTINGS))}"
        )
    try:
        conditions = {k: CONDITIONS[k](v) for k, v in data.items() if k in CONDITIONS}
    except ValueError as e:
        raise ValueError(f"alert {name}: {e}") from None
    if not conditions:
        raise ValueError(f"alert {name}: no condition given; use one or more of {', '.join(CONDITIONS)}")
    if conditions.get("rain", 0) < 0:
        raise ValueError(f"alert {name}: rain must not be negative")
    rule = AlertRule(name, conditions, horizon=horizon)
    if "probability" in data:
        if "rain" not in conditions:
            raise ValueError(f"alert {name}: probability only applies to rain")
        rule.probability = float(data["probability"])
        if not 0 < rule.probability <= 1:
            raise ValueError(f"alert {name}: probability must be above 0 and at most 1")
    if "worst" in data:
        worst = data["worst"]
        if isinstance(worst, bool) or not isinstance(worst, int) or worst < 1:
            raise ValueError(f"alert {name}: worst must be a whole number, 1 or more")
        rule.worst = worst
    for key in ("window", "horizon", "lead"):
        if key in data:
            try:
                setattr(rule, key, parse_duration(data[key]))
            except ValueError as e:
                raise ValueError(f"alert {name}: {key}: {e}") from None
    if not timedelta(hours=1) <= rule.window <= timedelta(hours=24) or rule.window % timedelta(hours=1):
        raise ValueError(f"alert {name}: window must be whole hours, 1h-24h")
    if not timedelta(0) < rule.horizon <= MAX_HORIZON:
        raise ValueError(f"alert {name}: horizon must be above 0 and at most {MAX_HORIZON.days}d")
    for key in ("event", "task"):
        value = data.get(key)
        if value is not None and not isinstance(value, (str, bool)):
            raise ValueError(f"alert {name}: {key} must be a summary text or true/false")
    rule.event = data.get("event", True)
    task = data.get("task")
    rule.task = task if isinstance(task, str) else None
    if not rule.event and rule.task is None:
        raise ValueError(f"alert {name}: neither an event nor a task; it would never show up")
    return rule


@dataclass
class Config:
    places: dict[str, Place] = field(default_factory=dict)
    default_place: str | None = None
    calendar_section: str | None = None
    wind_unit: str = "m/s"
    alerts: dict[str, AlertRule] = field(default_factory=dict)

    def resolve_place(self, spec: str) -> Place:
        """A configured place by name, or an ad-hoc one from 'LAT,LON'."""
        if spec in self.places:
            return self.places[spec]
        try:
            lat, lon = (float(x) for x in spec.split(","))
        except ValueError:
            raise KeyError(f"no place named {spec!r} in the configuration") from None
        return parse_place(spec, {"position": [lat, lon]})  # range-checked there


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else default_path()
    if not path.exists():
        return Config()
    raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    unknown = set(raw) - _TOP_LEVEL
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {', '.join(sorted(map(str, unknown)))}")
    places = {name: parse_place(name, data or {}) for name, data in (raw.get("places") or {}).items()}
    default = raw.get("default_place")
    if default is not None and default not in places:
        raise ValueError(f"default_place {default!r} is not among the configured places")
    wind_unit = check_wind_unit((raw.get("units") or {}).get("wind", "m/s"))
    horizon = parse_duration(raw["horizon"]) if "horizon" in raw else DEFAULT_HORIZON
    alerts = {name: parse_alert(name, data, horizon) for name, data in (raw.get("alerts") or {}).items()}
    return Config(
        places=places,
        default_place=default,
        calendar_section=raw.get("calendar"),
        wind_unit=wind_unit,
        alerts=alerts,
    )
