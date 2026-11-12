"""Weather alerts and planning from free forecast data, delivered through your calendar."""

from __future__ import annotations

try:
    from ._version import __version__
except ImportError:  # pragma: no cover - running from a checkout without a build
    __version__ = "0.0.0"

__all__ = ["__version__"]
