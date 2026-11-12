from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from weather_planner.openmeteo import Client, parse_ensemble, parse_forecast, parse_marine


def test_parse_forecast_splits_by_model(forecast_json: dict[str, Any]) -> None:
    fc = parse_forecast(forecast_json)
    assert set(fc.models) == {"ecmwf_ifs025", "icon_seamless"}
    assert len(fc.times) == 72
    assert fc.times[0] == datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)
    wind = fc.models["ecmwf_ifs025"]["wind_speed_10m"]
    assert len(wind) == 72
    assert fc.grid_point == (60.5, 5.25)


def test_parse_forecast_single_model_has_no_suffix() -> None:
    data = {
        "latitude": 1.0,
        "longitude": 2.0,
        "utc_offset_seconds": 0,
        "hourly": {"time": ["2026-01-01T00:00"], "wind_speed_10m": [3.0]},
    }
    fc = parse_forecast(data, models=["best_match"])
    assert fc.models == {"best_match": {"wind_speed_10m": [3.0]}}


def test_parse_ensemble_collects_control_and_members(ensemble_json: dict[str, Any]) -> None:
    ens = parse_ensemble(ensemble_json)
    precip = ens.members["precipitation"]
    assert len(precip) == 51  # control + 50 perturbed members
    assert all(len(m) == 72 for m in precip)
    assert len(ens.members["wind_gusts_10m"]) == 51


def test_parse_marine(marine_json: dict[str, Any]) -> None:
    m = parse_marine(marine_json)
    assert len(m.times) == 72
    assert set(m.values) >= {"wave_height", "swell_wave_height", "swell_wave_direction"}


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, params: dict[str, Any], timeout: float) -> FakeResponse:
        self.calls.append((url, params))
        return FakeResponse(self.payload, self.status)


def test_client_forecast_request(forecast_json: dict[str, Any]) -> None:
    session = FakeSession(forecast_json)
    client = Client(session=session)
    fc = client.forecast(60.39, 5.32, models=["ecmwf_ifs025", "icon_seamless"], days=3)
    url, params = session.calls[0]
    assert url == "https://api.open-meteo.com/v1/forecast"
    assert params["latitude"] == 60.39
    assert params["models"] == "ecmwf_ifs025,icon_seamless"
    assert params["wind_speed_unit"] == "ms"
    assert params["timezone"] == "UTC"
    assert "wind_gusts_10m" in params["hourly"].split(",")
    assert "cape" in params["hourly"].split(",")
    assert set(fc.models) == {"ecmwf_ifs025", "icon_seamless"}


def test_client_ensemble_and_marine_urls(ensemble_json: dict[str, Any], marine_json: dict[str, Any]) -> None:
    s1 = FakeSession(ensemble_json)
    Client(session=s1).ensemble(1.0, 2.0)
    assert s1.calls[0][0] == "https://ensemble-api.open-meteo.com/v1/ensemble"
    s2 = FakeSession(marine_json)
    Client(session=s2).marine(1.0, 2.0)
    assert s2.calls[0][0] == "https://marine-api.open-meteo.com/v1/marine"


def test_parse_rejects_error_payload() -> None:
    with pytest.raises(ValueError, match="bad thing"):
        parse_forecast({"error": True, "reason": "bad thing"})


def test_client_reports_api_reason_on_http_error() -> None:
    session = FakeSession({"error": True, "reason": "Cannot initialize MultiDomains from nosuchmodel"}, status=400)
    with pytest.raises(ValueError, match="nosuchmodel"):
        Client(session=session).forecast(1.0, 2.0, models=["nosuchmodel"])


def test_parse_rejects_non_utc() -> None:
    data = {"latitude": 1, "longitude": 2, "utc_offset_seconds": 3600, "hourly": {"time": ["2026-01-01T00:00"]}}
    with pytest.raises(ValueError, match="UTC"):
        parse_marine(data)


def test_parse_forecast_multi_model_without_hint_and_unknown_variable() -> None:
    data = {
        "latitude": 1,
        "longitude": 2,
        "utc_offset_seconds": 0,
        "hourly": {"time": ["2026-01-01T00:00"], "visibility_m1": [1.0], "visibility_m2": [2.0]},
    }
    fc = parse_forecast(data, variables=["visibility"])
    assert fc.models == {"m1": {"visibility": [1.0]}, "m2": {"visibility": [2.0]}}


def test_parse_forecast_finds_no_models() -> None:
    data = {"latitude": 1, "longitude": 2, "utc_offset_seconds": 0, "hourly": {"time": [], "wind_speed_10m": []}}
    with pytest.raises(ValueError, match="model"):
        parse_forecast(data)
