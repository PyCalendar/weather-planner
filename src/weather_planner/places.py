"""Places: where the boat (or the house) is, and how sheltered it is.

A forecast is for a grid cell, which for a small bay is often the open sea.
So a place may name a separate ``forecast_point`` (typically a bit offshore,
where the models are right), and say per wind direction how much of that
wind actually reaches the place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def _bearing(name: str) -> float:
    try:
        return COMPASS.index(name.upper()) * 22.5
    except ValueError:
        raise ValueError(f"unknown compass direction {name!r}; use one of {', '.join(COMPASS)}") from None


def compass_name(bearing: float) -> str:
    return COMPASS[round(bearing / 22.5) % 16]


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (latitude, longitude) points."""
    (lat1, lon1), (lat2, lon2) = (tuple(map(math.radians, p)) for p in (a, b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


@dataclass(frozen=True)
class Sector:
    """A range of directions, clockwise from *start* to *end*, both included."""

    start: float
    end: float

    @classmethod
    def parse(cls, text: str) -> Sector:
        parts = text.split("-")
        if len(parts) != 2:
            raise ValueError(f"sector {text!r} must look like '150-240'")
        try:
            start, end = (float(p) for p in parts)
        except ValueError:
            raise ValueError(f"sector {text!r} must look like '150-240'") from None
        if not (0 <= start <= 360 and 0 <= end <= 360):
            raise ValueError(f"sector {text!r} must be within 0-360")
        return cls(start, end)

    def contains(self, direction: float) -> bool:
        d = direction % 360
        if self.start <= self.end:
            return self.start <= d <= self.end
        return d >= self.start or d <= self.end


@dataclass
class Place:
    name: str
    position: tuple[float, float]
    forecast_point: tuple[float, float]
    shelter: dict[float, float] = field(default_factory=dict)
    swell_exposure: list[Sector] = field(default_factory=list)
    comfortable_with: str | None = None
    holding: str | None = None
    depth: float | None = None

    def shelter_at(self, direction: float) -> float:
        """Shelter (0 = open, 1 = fully sheltered) for wind *from* direction.

        Uses the nearest configured compass point within 22.5° (half the
        gap of an 8-point rose, so N/NE/E/... alone covers every
        direction); anything not covered counts as open, which errs on the
        side of an alert.
        """
        best: tuple[float, float] | None = None
        for bearing, value in self.shelter.items():
            diff = _angle_diff(bearing, direction)
            if diff <= 22.5 and (best is None or diff < best[0]):
                best = (diff, value)
        return best[1] if best else 0.0

    def effective_wind(self, speed: float, direction: float) -> float:
        return speed * (1 - self.shelter_at(direction))

    def swell_exposed(self, direction: float) -> bool:
        return any(s.contains(direction) for s in self.swell_exposure)


def _point(value: Any, what: str) -> tuple[float, float]:
    try:
        lat, lon = (float(v) for v in value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be [latitude, longitude], got {value!r}") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"{what} {value!r} is out of range (latitude ±90, longitude ±180)")
    return (lat, lon)


def parse_place(name: str, data: dict[str, Any]) -> Place:
    position = _point(data.get("position"), f"place {name}: position")
    forecast_point = (
        _point(data["forecast_point"], f"place {name}: forecast_point") if "forecast_point" in data else position
    )
    shelter: dict[float, float] = {}
    for direction, value in (data.get("shelter") or {}).items():
        v = float(value)
        if not 0 <= v <= 1:
            raise ValueError(f"place {name}: shelter for {direction} must be between 0 and 1")
        bearing = _bearing(str(direction))
        if bearing in shelter:
            raise ValueError(f"place {name}: shelter for {direction} is given twice")
        shelter[bearing] = v
    depth = data.get("depth")
    return Place(
        name=name,
        position=position,
        forecast_point=forecast_point,
        shelter=shelter,
        swell_exposure=[Sector.parse(s) for s in data.get("swell_exposure") or []],
        comfortable_with=data.get("comfortable_with"),
        holding=data.get("holding"),
        depth=float(depth) if depth is not None else None,
    )
