from __future__ import annotations

from pathlib import Path

import pytest

from weather_planner.config import load_config


def test_load_config(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(
        """
calendar: weather
default_place: home
places:
  home:
    position: [60.39, 5.32]
    shelter: {N: 0.5}
"""
    )
    cfg = load_config(f)
    assert cfg.calendar_section == "weather"
    assert cfg.default_place == "home"
    assert cfg.places["home"].shelter_at(0) == 0.5


def test_unknown_default_place(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text("default_place: nowhere\nplaces: {}\n")
    with pytest.raises(ValueError, match="nowhere"):
        load_config(f)


def test_missing_file_gives_empty_config(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "absent.yaml")
    assert cfg.places == {}


def test_resolve_place_by_name_or_coordinates(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text("places:\n  home:\n    position: [1, 2]\n")
    cfg = load_config(f)
    assert cfg.resolve_place("home").position == (1.0, 2.0)
    assert cfg.resolve_place("3.5,4.25").position == (3.5, 4.25)
    with pytest.raises(KeyError):
        cfg.resolve_place("elsewhere")
