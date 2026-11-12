"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import load_config
from .openmeteo import DEFAULT_MODELS, UNITS, Client, Ensemble, Forecast, Marine, window_indices
from .places import Place, compass_name
from .probability import probability_exceeds

RAIN_THRESHOLD_MM = 0.1
MAX_DAYS = 16
MAX_HOURS = MAX_DAYS * 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _compass(direction: float | None) -> str:
    return "-" if direction is None else compass_name(direction)


def _fmt(value: float | None, spec: str = ".0f") -> str:
    if value is None:
        return "-"
    text = format(value, spec)
    return text[1:] if text.startswith("-") and float(text) == 0 else text  # no "-0"


def _at(series: Sequence[float | None] | None, i: int) -> float | None:
    if series is None or i >= len(series):
        return None
    return series[i]


def _window_max(series: Sequence[float | None] | None, idx: Sequence[int]) -> float | None:
    values = [v for v in (_at(series, i) for i in idx) if v is not None]
    return max(values) if values else None


def forecast_days(hours: int) -> int:
    """Days to request so that *hours* from now are covered, wherever in today "now" is."""
    return min(MAX_DAYS, -(-hours // 24) + 1)


def build_rows(
    place: Place,
    fc: Forecast,
    ens: Ensemble | None,
    marine: Marine | None,
    step: int,
    hours: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """One row per *step* hours from the current hour on.

    Wind, gusts, CAPE, humidity and waves are maxima over the step;
    temperature and directions are at its start.  The effective gust is the
    worst hour's gust after the place's shelter for that hour's direction.
    """
    if not fc.models:
        raise ValueError("the forecast holds no models")
    rows: list[dict[str, Any]] = []
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    primary = fc.models[next(iter(fc.models))]
    directions = primary.get("wind_direction_10m")
    t0 = current_hour
    end = current_hour + timedelta(hours=hours)
    while t0 < end:
        t1 = t0 + timedelta(hours=step)
        inst = window_indices(fc.times, t0, t1, "wind_speed_10m")
        gust_idx = window_indices(fc.times, t0, t1, "wind_gusts_10m")
        if not inst:
            break
        row: dict[str, Any] = {"time": t0.isoformat(), "wind": {}, "gust": {}}
        for model, series in fc.models.items():
            row["wind"][model] = _window_max(series.get("wind_speed_10m"), inst)
            row["gust"][model] = _window_max(series.get("wind_gusts_10m"), gust_idx)
        direction = _at(directions, inst[0])
        row["direction"] = direction
        row["shelter"] = place.shelter_at(direction) if direction is not None else None
        effective = [
            place.effective_wind(g, d)
            for g, d in ((_at(primary.get("wind_gusts_10m"), i), _at(directions, i)) for i in gust_idx)
            if g is not None and d is not None
        ]
        row["effective_gust"] = max(effective) if effective else None
        row["cape"] = _window_max(primary.get("cape"), inst)
        row["temperature"] = _at(primary.get("temperature_2m"), inst[0])
        row["humidity"] = _window_max(primary.get("relative_humidity_2m"), inst)
        row["p_rain"] = probability_exceeds(ens, "precipitation", RAIN_THRESHOLD_MM, t0, t1) if ens else None
        if marine is not None:
            mi = window_indices(marine.times, t0, t1, "wave_height")
            row["wave_height"] = _window_max(marine.values.get("wave_height"), mi)
            row["swell_height"] = _window_max(marine.values.get("swell_wave_height"), mi)
            row["swell_direction"] = _at(marine.values.get("swell_wave_direction"), mi[0]) if mi else None
        rows.append(row)
        t0 = t1
    return rows


def _place_json(place: Place) -> dict[str, Any]:
    data = asdict(place)
    data["shelter"] = {compass_name(b): v for b, v in place.shelter.items()}
    data["swell_exposure"] = [f"{s.start:g}-{s.end:g}" for s in place.swell_exposure]
    return data


def print_table(place: Place, fc: Forecast, marine: Marine | None, rows: list[dict[str, Any]]) -> None:
    models = list(fc.models)
    marine_grid = f"{marine.grid_point[0]:.3f},{marine.grid_point[1]:.3f}" if marine else "-"
    print(
        f"{place.name}: forecast point {place.forecast_point[0]:.3f},{place.forecast_point[1]:.3f};"
        f" forecast grid {fc.grid_point[0]:.3f},{fc.grid_point[1]:.3f}; marine grid {marine_grid}"
    )
    print(
        f"wind/gust per model ({'/'.join(models)}) in m/s; eff = worst gust after this place's shelter;"
        f" rain% = ensemble chance of > {RAIN_THRESHOLD_MM} mm"
    )
    w = 3 * len(models)
    print(
        f"{'UTC':<11} {'dir':>3} {'wind':>{w}} {'gust':>{w}} {'eff':>3} {'cape':>4} {'T':>3} {'RH':>3}"
        f" {'rain%':>5} {'wave':>4} {'swell':>9}"
    )
    for r in rows:
        t = datetime.fromisoformat(r["time"]).strftime("%a %d %H")
        wind = "/".join(_fmt(r["wind"][m]) for m in models)
        gust = "/".join(_fmt(r["gust"][m]) for m in models)
        p_rain = "-" if r["p_rain"] is None else f"{100 * r['p_rain']:.0f}"
        swell = f"{_fmt(r.get('swell_height'), '.1f')} {_compass(r.get('swell_direction'))}"
        print(
            f"{t:<11} {_compass(r['direction']):>3} {wind:>{w}} {gust:>{w}}"
            f" {_fmt(r['effective_gust']):>3} {_fmt(r['cape']):>4} {_fmt(r['temperature']):>3} {_fmt(r['humidity']):>3}"
            f" {p_rain:>5} {_fmt(r.get('wave_height'), '.1f'):>4} {swell:>9}"
        )


def cmd_forecast(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        spec = args.place or config.default_place
        if spec is None:
            print("no place given and no default_place configured", file=sys.stderr)
            return 2
        place = config.resolve_place(spec)
    except (KeyError, ValueError) as e:
        print(e.args[0] if e.args else e, file=sys.stderr)
        return 2
    client = Client()
    lat, lon = place.forecast_point
    days = forecast_days(args.hours)
    models = args.models.split(",") if args.models else list(DEFAULT_MODELS)
    try:
        fc = client.forecast(lat, lon, models=models, days=days)
        ens = client.ensemble(lat, lon, days=days)
        marine = None if args.no_marine else client.marine(lat, lon, days=days)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    rows = build_rows(place, fc, ens, marine, args.step, args.hours, _now())
    if args.json:
        output = {
            "place": _place_json(place),
            "grid_point": {"forecast": fc.grid_point, "marine": marine.grid_point if marine else None},
            "units": UNITS,
            "rows": rows,
        }
        json.dump(output, sys.stdout, indent=1)
        print()
    else:
        print_table(place, fc, marine, rows)
    return 0


def _hours(text: str) -> int:
    value = int(text)
    if not 1 <= value <= MAX_HOURS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_HOURS}")
    return value


def _step(text: str) -> int:
    value = int(text)
    if not 1 <= value <= 24:
        raise argparse.ArgumentTypeError("must be between 1 and 24")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weather-planner", description=__doc__)
    parser.add_argument("--config", help="configuration file (default: ~/.config/weather-planner/config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)
    fc = sub.add_parser("forecast", help="show the forecast for a place")
    fc.add_argument("place", nargs="?", help="a configured place, or LAT,LON")
    fc.add_argument("--hours", type=_hours, default=72, help=f"how far ahead, 1-{MAX_HOURS} (default 72)")
    fc.add_argument("--step", type=_step, default=3, help="hours per row (default 3)")
    fc.add_argument("--models", help=f"comma-separated Open-Meteo models (default {','.join(DEFAULT_MODELS)})")
    fc.add_argument("--no-marine", action="store_true", help="skip waves and swell")
    fc.add_argument("--json", action="store_true", help="machine-readable output")
    fc.set_defaults(func=cmd_forecast)
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
