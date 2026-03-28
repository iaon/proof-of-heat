from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

try:  # pragma: no cover - optional import guard
    from whatsminer_cli import DEFAULT_PORT, DEFAULT_TIMEOUT
except Exception:  # pragma: no cover - defensive fallback
    DEFAULT_PORT = 4433
    DEFAULT_TIMEOUT = 10


class MinerConfig(BaseModel):
    name: str = "whatsminer"
    host: Optional[str] = Field(default=None, description="Miner host address.")
    port: int = Field(default=DEFAULT_PORT, description="Miner API port.")
    login: Optional[str] = Field(default=None, description="Miner login account.")
    password: Optional[str] = Field(default=None, description="Miner login password.")
    timeout: int = Field(default=DEFAULT_TIMEOUT, description="API timeout (seconds).")


class AppConfig(BaseModel):
    target_temperature_c: float = Field(
        default=22.0, description="Desired ambient temperature in Celsius."
    )
    mode: str = Field(default="comfort", description="comfort | eco | off")
    heating_curve: str = Field(
        default="standard", description="Selected heating curve profile."
    )
    data_dir: Path = Field(default=Path("./data"))
    miner: MinerConfig = Field(default_factory=MinerConfig)

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG = AppConfig()
