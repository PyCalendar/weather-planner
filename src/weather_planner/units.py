"""Units in the configuration and in the output.

Everything internal is SI-ish, as Open-Meteo delivers it when asked: m/s,
mm, °C.  The configuration may write a value with a unit (``25 kn``,
``7 bft``, ``6h``); plain numbers are in the internal unit.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from datetime import timedelta
from typing import Any

# m/s per unit; Beaufort is not linear and handled apart
SPEED_UNITS = {"m/s": 1.0, "kn": 1852 / 3600, "km/h": 1 / 3.6}
WIND_UNITS = (*SPEED_UNITS, "bft")
# Lower bound in m/s of each Beaufort force (WMO), force 0 to 12
BEAUFORT = (0.0, 0.5, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7)

_NUMBER = r"([-+]?\d+(?:\.\d+)?)"
_DURATIONS = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}


def _split(value: Any, what: str) -> tuple[float, str]:
    """A number and its (possibly empty) unit."""
    if isinstance(value, bool):  # YAML "yes" is not a number
        raise ValueError(f"{what} {value!r} is not a number")
    if isinstance(value, (int, float)):
        return float(value), ""
    match = re.fullmatch(_NUMBER + r"\s*(\S*)", str(value).strip())
    if not match:
        raise ValueError(f"{what} {value!r} is not a number with an optional unit")
    return float(match[1]), match[2]


def parse_speed(value: Any) -> float:
    """A wind speed in m/s, from a number (m/s) or e.g. '25 kn', '36 km/h', '7 bft'.

    A Beaufort force means its lower bound: '7 bft' is 13.9 m/s, so that
    "gust: 7 bft" alerts at force 7 and above.
    """
    number, unit = _split(value, "speed")
    if number < 0:
        raise ValueError(f"speed {value!r} is negative")
    if unit == "bft":
        if not number.is_integer() or number > 12:
            raise ValueError(f"speed {value!r}: Beaufort force must be a whole number 0-12")
        return BEAUFORT[int(number)]
    if unit and unit not in SPEED_UNITS:
        raise ValueError(f"speed {value!r} has unknown unit {unit!r}; use one of {', '.join(WIND_UNITS)}")
    return number * SPEED_UNITS.get(unit, 1.0)


def check_wind_unit(unit: str) -> str:
    if unit not in WIND_UNITS:
        raise ValueError(f"unknown wind unit {unit!r}; use one of {', '.join(WIND_UNITS)}")
    return unit


def to_unit(ms: float, unit: str) -> float:
    """m/s in *unit*; for Beaufort, the force number."""
    if check_wind_unit(unit) == "bft":
        return float(bisect_right(BEAUFORT, ms) - 1)
    return ms / SPEED_UNITS[unit]


def format_speed(ms: float, unit: str) -> str:
    return f"{to_unit(ms, unit):.0f} {unit}"


def parse_duration(value: Any) -> timedelta:
    """A duration from a number (hours) or e.g. '90m', '6h', '2d'."""
    try:
        number, unit = _split(value, "duration")
    except ValueError:
        raise ValueError(f"duration {value!r} must look like 90m, 6h or 2d") from None
    if number < 0 or (unit and unit not in _DURATIONS):
        raise ValueError(f"duration {value!r} must look like 90m, 6h or 2d")
    return number * _DURATIONS[unit or "h"]


def parse_amount(value: Any, unit: str) -> float:
    """A number, optionally followed by *unit* (the only one accepted)."""
    number, given = _split(value, unit)
    if given and given != unit:
        raise ValueError(f"{value!r}: expected a number in {unit}")
    return number
