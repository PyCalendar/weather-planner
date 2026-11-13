from __future__ import annotations

from datetime import timedelta
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


ALERTS = """
default_place: home
places:
  home:
    position: [60.39, 5.32]
units: {wind: kn}
horizon: 48h
alerts:
  strong-wind:
    effective_gust: 25 kn
    task: prepare for strong winds
    lead: 6h
    worst: 2
  rain:
    rain: 1 mm
    probability: 0.6
    window: 6h
    task: close hatches
    event: false
  frost:
    temperature_below: -2
    horizon: 4d
    event: frost tonight
"""


def test_load_alerts(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text(ALERTS)
    cfg = load_config(f)
    assert cfg.wind_unit == "kn"
    wind, rain, frost = (cfg.alerts[n] for n in ("strong-wind", "rain", "frost"))
    assert wind.conditions == {"effective_gust": pytest.approx(25 * 0.514444, abs=1e-3)}
    assert wind.task == "prepare for strong winds"
    assert wind.lead == timedelta(hours=6)
    assert wind.worst == 2
    assert wind.horizon == timedelta(hours=48)
    assert wind.window == timedelta(hours=3)
    assert wind.event is True
    assert rain.conditions == {"rain": 1.0}
    assert rain.probability == 0.6
    assert rain.window == timedelta(hours=6)
    assert rain.event is False
    assert frost.conditions == {"temperature_below": -2.0}
    assert frost.horizon == timedelta(days=4)
    assert frost.event == "frost tonight"
    assert frost.task is None
    assert frost.lead == timedelta(0)


def test_defaults_without_alerts(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text("places: {}\n")
    cfg = load_config(f)
    assert cfg.alerts == {}
    assert cfg.wind_unit == "m/s"


@pytest.mark.parametrize(
    ("alert", "message"),
    [
        ("{gust: 20, colour: red}", "colour"),
        ("{task: do something}", "condition"),
        ("{gust: 20, event: false}", "neither"),
        ("{gust: 20, worst: 0}", "worst"),
        ("{gust: 20, window: 25h}", "window"),
        ("{gust: 20, horizon: 17d}", "horizon"),
        ("{gust: 20, probability: 0.5}", "probability"),
        ("{rain: 1, probability: 1.5}", "probability"),
        ("{rain: -1}", "rain"),
        ("{gust: 20 mph}", "speed"),
        ("{gust: 20, task: [a, b]}", "task"),
        ("[gust]", "mapping"),
        ("{rain: 1, direction: 135-225}", "direction"),
        ("{wind: 5, direction: south}", "sector"),
    ],
)
def test_bad_alert_rejected(tmp_path: Path, alert: str, message: str) -> None:
    f = tmp_path / "c.yaml"
    f.write_text(f"alerts:\n  a: {alert}\n")
    with pytest.raises(ValueError, match=message):
        load_config(f)


def test_bad_wind_unit_rejected(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text("units: {wind: mph}\n")
    with pytest.raises(ValueError, match="mph"):
        load_config(f)


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text("alert: {}\n")
    with pytest.raises(ValueError, match="alert"):
        load_config(f)


def test_direction_limits_wind_alerts_to_a_sector(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text("alerts:\n  southerly: {wind: 5 kn, direction: 135-225}\n")
    rule = load_config(f).alerts["southerly"]
    assert rule.direction is not None
    assert rule.direction.contains(180)
    assert not rule.direction.contains(90)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("units: kn\n", "units"),
        ("places: [a, b]\n", "places"),
        ("- a\n", "mapping"),
        ("places:\n  a/b: {position: [1, 2]}\n", "/"),
        ("alerts:\n  a/b: {gust: 20}\n", "/"),
        ("alerts:\n  a: {gust: 20, task: true}\n", "task"),
        ("alerts:\n  a: {gust: 20, event: '', task: x}\n", "event"),
        ("alerts:\n  a: {rain: 1, probability: true}\n", "probability"),
        ("alerts:\n  a: {rain: 1, probability: abc}\n", "probability"),
    ],
)
def test_malformed_config_rejected(tmp_path: Path, text: str, message: str) -> None:
    f = tmp_path / "c.yaml"
    f.write_text(text)
    with pytest.raises(ValueError, match=message):
        load_config(f)
