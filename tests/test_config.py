from proof_of_heat.config import (
    AppConfig,
    FixedPowerHeatingParams,
    FixedSupplyTempHeatingParams,
    HeatingModeConfig,
    MinerConfig,
    RoomTargetHeatingParams,
)
from proof_of_heat.settings import DEFAULT_SETTINGS_YAML, parse_settings_yaml


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
            "price_per_kwh": 5.5,
        },
    }
