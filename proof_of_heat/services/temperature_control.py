from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from proof_of_heat.config import AppConfig
from proof_of_heat.plugins.base import Miner


@dataclass
class Snapshot:
    timestamp: datetime
    indoor_temp_c: float
    target_temp_c: float
    mode: str
    miner_status: Dict[str, Any]


@dataclass
class TemperatureController:
    config: AppConfig
    miner: Miner
    history_file: Path | None = None
    snapshots: List[Snapshot] = field(default_factory=list)

    def record_snapshot(self, indoor_temp_c: float, miner_status: Dict[str, Any]) -> Snapshot:
        snapshot = Snapshot(
            timestamp=datetime.utcnow(),
            indoor_temp_c=indoor_temp_c,
            target_temp_c=self.config.target_temperature_c,
            mode=self.config.mode,
            miner_status=miner_status,
        )
        self.snapshots.append(snapshot)
        self.persist()
        return snapshot

    def persist(self) -> None:
        if not self.history_file:
            return
        lines = []
        for snap in self.snapshots:
            lines.append(
                f"{snap.timestamp.isoformat()},{snap.indoor_temp_c},{snap.target_temp_c},{snap.mode},{snap.miner_status}\n"
            )
        self.history_file.write_text("".join(lines))

    def set_target(self, temp_c: float) -> None:
        self.config.target_temperature_c = temp_c
        self.persist()

    def set_mode(self, mode: str) -> None:
        self.config.mode = mode
        self.persist()
