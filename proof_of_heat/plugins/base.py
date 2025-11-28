from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class Miner(ABC):
    """Base protocol for miner integrations."""

    name: str

    @abstractmethod
    def fetch_status(self) -> Dict[str, Any]:
        """Return a dictionary with miner telemetry."""

    @abstractmethod
    def set_power_limit(self, watts: int) -> Dict[str, Any]:
        """Adjust miner power output (watts)."""

    @abstractmethod
    def stop(self) -> Dict[str, Any]:
        """Stop the miner."""

    @abstractmethod
    def start(self) -> Dict[str, Any]:
        """Start the miner."""


def human_readable_mode(mode: str) -> str:
    mapping = {
        "comfort": "Comfort (max performance)",
        "eco": "Eco (reduced power)",
        "off": "Off",
    }
    return mapping.get(mode, mode)
