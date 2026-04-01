from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSample:
    name: str
    value: float
    unit: str | None = None
