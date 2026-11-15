"""The weather-planner configuration file (YAML).

Calendar credentials are not kept here: ``config_section`` names a section of the
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

from .places import Place, Sector, parse_place
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
    "waves": lambda v: parse_amount(v, "m"),
}
_SETTINGS = {"probability", "worst", "window", "horizon", "lead", "event", "task", "direction"}
WIND_CONDITIONS = ("effective_gust", "gust", "wind")
# Conditions a direction applies to; an alert reports the direction of the first one it has
DIRECTIONAL = (*WIND_CONDITIONS, "waves")
_TOP_LEVEL = {"config_section", "default_place", "places", "units", "horizon", "alerts"}


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
    direction: Sector | None = None  # only wind (and waves) from here count


def parse_alert(name: str, data: Any, horizon: timedelta = DEFAULT_HORIZON) -> AlertRule:
    _check_name("alert", name)
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
        p = data["probability"]
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            raise ValueError(f"alert {name}: probability must be a number above 0 and at most 1")
        rule.probability = float(p)
        if not 0 < rule.probability <= 1:
            raise ValueError(f"alert {name}: probability must be above 0 and at most 1")
    if "direction" in data:
        if not any(c in conditions for c in DIRECTIONAL):
            raise ValueError(f"alert {name}: direction only applies to {', '.join(DIRECTIONAL)}")
        try:
            rule.direction = Sector.parse(str(data["direction"]))
        except ValueError as e:
            raise ValueError(f"alert {name}: direction: {e}") from None
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
    event = data.get("event", True)
    if not isinstance(event, (str, bool)) or event == "":
        raise ValueError(f"alert {name}: event must be a summary text or true/false")
    task = data.get("task")
    if task is not None and (not isinstance(task, str) or task == ""):
        raise ValueError(f"alert {name}: task must be a summary text")
    rule.event = event
    rule.task = task
    if not rule.event and rule.task is None:
        raise ValueError(f"alert {name}: neither an event nor a task; it would never show up")
    return rule


@dataclass
class Config:
    places: dict[str, Place] = field(default_factory=dict)
    default_place: str | None = None
    config_section: str | None = None
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


def _check_name(what: str, name: Any) -> None:
    # Earlier versions keyed calendar items "<place>/<alert>"; keeping "/" out of the names keeps those readable.
    if not isinstance(name, str) or not name or "/" in name:
        raise ValueError(f"{what} name {name!r} must be a non-empty text without '/'")


def _mapping(raw: dict[str, Any], key: str) -> dict[Any, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else default_path()
    if not path.exists():
        return Config()
    raw: Any = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: must be a mapping of settings")
    unknown = set(raw) - _TOP_LEVEL
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {', '.join(sorted(map(str, unknown)))}")
    places_raw = _mapping(raw, "places")
    for name in places_raw:
        _check_name("place", name)
    places = {name: parse_place(name, data or {}) for name, data in places_raw.items()}
    default = raw.get("default_place")
    if default is not None and default not in places:
        raise ValueError(f"default_place {default!r} is not among the configured places")
    wind_unit = check_wind_unit(_mapping(raw, "units").get("wind", "m/s"))
    horizon = parse_duration(raw["horizon"]) if "horizon" in raw else DEFAULT_HORIZON
    alerts = {name: parse_alert(name, data, horizon) for name, data in _mapping(raw, "alerts").items()}
    return Config(
        places=places,
        default_place=default,
        config_section=raw.get("config_section"),
        wind_unit=wind_unit,
        alerts=alerts,
    )
