"""Fetch and parse forecasts from the free Open-Meteo APIs.

Everything is requested in UTC and m/s, so callers never deal with units or
local offsets.  Open-Meteo snaps each request to its model's grid; the grid
point it actually used is kept, since for a sheltered bay it may well lie
out at sea, or on land.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, cast

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

FORECAST_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "snowfall",
    "cape",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
)
ENSEMBLE_VARIABLES = ("precipitation", "wind_gusts_10m", "wind_speed_10m")
MARINE_VARIABLES = (
    "wave_height",
    "wave_direction",
    "wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
)
# Values stamped with the *end* of the hour they cover (a sum or maximum over the
# preceding hour), as opposed to instantaneous values at the stamp.
PRECEDING_HOUR = frozenset({"precipitation", "snowfall", "rain", "showers", "wind_gusts_10m"})
# Summed over a window; everything else is compared hour by hour.
ACCUMULATED = frozenset({"precipitation", "snowfall", "rain", "showers"})
UNITS = {
    "wind": "m/s",
    "gust": "m/s",
    "effective_gust": "m/s",
    "cape": "J/kg",
    "temperature": "°C",
    "humidity": "%",
    "p_rain": "probability 0-1",
    "wave_height": "m",
    "swell_height": "m",
    "direction": "degrees, wind from",
    "swell_direction": "degrees, swell from",
}
DEFAULT_MODELS = ("ecmwf_ifs025", "icon_seamless", "gfs_seamless")
DEFAULT_ENSEMBLE_MODEL = "ecmwf_ifs025"

Values = list[float | None]


@dataclass
class Forecast:
    """Deterministic forecasts, one series per model and variable."""

    times: list[datetime]
    grid_point: tuple[float, float]
    models: dict[str, dict[str, Values]] = field(default_factory=dict)


@dataclass
class Ensemble:
    """Ensemble forecast: for each variable, one series per member (control first)."""

    times: list[datetime]
    grid_point: tuple[float, float]
    members: dict[str, list[Values]] = field(default_factory=dict)


@dataclass
class Marine:
    times: list[datetime]
    grid_point: tuple[float, float]
    values: dict[str, Values] = field(default_factory=dict)


def window_indices(times: Sequence[datetime], start: datetime, end: datetime, variable: str) -> list[int]:
    """Indices of the values that describe the period [start, end).

    For a preceding-hour variable that is the stamps in (start, end]; for an
    instantaneous one, the stamps in [start, end).
    """
    if variable in PRECEDING_HOUR:
        return [i for i, t in enumerate(times) if start < t <= end]
    return [i for i, t in enumerate(times) if start <= t < end]


def _check(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("error"):
        raise ValueError(f"Open-Meteo error: {data.get('reason', 'unknown')}")
    if data.get("utc_offset_seconds", 0) != 0:
        raise ValueError("Open-Meteo response is not in UTC; request it with timezone=UTC")
    hourly: dict[str, Any] = data["hourly"]
    return hourly


def _times(hourly: dict[str, Any]) -> list[datetime]:
    return [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly["time"]]


def _grid(data: dict[str, Any]) -> tuple[float, float]:
    return (float(data["latitude"]), float(data["longitude"]))


def parse_forecast(
    data: dict[str, Any],
    models: Sequence[str] | None = None,
    variables: Sequence[str] = FORECAST_VARIABLES,
) -> Forecast:
    """Parse a forecast response.

    With several models, Open-Meteo suffixes each variable with ``_<model>``;
    with a single one it doesn't, so the model name has to be passed in.
    *variables* are the ones requested, needed to split the suffix off.
    """
    hourly = _check(data)
    fc = Forecast(times=_times(hourly), grid_point=_grid(data))
    keys = [k for k in hourly if k != "time"]
    if models is not None and len(models) == 1:
        fc.models[models[0]] = {k: hourly[k] for k in keys}
        return fc
    longest_first = sorted(variables, key=len, reverse=True)
    for key in keys:
        variable = next((v for v in longest_first if key.startswith(v + "_")), None)
        if variable is None:
            continue
        model = key[len(variable) + 1 :]
        fc.models.setdefault(model, {})[variable] = hourly[key]
    if not fc.models:
        raise ValueError("no per-model series found in the forecast response; for a single model, pass models=[name]")
    return fc


def parse_ensemble(data: dict[str, Any]) -> Ensemble:
    hourly = _check(data)
    ens = Ensemble(times=_times(hourly), grid_point=_grid(data))
    for key in hourly:
        if key == "time":
            continue
        variable, sep, member = key.partition("_member")
        if sep and not member.isdigit():
            variable = key
        ens.members.setdefault(variable, []).append(hourly[key])
    return ens


def parse_marine(data: dict[str, Any]) -> Marine:
    hourly = _check(data)
    return Marine(
        times=_times(hourly),
        grid_point=_grid(data),
        values={k: v for k, v in hourly.items() if k != "time"},
    )


class _Response(Protocol):
    def raise_for_status(self) -> Any: ...
    def json(self) -> Any: ...


class _Session(Protocol):
    def get(self, url: str, params: dict[str, Any], timeout: float) -> _Response: ...


class Client:
    """A thin client for the three Open-Meteo endpoints."""

    def __init__(self, session: _Session | None = None, timeout: float = 30.0) -> None:
        if session is None:
            import niquests

            session = cast(_Session, niquests.Session())
        self.session: _Session = session
        self.timeout = timeout

    def _get(self, url: str, lat: float, lon: float, days: int, **params: Any) -> dict[str, Any]:
        query = {
            "latitude": lat,
            "longitude": lon,
            "timezone": "UTC",
            "forecast_days": days,
            **params,
        }
        response = self.session.get(url, params=query, timeout=self.timeout)
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("error"):
            # Open-Meteo explains a 400 in the body; that beats a bare HTTP error
            raise ValueError(f"Open-Meteo error: {data.get('reason', 'unknown')}")
        response.raise_for_status()
        if not isinstance(data, dict):
            raise ValueError(f"unexpected response from {url}")
        return data

    def forecast(
        self,
        lat: float,
        lon: float,
        models: Sequence[str] = DEFAULT_MODELS,
        days: int = 7,
        variables: Sequence[str] = FORECAST_VARIABLES,
    ) -> Forecast:
        data = self._get(
            FORECAST_URL,
            lat,
            lon,
            days,
            hourly=",".join(variables),
            models=",".join(models),
            wind_speed_unit="ms",
        )
        return parse_forecast(data, models=models, variables=variables)

    def ensemble(
        self,
        lat: float,
        lon: float,
        model: str = DEFAULT_ENSEMBLE_MODEL,
        days: int = 7,
        variables: Sequence[str] = ENSEMBLE_VARIABLES,
    ) -> Ensemble:
        data = self._get(
            ENSEMBLE_URL,
            lat,
            lon,
            days,
            hourly=",".join(variables),
            models=model,
            wind_speed_unit="ms",
        )
        return parse_ensemble(data)

    def marine(
        self,
        lat: float,
        lon: float,
        days: int = 7,
        variables: Sequence[str] = MARINE_VARIABLES,
    ) -> Marine:
        data = self._get(MARINE_URL, lat, lon, days, hourly=",".join(variables))
        return parse_marine(data)
