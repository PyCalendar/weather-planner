"""The weather-planner configuration file (YAML).

Calendar credentials are not kept here: ``calendar`` names a section of the
shared calendaring-config file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .places import Place, parse_place


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "weather-planner" / "config.yaml"


@dataclass
class Config:
    places: dict[str, Place] = field(default_factory=dict)
    default_place: str | None = None
    calendar_section: str | None = None

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
    places = {name: parse_place(name, data or {}) for name, data in (raw.get("places") or {}).items()}
    default = raw.get("default_place")
    if default is not None and default not in places:
        raise ValueError(f"default_place {default!r} is not among the configured places")
    return Config(places=places, default_place=default, calendar_section=raw.get("calendar"))
