from __future__ import annotations

from datetime import timedelta

import pytest

from weather_planner.units import format_speed, parse_amount, parse_duration, parse_speed, to_unit


@pytest.mark.parametrize(
    ("text", "ms"),
    [
        (10, 10.0),
        (7.5, 7.5),
        ("10", 10.0),
        ("10 m/s", 10.0),
        ("25 kn", 25 * 0.514444),
        ("25kn", 25 * 0.514444),
        ("36 km/h", 10.0),
        ("7 bft", 13.9),  # the lower bound of force 7
        ("12 bft", 32.7),
    ],
)
def test_parse_speed(text: str | float, ms: float) -> None:
    assert parse_speed(text) == pytest.approx(ms, abs=1e-3)


@pytest.mark.parametrize("text", ["fast", "10 mph", "13 bft", "6.5 bft", "-3 kn", True])
def test_parse_speed_rejects(text: object) -> None:
    with pytest.raises(ValueError, match="speed"):
        parse_speed(text)


@pytest.mark.parametrize(
    ("ms", "unit", "text"),
    [
        (10.0, "m/s", "10 m/s"),
        (16.0, "kn", "31 kn"),
        (10.0, "km/h", "36 km/h"),
        (13.9, "bft", "7 bft"),
        (13.8, "bft", "6 bft"),
        (40.0, "bft", "12 bft"),
        (0.0, "bft", "0 bft"),
    ],
)
def test_format_speed(ms: float, unit: str, text: str) -> None:
    assert format_speed(ms, unit) == text


def test_to_unit_beaufort_is_the_force_number() -> None:
    assert to_unit(17.2, "bft") == 8
    assert to_unit(10.0, "kn") == pytest.approx(19.438, abs=1e-3)


def test_unknown_display_unit() -> None:
    with pytest.raises(ValueError, match="unit"):
        format_speed(1.0, "furlongs/fortnight")


@pytest.mark.parametrize(
    ("text", "delta"),
    [(6, timedelta(hours=6)), ("6h", timedelta(hours=6)), ("90m", timedelta(minutes=90)), ("2d", timedelta(days=2))],
)
def test_parse_duration(text: str | int, delta: timedelta) -> None:
    assert parse_duration(text) == delta


@pytest.mark.parametrize("text", ["soon", "-2h", "3w", None])
def test_parse_duration_rejects(text: object) -> None:
    with pytest.raises(ValueError, match="duration"):
        parse_duration(text)


@pytest.mark.parametrize(("text", "mm"), [(1, 1.0), ("0.5", 0.5), ("2 mm", 2.0), ("2mm", 2.0)])
def test_parse_amount(text: str | float, mm: float) -> None:
    assert parse_amount(text, "mm") == mm


def test_parse_amount_rejects_wrong_unit() -> None:
    with pytest.raises(ValueError, match="mm"):
        parse_amount("2 cm", "mm")
