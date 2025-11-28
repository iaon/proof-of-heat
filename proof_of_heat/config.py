from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class MinerConfig(BaseModel):
    name: str = "whatsminer"
    cli_path: str = Field(
        default="whatsminer",
        description="Path to the WhatsMiner CLI tool that returns miner status.",
    )
    host: Optional[str] = Field(
        default=None,
        description="Optional host parameter passed to the CLI (if supported).",
    )


class AppConfig(BaseModel):
    target_temperature_c: float = Field(
        default=22.0, description="Desired ambient temperature in Celsius."
    )
    mode: str = Field(default="comfort", description="comfort | eco | off")
    data_dir: Path = Field(default=Path("./data"))
    miner: MinerConfig = Field(default_factory=MinerConfig)

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG = AppConfig()
