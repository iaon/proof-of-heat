import json
from pathlib import Path

import pytest

from proof_of_heat.config import (
    AppConfig,
    FixedPowerHeatingParams,
    FixedSupplyTempHeatingParams,
    HeatingModeConfig,
    MinerConfig,
    RoomTargetHeatingParams,
)
from proof_of_heat.settings import load_default_settings_yaml, parse_settings_yaml, render_settings_yaml
from proof_of_heat.settings_schema import SettingsValidationError, build_settings_json_schema


def test_miner_config_defaults_min_power():
    config = MinerConfig()

    assert config.min_power == 1000


def test_miner_config_defaults_max_power_to_none():
    config = MinerConfig()

    assert config.max_power is None


def test_default_settings_yaml_includes_whatsminer_min_power():
    parsed = parse_settings_yaml(load_default_settings_yaml())

    whatsminer_devices = parsed["devices"]["whatsminer"]
    assert whatsminer_devices[0]["min_power"] == 1000


def test_default_settings_yaml_includes_whatsminer_max_power():
    parsed = parse_settings_yaml(load_default_settings_yaml())

    whatsminer_devices = parsed["devices"]["whatsminer"]
    assert whatsminer_devices[0]["max_power"] is None


def test_heating_mode_defaults_to_room_target():
    config = HeatingModeConfig()

    assert config.enabled is True
    assert config.type == "room_target"
    assert isinstance(config.params, RoomTargetHeatingParams)
    assert config.params.target_room_temp_c == 22.0


def test_heating_mode_supports_fixed_power():
    config = HeatingModeConfig(
        type="fixed_power",
        params=FixedPowerHeatingParams(power_w=3200),
    )

    assert config.type == "fixed_power"
    assert config.params.power_w == 3200


def test_heating_mode_supports_fixed_supply_temp():
    config = HeatingModeConfig(
        type="fixed_supply_temp",
        params=FixedSupplyTempHeatingParams(target_supply_temp_c=42.0),
    )

    assert config.type == "fixed_supply_temp"
    assert config.params.target_supply_temp_c == 42.0
    assert config.params.tolerance_c == 1.0
    assert config.params.correction == 0.0


def test_app_config_includes_heating_mode():
    config = AppConfig()

    assert config.heating_mode.type == "room_target"
    assert isinstance(config.heating_mode.params, RoomTargetHeatingParams)


def test_default_settings_yaml_includes_heating_mode():
    parsed = parse_settings_yaml(load_default_settings_yaml())

    assert parsed["heating_mode"] == {
        "enabled": True,
        "type": "room_target",
        "params": {
            "target_room_temp_c": 22.0,
        },
    }


def test_default_settings_yaml_includes_database_maintenance_settings():
    parsed = parse_settings_yaml(load_default_settings_yaml())

    assert parsed["database"] == {
        "retention": {
            "raw_events": {
                "enabled": True,
                "retention_seconds": 86400,
                "interval_seconds": 3600,
            },
            "metrics": {
                "enabled": True,
                "interval_seconds": 3600,
                "raw_retention_seconds": 604800,
                "rollups": [
                    {
                        "resolution_seconds": 600,
                        "retention_seconds": 15552000,
                        "sample": "last",
                    }
                ],
            }
        },
        "maintenance": {
            "vacuum": {
                "enabled": False,
                "interval_seconds": 86400,
                "min_free_ratio": 0.25,
                "min_reclaimable_mb": 64.0,
            }
        },
    }


def test_default_settings_yaml_includes_economics():
    parsed = parse_settings_yaml(load_default_settings_yaml())

    assert parsed["economics"] == {
        "enabled": True,
        "currencies": {
            "crypto": "BTC",
            "fiat": "RUB",
        },
        "exchange_rate": {
            "integrations": {
                "crypto_usd": "mempool_space",
                "usd_fiat": "cbr",
            },
            "refresh_interval": 3600,
            "stale_after": 7200,
        },
        "hashprice": {
            "integration": "mempool_space",
            "reward_stats_blocks": 144,
            "hashrate_window": "1m",
            "refresh_interval": 3600,
            "stale_after": 7200,
        },
        "electricity": {
            "mode": "time_of_day",
            "tariffs": [
                {
                    "start": "07:00",
                    "price_per_kwh": 8.0,
                },
                {
                    "start": "23:00",
                    "price_per_kwh": 5.0,
                },
            ],
        },
    }


