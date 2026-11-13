from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from weather_planner import cli
from weather_planner.cli import build_rows, forecast_days
from weather_planner.openmeteo import Forecast, parse_ensemble, parse_forecast, parse_marine
from weather_planner.places import parse_place

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_build_rows(forecast_json: dict[str, Any], ensemble_json: dict[str, Any], marine_json: dict[str, Any]) -> None:
    place = parse_place("bay", {"position": [60.39, 5.32], "shelter": dict.fromkeys(("N", "E", "S", "W"), 0.5)})
    fc = parse_forecast(forecast_json)
    now = datetime(2026, 11, 12, 6, tzinfo=timezone.utc)
    rows = build_rows(place, fc, parse_ensemble(ensemble_json), parse_marine(marine_json), step=3, hours=24, now=now)
    assert len(rows) == 8
    assert rows[0]["time"] == "2026-11-12T06:00:00+00:00"
    for r in rows:
        assert 0 <= r["p_rain"] <= 1
        assert set(r["wind"]) == {"ecmwf_ifs025", "icon_seamless"}
        assert r["effective_gust"] <= max(r["gust"].values())


def synthetic(gusts: list[float], directions: list[float], precip: list[float] | None = None) -> Forecast:
    n = len(gusts)
    series: dict[str, list[float | None]] = {
        "wind_gusts_10m": list(gusts),
        "wind_direction_10m": list(directions),
        "wind_speed_10m": [1.0] * n,
    }
    if precip is not None:
        series["precipitation"] = list(precip)
    return Forecast(times=[T0 + timedelta(hours=h) for h in range(n)], grid_point=(0, 0), models={"m": series})


def test_effective_gust_uses_direction_of_each_hour() -> None:
    # north is sheltered, south is open; the strong gust comes from the south in the step's last hour
    place = parse_place("bay", {"position": [0, 0], "shelter": {"N": 0.9}})
    fc = synthetic(gusts=[5, 5, 5, 20, 5, 5, 5], directions=[0, 0, 0, 180, 0, 0, 0])
    rows = build_rows(place, fc, None, None, step=3, hours=6, now=T0 + timedelta(minutes=30))
    assert rows[0]["time"] == T0.isoformat()
    assert rows[0]["effective_gust"] == pytest.approx(20)


def test_gust_and_rain_windows_use_preceding_hour() -> None:
    # value stamped T0 belongs to the hour before T0: not in the row starting at T0
    place = parse_place("open", {"position": [0, 0]})
    fc = synthetic(gusts=[30, 1, 1, 2, 1, 1, 1], directions=[0] * 7, precip=[0, 0, 0, 0, 0, 0, 0])
    rows = build_rows(place, fc, None, None, step=3, hours=6, now=T0)
    assert rows[0]["gust"]["m"] == 2


def test_single_model_path(forecast_json: dict[str, Any]) -> None:
    data = {
        "latitude": 1,
        "longitude": 2,
        "utc_offset_seconds": 0,
        "hourly": {
            "time": ["2026-01-01T00:00", "2026-01-01T01:00"],
            "wind_speed_10m": [3.0, 4.0],
            "wind_gusts_10m": [5.0, 6.0],
            "wind_direction_10m": [90.0, 90.0],
        },
    }
    fc = parse_forecast(data, models=["best_match"])
    place = parse_place("p", {"position": [1, 2]})
    rows = build_rows(place, fc, None, None, step=1, hours=2, now=T0)
    assert rows[0]["gust"] == {"best_match": 6.0}


@pytest.mark.parametrize(("hours", "days"), [(1, 2), (24, 2), (72, 4), (384, 16)])
def test_forecast_days(hours: int, days: int) -> None:
    assert forecast_days(hours) == days


@pytest.mark.parametrize("args", [["--step", "0"], ["--hours", "-5"], ["--hours", "385"]])
def test_bad_arguments_rejected(args: list[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["forecast", "1,2", *args])
    assert e.value.code == 2


def test_bad_config_is_an_error_not_a_traceback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "c.yaml"
    f.write_text("places:\n  x:\n    position: [100, 0]\n")
    assert cli.main(["--config", str(f), "forecast", "x"]) == 2
    assert "range" in capsys.readouterr().err


class FakeClient:
    def __init__(
        self, forecast_json: dict[str, Any], ensemble_json: dict[str, Any], marine_json: dict[str, Any]
    ) -> None:
        self.data = (forecast_json, ensemble_json, marine_json)

    def forecast(self, lat: float, lon: float, models: Any, days: int) -> Forecast:
        return parse_forecast(self.data[0])

    def ensemble(self, lat: float, lon: float, days: int) -> Any:
        return parse_ensemble(self.data[1])

    def marine(self, lat: float, lon: float, days: int) -> Any:
        return parse_marine(self.data[2])


def test_json_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    forecast_json: dict[str, Any],
    ensemble_json: dict[str, Any],
    marine_json: dict[str, Any],
) -> None:
    monkeypatch.setattr(cli, "Client", lambda: FakeClient(forecast_json, ensemble_json, marine_json))
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 11, 12, 6, tzinfo=timezone.utc))
    assert cli.main(["--config", "/nonexistent", "forecast", "60.39,5.32", "--json", "--hours", "6"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["units"]["wind"] == "m/s"
    assert out["grid_point"]["forecast"] == [60.5, 5.25]
    assert out["grid_point"]["marine"] is not None
    assert len(out["rows"]) == 2


def test_table_output_has_legend_and_marine_grid(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    forecast_json: dict[str, Any],
    ensemble_json: dict[str, Any],
    marine_json: dict[str, Any],
) -> None:
    monkeypatch.setattr(cli, "Client", lambda: FakeClient(forecast_json, ensemble_json, marine_json))
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 11, 12, 6, tzinfo=timezone.utc))
    assert cli.main(["--config", "/nonexistent", "forecast", "60.39,5.32", "--hours", "6"]) == 0
    out = capsys.readouterr().out
    assert "marine grid" in out
    assert "eff = " in out


