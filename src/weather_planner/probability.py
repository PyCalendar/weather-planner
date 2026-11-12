"""Probabilities from ensemble forecasts."""

from __future__ import annotations

from datetime import datetime

from .openmeteo import ACCUMULATED, Ensemble, window_indices


def probability_exceeds(
    ensemble: Ensemble,
    variable: str,
    threshold: float,
    start: datetime,
    end: datetime,
) -> float | None:
    """Share of ensemble members exceeding *threshold* during [start, end).

    This answers "will it rain at all while the varnish cures?", not "how
    much rain on average?".  Accumulated variables (precipitation, snow) are
    summed over the window per member before comparing, since coarse
    ensembles spread a 3- or 6-hour sum evenly over the hours, and a per-hour
    threshold would then miss light rain.  Others count if any hour exceeds
    it.  Members without data for the window are left out; ``None`` means no
    member has any data there.
    """
    idx = window_indices(ensemble.times, start, end, variable)
    hits = total = 0
    for member in ensemble.members[variable]:
        window = [v for v in (member[i] for i in idx) if v is not None]
        if not window:
            continue
        total += 1
        value = sum(window) if variable in ACCUMULATED else max(window)
        if value > threshold:
            hits += 1
    return hits / total if total else None