def test_parse_settings_yaml_preserves_unquoted_time_of_day_values_as_strings():
    parsed = parse_settings_yaml(
        """
location:
  name: "Moscow"
  latitude: 55.7558
  longitude: 37.6173
  timezone: "Europe/Moscow"
economics:
  enabled: true
  currencies:
    crypto: BTC
    fiat: RUB
  exchange_rate:
    integrations:
      crypto_usd: mempool_space
      usd_fiat: cbr
    refresh_interval: 3600
    stale_after: 7200
  hashprice:
    integration: mempool_space
    reward_stats_blocks: 144
    hashrate_window: 1m
    refresh_interval: 3600
    stale_after: 7200
  electricity:
    mode: time_of_day
    tariffs:
      - start: 07:00
        price_per_kwh: 8.0
      - start: 23:00
        price_per_kwh: 5.0
"""
    )

    tariffs = parsed["economics"]["electricity"]["tariffs"]
    assert tariffs[0]["start"] == "07:00"
    assert tariffs[1]["start"] == "23:00"


def test_render_settings_yaml_quotes_time_of_day_values():
    rendered = render_settings_yaml(
        {
            "economics": {
                "electricity": {
                    "mode": "time_of_day",
                    "tariffs": [
                        {"start": "07:00", "price_per_kwh": 8.0},
                        {"start": "23:00", "price_per_kwh": 5.0},
                    ],
                }
            }
        }
    )

    assert "start: '07:00'" in rendered
    assert "start: '23:00'" in rendered


def test_example_settings_yaml_is_valid():
    raw_yaml = load_default_settings_yaml()

    parsed = parse_settings_yaml(raw_yaml)

    assert parsed["devices"]["met_no"][0]["device_id"] == 2001
    assert parsed["devices"]["met_no"][0]["refresh_interval"] == 180
    assert parsed["devices"]["open_meteo"][0]["refresh_interval"] == 180
    assert parsed["devices"]["whatsminer"][0]["device_id"] == "miner01"
    assert parsed["devices"]["whatsminer"][0]["refresh_interval"] == 60


def test_parse_settings_yaml_accepts_per_device_refresh_interval_for_polled_devices():
    parsed = parse_settings_yaml(
        """
integrations:
  zont_api:
    - id: 1
      headers:
        X-ZONT-Client: "client"
      login: "login"
      password: "password"
devices:
  refresh_interval: 30
  open_meteo:
    - device_id: 1001
      type: "virtual"
      refresh_interval: 180
  met_no:
    - device_id: 2001
      type: "virtual"
      refresh_interval: 240
  zont:
    - integration_id: 1
      device_id: 12000
      serial: "0000000000"
      refresh_interval: 300
  whatsminer:
    - device_id: "miner01"
      host: "example.com"
      login: "login"
      password: "password"
      refresh_interval: 90
"""
    )

    assert parsed["devices"]["open_meteo"][0]["refresh_interval"] == 180
    assert parsed["devices"]["met_no"][0]["refresh_interval"] == 240
    assert parsed["devices"]["zont"][0]["refresh_interval"] == 300
    assert parsed["devices"]["whatsminer"][0]["refresh_interval"] == 90


def test_parse_settings_yaml_rejects_unknown_top_level_fields():
    with pytest.raises(SettingsValidationError) as exc_info:
        parse_settings_yaml("unexpected_section:\n  enabled: true\n")

    assert "unexpected_section" in str(exc_info.value)
    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_parse_settings_yaml_rejects_unknown_zont_integration_ids():
    with pytest.raises(SettingsValidationError) as exc_info:
        parse_settings_yaml(
            """
integrations:
  zont_api:
    - id: 1
      headers:
        X-ZONT-Client: "client"
      login: "login"
      password: "password"
devices:
  zont:
    - integration_id: 2
      device_id: 12000
      serial: "0000000000"
"""
        )

    assert "devices.zont integration_id 2" in str(exc_info.value)


def test_parse_settings_yaml_requires_timezone_for_time_of_day_tariffs():
    with pytest.raises(SettingsValidationError) as exc_info:
        parse_settings_yaml(
            """
economics:
  enabled: true
  currencies:
    crypto: BTC
    fiat: RUB
  exchange_rate:
    integrations:
      crypto_usd: mempool_space
      usd_fiat: cbr
    refresh_interval: 3600
    stale_after: 7200
  hashprice:
    integration: mempool_space
    reward_stats_blocks: 144
    hashrate_window: 1m
    refresh_interval: 3600
    stale_after: 7200
  electricity:
    mode: time_of_day
    tariffs:
      - start: "07:00"
        price_per_kwh: 8.0
"""
        )

    assert "economics.electricity.time_of_day requires electricity.timezone or location.timezone" in str(
        exc_info.value
    )


def test_checked_in_settings_json_schema_matches_generated_schema():
    schema_path = Path(__file__).resolve().parents[1] / "conf" / "settings.schema.json"

    assert json.loads(schema_path.read_text(encoding="utf-8")) == build_settings_json_schema()
