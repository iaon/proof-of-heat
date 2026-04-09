from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

BASE_DIR = Path(__file__).resolve().parents[1]
SETTINGS_SCHEMA_FILE = BASE_DIR / "conf" / "settings.schema.json"
TIME_OF_DAY_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class SettingsValidationError(ValueError):
    def __init__(self, message: str, *, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class SettingsSchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _validate_timezone_name(value: str) -> str:
    _validate_non_blank(value)
    ZoneInfo(value)
    return value


def _format_error_path(loc: tuple[Any, ...]) -> str:
    path = ""
    for part in loc:
        if part == "__root__":
            continue
        if isinstance(part, int):
            if path:
                path += f"[{part}]"
            else:
                path = f"[{part}]"
            continue
        part_str = str(part)
        if not path:
            path = part_str
        else:
            path += f".{part_str}"
    return path or "config"


def format_settings_validation_error(exc: ValidationError) -> str:
    lines = ["Settings validation failed:"]
    for error in exc.errors():
        path = _format_error_path(tuple(error.get("loc", ())))
        lines.append(f"- {path}: {error.get('msg', 'Invalid value')}")
    return "\n".join(lines)


class LocationSettings(SettingsSchemaModel):
    name: str
    latitude: float
    longitude: float
    timezone: str
    altitude_m: int | None = None

    @field_validator("name", "timezone")
    @classmethod
    def _validate_non_blank_fields(cls, value: str) -> str:
        return _validate_non_blank(value)


class ZontApiIntegrationSettings(SettingsSchemaModel):
    id: int | str
    headers: dict[str, str]
    login: str
    password: str

    @field_validator("login", "password")
    @classmethod
    def _validate_credentials(cls, value: str) -> str:
        return _validate_non_blank(value)

    @model_validator(mode="after")
    def _validate_headers(self) -> "ZontApiIntegrationSettings":
        zont_client = self.headers.get("X-ZONT-Client")
        if not isinstance(zont_client, str) or not zont_client.strip():
            raise ValueError("headers.X-ZONT-Client is required")
        return self


class IntegrationsSettings(SettingsSchemaModel):
    zont_api: list[ZontApiIntegrationSettings] = Field(default_factory=list)


class WeatherDeviceSettings(SettingsSchemaModel):
    device_id: int
    type: str | None = None

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_non_blank(value)


class ZontDeviceSettings(SettingsSchemaModel):
    integration_id: int | str | None = None
    device_id: int | str
    serial: str
    refresh_interval: int | None = Field(default=None, gt=0)

    @field_validator("serial")
    @classmethod
    def _validate_serial(cls, value: str) -> str:
        return _validate_non_blank(value)

    @field_validator("device_id")
    @classmethod
    def _validate_device_id(cls, value: int | str) -> int | str:
        if isinstance(value, str):
            return _validate_non_blank(value)
        return value


class WhatsminerDeviceSettings(SettingsSchemaModel):
    device_id: int | str
    login: str
    password: str
    host: str
    port: int | None = Field(default=None, gt=0)
    timeout: int | None = Field(default=None, gt=0)
    max_power: int | None = Field(default=None, gt=0)
    min_power: int | None = Field(default=None, ge=0)

    @field_validator("login", "password", "host")
    @classmethod
    def _validate_non_blank_fields(cls, value: str) -> str:
        return _validate_non_blank(value)

    @field_validator("device_id")
    @classmethod
    def _validate_device_id(cls, value: int | str) -> int | str:
        if isinstance(value, str):
            return _validate_non_blank(value)
        return value

    @model_validator(mode="after")
    def _validate_power_bounds(self) -> "WhatsminerDeviceSettings":
        if (
            self.max_power is not None
            and self.min_power is not None
            and self.min_power > self.max_power
        ):
            raise ValueError("min_power must be less than or equal to max_power")
        return self


class DevicesSettings(SettingsSchemaModel):
    refresh_interval: int | None = Field(default=None, gt=0)
    open_meteo: list[WeatherDeviceSettings] = Field(default_factory=list)
    met_no: list[WeatherDeviceSettings] = Field(default_factory=list)
    zont: list[ZontDeviceSettings] = Field(default_factory=list)
    whatsminer: list[WhatsminerDeviceSettings] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_weather_device_ids(self) -> "DevicesSettings":
        seen: dict[int, str] = {}
        for device_type, devices in (
            ("open_meteo", self.open_meteo),
            ("met_no", self.met_no),
        ):
            for device in devices:
                current = seen.get(device.device_id)
                if current is not None:
                    raise ValueError(
                        f"duplicate weather device_id {device.device_id} in {current} and {device_type}"
                    )
                seen[device.device_id] = device_type
        return self


class RawEventsRetentionSettings(SettingsSchemaModel):
    enabled: bool = True
    retention_seconds: int | None = Field(default=None, gt=0)
    interval_seconds: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_enabled_block(self) -> "RawEventsRetentionSettings":
        if self.enabled is False:
            return self
        if self.retention_seconds is None or self.interval_seconds is None:
            raise ValueError("enabled raw_events retention requires retention_seconds and interval_seconds")
        return self


class MetricRollupSettings(SettingsSchemaModel):
    resolution_seconds: int = Field(gt=0)
    retention_seconds: int = Field(gt=0)
    sample: Literal["last", "first", "any"] = "last"


class MetricsRetentionSettings(SettingsSchemaModel):
    enabled: bool = True
    interval_seconds: int | None = Field(default=None, gt=0)
    raw_retention_seconds: int | None = Field(default=None, gt=0)
    rollups: list[MetricRollupSettings] | None = None

    @model_validator(mode="after")
    def _validate_enabled_block(self) -> "MetricsRetentionSettings":
        if self.enabled is False:
            return self
        if self.interval_seconds is None or self.raw_retention_seconds is None:
            raise ValueError(
                "enabled metrics retention requires interval_seconds and raw_retention_seconds"
            )
        if not self.rollups:
            raise ValueError("enabled metrics retention requires a non-empty rollups list")
        return self


class DatabaseRetentionSettings(SettingsSchemaModel):
    raw_events: RawEventsRetentionSettings | None = None
    metrics: MetricsRetentionSettings | None = None


class VacuumMaintenanceSettings(SettingsSchemaModel):
    enabled: bool = True
    interval_seconds: int = Field(gt=0)
    min_free_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    min_reclaimable_mb: float | None = Field(default=None, ge=0.0)


class DatabaseMaintenanceSettings(SettingsSchemaModel):
    vacuum: VacuumMaintenanceSettings | None = None


class DatabaseSettings(SettingsSchemaModel):
    retention: DatabaseRetentionSettings | None = None
    maintenance: DatabaseMaintenanceSettings | None = None


class ControlInputSourceSettings(SettingsSchemaModel):
    device_type: str
    device_id: int | str
    metric: str
    correction: float | None = None

    @field_validator("device_type", "metric")
    @classmethod
    def _validate_non_blank_fields(cls, value: str) -> str:
        return _validate_non_blank(value)

    @field_validator("device_id")
    @classmethod
    def _validate_device_id(cls, value: int | str) -> int | str:
        if isinstance(value, str):
            return _validate_non_blank(value)
        return value


class ControlInputSettings(SettingsSchemaModel):
    select: Literal["highest_priority_available", "sum_all_available"]
    sources: list[ControlInputSourceSettings] = Field(default_factory=list)
    default: float | None = None

    @model_validator(mode="after")
    def _validate_default_usage(self) -> "ControlInputSettings":
        if self.select != "sum_all_available" and self.default is not None:
            raise ValueError("default is supported only for sum_all_available inputs")
        return self


class ControlInputsSettings(SettingsSchemaModel):
    max_age_seconds: int | None = Field(default=None, ge=0)
    indoor_temp: ControlInputSettings | None = None
    outdoor_temp: ControlInputSettings | None = None
    supply_temp: ControlInputSettings | None = None
    power: ControlInputSettings | None = None


class FixedPowerHeatingParams(SettingsSchemaModel):
    power_w: int = Field(gt=0)


class FixedSupplyTempHeatingParams(SettingsSchemaModel):
    target_supply_temp_c: float
    tolerance_c: float | None = Field(default=None, ge=0.0)
    correction: float | None = None


class RoomTargetHeatingParams(SettingsSchemaModel):
    target_room_temp_c: float
    tolerance_c: float | None = Field(default=None, ge=0.0)
    correction: float | None = None


class HeatingModeSettings(SettingsSchemaModel):
    enabled: bool = True
    type: Literal["fixed_power", "fixed_supply_temp", "room_target"] | None = None
    params: (
        FixedPowerHeatingParams
        | FixedSupplyTempHeatingParams
        | RoomTargetHeatingParams
        | None
    ) = None
    target_room_temp_c: float | None = None

    @model_validator(mode="after")
    def _validate_mode(self) -> "HeatingModeSettings":
        if self.enabled is False:
            return self
        if self.type is None:
            raise ValueError("enabled heating_mode requires type")
        if self.type == "fixed_power":
            if not isinstance(self.params, FixedPowerHeatingParams):
                raise ValueError("fixed_power mode requires params.power_w")
            return self
        if self.type == "fixed_supply_temp":
            if not isinstance(self.params, FixedSupplyTempHeatingParams):
                raise ValueError("fixed_supply_temp mode requires supply temperature params")
            return self
        if isinstance(self.params, RoomTargetHeatingParams):
            return self
        if self.target_room_temp_c is None:
            raise ValueError("room_target mode requires params.target_room_temp_c or target_room_temp_c")
        return self


class HeatingCurveSettings(SettingsSchemaModel):
    slope: float | None = Field(default=None, ge=0.0)
    exponent: float | None = Field(default=None, ge=0.0)
    offset: float | None = None
    force_max_power_below_target: bool | None = None
    force_max_power_margin_c: float | None = Field(default=None, ge=0.0)
    min_supply_temp_c: float | None = None
    max_supply_temp_c: float | None = None

    @model_validator(mode="after")
    def _validate_bounds(self) -> "HeatingCurveSettings":
        if (
            self.min_supply_temp_c is not None
            and self.max_supply_temp_c is not None
            and self.max_supply_temp_c < self.min_supply_temp_c
        ):
            raise ValueError("max_supply_temp_c must be greater than or equal to min_supply_temp_c")
        return self


class CurrenciesSettings(SettingsSchemaModel):
    crypto: str
    fiat: str

    @field_validator("crypto", "fiat")
    @classmethod
    def _validate_codes(cls, value: str) -> str:
        return _validate_non_blank(value)


class ExchangeRateIntegrationsSettings(SettingsSchemaModel):
    crypto_usd: Literal["mempool_space"]
    usd_fiat: Literal["cbr"]


class ExchangeRateSettings(SettingsSchemaModel):
    integrations: ExchangeRateIntegrationsSettings
    refresh_interval: int = Field(gt=0)
    stale_after: int = Field(ge=0)
    timeout_s: float | None = Field(default=None, gt=0.0)


class HashpriceSettings(SettingsSchemaModel):
    integration: Literal["mempool_space"]
    reward_stats_blocks: int = Field(gt=0)
    hashrate_window: str
    refresh_interval: int = Field(gt=0)
    stale_after: int = Field(ge=0)
    timeout_s: float | None = Field(default=None, gt=0.0)

    @field_validator("hashrate_window")
    @classmethod
    def _validate_hashrate_window(cls, value: str) -> str:
        return _validate_non_blank(value)


class ElectricityTariffSettings(SettingsSchemaModel):
    start: str = Field(pattern=TIME_OF_DAY_PATTERN.pattern)
    price_per_kwh: float = Field(ge=0.0)


class ElectricitySettings(SettingsSchemaModel):
    mode: Literal["fixed", "time_of_day"] | None = None
    price_per_kwh: float | None = Field(default=None, ge=0.0)
    timezone: str | None = None
    tariffs: list[ElectricityTariffSettings] | None = None

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_non_blank(value)

    def resolved_mode(self) -> str:
        if self.mode is not None:
            return self.mode
        return "time_of_day" if self.tariffs is not None else "fixed"

    @model_validator(mode="after")
    def _validate_mode(self) -> "ElectricitySettings":
        if self.resolved_mode() == "fixed":
            if self.tariffs is not None:
                raise ValueError("fixed electricity mode does not allow tariffs")
            if self.price_per_kwh is None:
                raise ValueError("fixed electricity mode requires price_per_kwh")
            return self

        if self.price_per_kwh is not None:
            raise ValueError("time_of_day electricity mode does not allow top-level price_per_kwh")
        if not self.tariffs:
            raise ValueError("time_of_day electricity mode requires a non-empty tariffs list")

        seen_starts: set[str] = set()
        for tariff in self.tariffs:
            if tariff.start in seen_starts:
                raise ValueError(f"duplicate electricity tariff start: {tariff.start}")
            seen_starts.add(tariff.start)
        return self


class EconomicsSettings(SettingsSchemaModel):
    enabled: bool = True
    currencies: CurrenciesSettings | None = None
    exchange_rate: ExchangeRateSettings | None = None
    hashprice: HashpriceSettings | None = None
    electricity: ElectricitySettings | None = None

    @model_validator(mode="after")
    def _validate_enabled_sections(self) -> "EconomicsSettings":
        if self.enabled is False:
            return self
        missing: list[str] = []
        if self.currencies is None:
            missing.append("currencies")
        if self.exchange_rate is None:
            missing.append("exchange_rate")
        if self.hashprice is None:
            missing.append("hashprice")
        if self.electricity is None:
            missing.append("electricity")
        if missing:
            raise ValueError(f"enabled economics requires {', '.join(missing)}")
        return self


class SettingsSchema(SettingsSchemaModel):
    location: LocationSettings | None = None
    integrations: IntegrationsSettings | None = None
    devices: DevicesSettings | None = None
    database: DatabaseSettings | None = None
    control_inputs: ControlInputsSettings | None = None
    heating_mode: HeatingModeSettings | None = None
    heating_curve: HeatingCurveSettings | None = None
    economics: EconomicsSettings | None = None

    @model_validator(mode="after")
    def _validate_cross_references(self) -> "SettingsSchema":
        integrations = self.integrations.zont_api if self.integrations is not None else []
        integration_ids = {str(integration.id) for integration in integrations}

        if self.devices is not None:
            if self.devices.zont and not integrations:
                raise ValueError("devices.zont requires at least one integrations.zont_api entry")
            for device in self.devices.zont:
                if device.integration_id is None:
                    continue
                if str(device.integration_id) not in integration_ids:
                    raise ValueError(
                        f"devices.zont integration_id {device.integration_id!r} does not match any integrations.zont_api id"
                    )

        if (
            self.economics is not None
            and self.economics.enabled is not False
            and self.economics.electricity is not None
            and self.economics.electricity.resolved_mode() == "time_of_day"
        ):
            timezone_name = self.economics.electricity.timezone
            if timezone_name is None and self.location is not None:
                timezone_name = self.location.timezone
            if timezone_name is None:
                raise ValueError(
                    "economics.electricity.time_of_day requires electricity.timezone or location.timezone"
                )
            try:
                _validate_timezone_name(timezone_name)
            except Exception as exc:
                raise ValueError(
                    f"economics.electricity timezone is invalid: {timezone_name}"
                ) from exc

        return self


def validate_settings_data(data: dict[str, Any]) -> None:
    try:
        SettingsSchema.model_validate(data)
    except ValidationError as exc:
        raise SettingsValidationError(
            format_settings_validation_error(exc),
            errors=exc.errors(),
        ) from exc


def build_settings_json_schema() -> dict[str, Any]:
    return SettingsSchema.model_json_schema()


def write_settings_json_schema(path: Path = SETTINGS_SCHEMA_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_settings_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    write_settings_json_schema()
