from proof_of_heat.config import (
    AppConfig,
    FixedPowerHeatingParams,
    FixedSupplyTempHeatingParams,
    HeatingModeConfig,
    MinerConfig,
    RoomTargetHeatingParams,
)
from proof_of_heat.settings import DEFAULT_SETTINGS_YAML, parse_settings_yaml, render_settings_yaml


def test_miner_config_defaults_min_power():
    config = MinerConfig()

    assert config.min_power == 1000


def test_miner_config_defaults_max_power_to_none():
    config = MinerConfig()

    assert config.max_power is None


def test_default_settings_yaml_includes_whatsminer_min_power():
    parsed = parse_settings_yaml(DEFAULT_SETTINGS_YAML)

    whatsminer_devices = parsed["devices"]["whatsminer"]
    assert whatsminer_devices[0]["min_power"] == 1000


def test_default_settings_yaml_includes_whatsminer_max_power():
    parsed = parse_settings_yaml(DEFAULT_SETTINGS_YAML)

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
    parsed = parse_settings_yaml(DEFAULT_SETTINGS_YAML)

    assert parsed["heating_mode"] == {
        "enabled": True,
        "type": "room_target",
        "params": {
            "target_room_temp_c": 22.0,
        },
    }


def test_default_settings_yaml_includes_database_maintenance_settings():
    parsed = parse_settings_yaml(DEFAULT_SETTINGS_YAML)

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
    parsed = parse_settings_yaml(DEFAULT_SETTINGS_YAML)

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
economics:
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