ALERT_CONFIG = """
default_place: home
units: {wind: kn}
places:
  home: {position: [60.39, 5.32]}
alerts:
  calm:
    gust: 0
    task: enjoy it
    lead: 1h
  rain:
    rain: 0
    probability: 0.01
    event: false
    task: close hatches
  never:
    gust: 12 bft
"""


def _fake(
    monkeypatch: pytest.MonkeyPatch,
    forecast_json: dict[str, Any],
    ensemble_json: dict[str, Any],
    marine_json: dict[str, Any],
) -> None:
    monkeypatch.setattr(cli, "Client", lambda: FakeClient(forecast_json, ensemble_json, marine_json))
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 11, 12, 6, tzinfo=timezone.utc))


def test_alerts_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    forecast_json: dict[str, Any],
    ensemble_json: dict[str, Any],
    marine_json: dict[str, Any],
) -> None:
    _fake(monkeypatch, forecast_json, ensemble_json, marine_json)
    f = tmp_path / "c.yaml"
    f.write_text(ALERT_CONFIG)
    assert cli.main(["--config", str(f), "alerts", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    by_name = {a["name"]: a for a in out["alerts"]}
    assert set(by_name) == {"calm", "rain"}
    calm = by_name["calm"]
    assert calm["start"] == "2026-11-12T06:00:00+00:00"
    assert calm["task_due"] == "2026-11-12T05:00:00+00:00"
    assert " kn" in calm["summary"]
    assert by_name["rain"]["summary"] is None
    assert cli.main(["--config", str(f), "alerts"]) == 0
    text = capsys.readouterr().out
    assert "calm" in text
    assert "enjoy it" in text
    assert "never" not in text


def test_alerts_command_without_alerts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "c.yaml"
    f.write_text("places:\n  home: {position: [1, 2]}\n")
    assert cli.main(["--config", str(f), "alerts", "home"]) == 2
    assert "no alerts" in capsys.readouterr().err


def test_forecast_table_in_display_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    forecast_json: dict[str, Any],
    ensemble_json: dict[str, Any],
    marine_json: dict[str, Any],
) -> None:
    _fake(monkeypatch, forecast_json, ensemble_json, marine_json)
    f = tmp_path / "c.yaml"
    f.write_text("units: {wind: bft}\n")
    assert cli.main(["--config", str(f), "forecast", "60.39,5.32", "--hours", "6"]) == 0
    assert "in bft" in capsys.readouterr().out


def test_alerts_write_puts_them_in_the_calendar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    forecast_json: dict[str, Any],
    ensemble_json: dict[str, Any],
    marine_json: dict[str, Any],
) -> None:
    from contextlib import contextmanager

    from calendaring import Backend, Component, Workspace

    _fake(monkeypatch, forecast_json, ensemble_json, marine_json)
    backend = Backend.open(str(tmp_path))
    Workspace([backend])
    collection = backend.create_collection("w", components={Component.EVENT, Component.TASK})
    sections: list[str | None] = []

    @contextmanager
    def fake_open(section: str | None = None) -> Any:
        sections.append(section)
        yield collection

    monkeypatch.setattr(cli, "open_collection", fake_open)
    f = tmp_path / "c.yaml"
    f.write_text(ALERT_CONFIG)
    assert cli.main(["--config", str(f), "alerts", "--write"]) == 0
    out = capsys.readouterr().out
    assert "created" in out
    assert sections == [None]  # no calendar configured: the default section
    assert len(collection.events()) == 1
    assert {str(t.component["SUMMARY"]) for t in collection.tasks()} == {"enjoy it", "close hatches"}
    assert cli.main(["--config", str(f), "alerts", "--write"]) == 0
    assert "created" not in capsys.readouterr().out
