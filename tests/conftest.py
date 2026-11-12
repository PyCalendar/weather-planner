from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


@pytest.fixture
def forecast_json() -> dict[str, Any]:
    return _load("forecast.json")


@pytest.fixture
def ensemble_json() -> dict[str, Any]:
    return _load("ensemble.json")


@pytest.fixture
def marine_json() -> dict[str, Any]:
    return _load("marine.json")
