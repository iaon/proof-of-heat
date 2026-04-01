from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

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
    max_power: Optional[int] = Field(
        default=None,
        description="Optional miner power ceiling in watts reserved for future control logic.",
    )
    min_power: int = Field(
        default=1000,
        description="Minimum stable miner power in watts; lower targets should stop the miner instead.",
    )


class FixedPowerHeatingParams(BaseModel):
    power_w: int = Field(description="Fixed miner power in watts.")


class FixedSupplyTempHeatingParams(BaseModel):
    target_supply_temp_c: float = Field(description="Target supply temperature in Celsius.")
    tolerance_c: float = Field(default=1.0, description="Allowed supply temperature deviation in Celsius.")
    correction: float = Field(
        default=0.0,
        description="Additional correction applied to the resolved supply temperature input.",
    )


class RoomTargetHeatingParams(BaseModel):
    target_room_temp_c: float = Field(description="Target indoor temperature in Celsius.")


class HeatingModeConfig(BaseModel):
    enabled: bool = Field(default=True, description="Whether automatic heating logic is enabled.")
    type: Literal["fixed_power", "fixed_supply_temp", "room_target"] = Field(
        default="room_target",
        description="Heating control mode.",
    )
    params: FixedPowerHeatingParams | FixedSupplyTempHeatingParams | RoomTargetHeatingParams = Field(
        default_factory=lambda: RoomTargetHeatingParams(target_room_temp_c=22.0),
        description="Mode-specific parameters.",
    )


class AppConfig(BaseModel):
    target_temperature_c: float = Field(
        default=22.0, description="Desired ambient temperature in Celsius."
    )
    mode: str = Field(default="comfort", description="comfort | eco | off")
    data_dir: Path = Field(default=Path("./data"))
    miner: MinerConfig = Field(default_factory=MinerConfig)
    heating_mode: HeatingModeConfig = Field(default_factory=HeatingModeConfig)

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG = AppConfig()
