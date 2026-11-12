from __future__ import annotations

import pytest

from weather_planner.places import Place, Sector, parse_place


def test_sector_contains_with_wraparound() -> None:
    s = Sector.parse("300-30")
    assert s.contains(350)
    assert s.contains(10)
    assert s.contains(300)
    assert not s.contains(180)
    assert Sector.parse("150-240").contains(200)


@pytest.mark.parametrize("bad", ["150", "a-b", "10-400"])
def test_sector_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        Sector.parse(bad)


def test_shelter_by_compass_point_nearest_bin() -> None:
    p = parse_place("bay", {"position": [10.0, 20.0], "shelter": {"N": 0.9, "NE": 0.8, "S": 0.0}})
    assert p.shelter_at(0) == 0.9
    assert p.shelter_at(10) == 0.9  # nearest is N
    assert p.shelter_at(40) == 0.8
    # unspecified directions are fully open (conservative)
    assert p.shelter_at(90) == 0.0
    assert p.shelter_at(270) == 0.0


def test_effective_wind_applies_shelter() -> None:
    p = parse_place("bay", {"position": [10.0, 20.0], "shelter": {"N": 0.75}})
    assert p.effective_wind(20.0, 0) == pytest.approx(5.0)
    assert p.effective_wind(20.0, 180) == pytest.approx(20.0)


def test_forecast_point_defaults_to_position() -> None:
    p = parse_place("x", {"position": [1.0, 2.0]})
    assert p.forecast_point == (1.0, 2.0)
    q = parse_place("y", {"position": [1.0, 2.0], "forecast_point": [1.1, 2.2]})
    assert q.forecast_point == (1.1, 2.2)


def test_full_place() -> None:
    p = parse_place(
        "spot",
        {
            "position": [10.1, 20.2],
            "swell_exposure": ["90-180"],
            "comfortable_with": "fine in strong easterlies",
            "holding": "good",
            "depth": 4.5,
        },
    )
    assert isinstance(p, Place)
    assert p.swell_exposed(135)
    assert not p.swell_exposed(0)
    assert p.depth == 4.5
    assert p.comfortable_with == "fine in strong easterlies"


def test_bad_shelter_value_rejected() -> None:
    with pytest.raises(ValueError, match="shelter"):
        parse_place("x", {"position": [1, 2], "shelter": {"N": 1.5}})
    with pytest.raises(ValueError, match="direction"):
        parse_place("x", {"position": [1, 2], "shelter": {"NORTH": 0.5}})


def test_duplicate_shelter_direction_rejected() -> None:
    with pytest.raises(ValueError, match="twice"):
        parse_place("x", {"position": [1, 2], "shelter": {"N": 0.5, "n": 0.7}})


@pytest.mark.parametrize("pos", [[91, 0], [0, 181], [-91, 0]])
def test_position_out_of_range_rejected(pos: list[float]) -> None:
    with pytest.raises(ValueError, match="range"):
        parse_place("x", {"position": pos})
