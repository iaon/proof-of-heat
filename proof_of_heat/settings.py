from __future__ import annotations

import copy
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from dynaconf import Dynaconf

BASE_DIR = Path(__file__).resolve().parents[1]
CONF_DIR = BASE_DIR / "conf"
SETTINGS_FILE = CONF_DIR / "settings.yaml"
SETTINGS_EXAMPLE_FILE = CONF_DIR / "settings.yaml.example"

_YAML_INT_PATTERN = re.compile(
    r"""^(?:
        [-+]?0b[0-1_]+
        |[-+]?0[0-7_]+
        |[-+]?(?:0|[1-9][0-9_]*)
        |[-+]?0x[0-9a-fA-F_]+
    )$""",
    re.X,
)
_YAML_TIME_OF_DAY_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class _SettingsLoader(yaml.SafeLoader):
    pass


class _SettingsDumper(yaml.SafeDumper):
    pass


_SettingsLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for _first_char, _resolvers in list(_SettingsLoader.yaml_implicit_resolvers.items()):
    _SettingsLoader.yaml_implicit_resolvers[_first_char] = [
        resolver
        for resolver in _resolvers
        if resolver[0] != "tag:yaml.org,2002:int"
    ]
_SettingsLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    _YAML_INT_PATTERN,
    list("-+0123456789"),
)


def _represent_settings_str(dumper: _SettingsDumper, value: str) -> yaml.nodes.ScalarNode:
    style = "'" if _YAML_TIME_OF_DAY_PATTERN.fullmatch(value) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_SettingsDumper.add_representer(str, _represent_settings_str)

DEFAULT_SETTINGS_YAML = """location:\n  name: \"Moscow\"\n  latitude: 55.7558\n  longitude: 37.6173\n  timezone: \"Europe/Moscow\"\nintegrations:\n  zont_api:\n    - id: 1\n      headers:\n        X-ZONT-Client: \"your@email.com\"\n      login: \"login\"\n      password: \"password\"\ndevices:\n  refresh_interval: 30\n  open_meteo:\n    - device_id: 1001\n      type: \"virtual\"\n  met_no:\n    - device_id: 1002\n      type: \"virtual\"\n  zont:\n    - integration_id: 1\n      device_id: 12000\n      serial: \"0000000000\"\n      refresh_interval: 180\n  whatsminer:\n    - device_id: 1\n      login: \"login\"\n      password: \"pass\"\n      host: \"example.com\"\n      port: 1111\n      max_power: null\n      min_power: 1000\ndatabase:\n  retention:\n    raw_events:\n      enabled: true\n      retention_seconds: 86400\n      interval_seconds: 3600\n    metrics:\n      enabled: true\n      interval_seconds: 3600\n      raw_retention_seconds: 604800\n      rollups:\n        - resolution_seconds: 600\n          retention_seconds: 15552000\n          sample: last\n  maintenance:\n    vacuum:\n      enabled: false\n      interval_seconds: 86400\n      min_free_ratio: 0.25\n      min_reclaimable_mb: 64.0\ncontrol_inputs:\n  max_age_seconds: 180\n  indoor_temp:\n    select: highest_priority_available\n    sources: []\n  outdoor_temp:\n    select: highest_priority_available\n    sources: []\n  supply_temp:\n    select: highest_priority_available\n    sources: []\n  power:\n    select: sum_all_available\n    default: 0\n    sources: []\nheating_mode:\n  enabled: true\n  type: room_target\n  params:\n    target_room_temp_c: 22.0\n  # fixed_supply_temp example:\n  # type: fixed_supply_temp\n  # params:\n  #   target_supply_temp_c: 42.0\n  #   tolerance_c: 1.0\n  #   correction: 0.0\nheating_curve:\n  slope: 6.0\n  exponent: 0.4\n  offset: 0.0\n  force_max_power_below_target: true\n  force_max_power_margin_c: 5.0\n  min_supply_temp_c: 25.0\n  max_supply_temp_c: 60.0\neconomics:\n  enabled: true\n  currencies:\n    crypto: BTC\n    fiat: RUB\n  exchange_rate:\n    integrations:\n      crypto_usd: mempool_space\n      usd_fiat: cbr\n    refresh_interval: 3600\n    stale_after: 7200\n  hashprice:\n    integration: mempool_space\n    reward_stats_blocks: 144\n    hashrate_window: 1m\n    refresh_interval: 3600\n    stale_after: 7200\n  electricity:\n    mode: time_of_day\n    tariffs:\n      - start: \"07:00\"\n        price_per_kwh: 8.0\n      - start: \"23:00\"\n        price_per_kwh: 5.0\n"""


def ensure_settings_file() -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_EXAMPLE_FILE.exists():
        SETTINGS_EXAMPLE_FILE.write_text(DEFAULT_SETTINGS_YAML, encoding="utf-8")
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(
            SETTINGS_EXAMPLE_FILE.read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def load_settings() -> "Dynaconf":
    ensure_settings_file()
    try:
        from dynaconf import Dynaconf
    except ImportError as exc:  # pragma: no cover - dependency guard
        fallback = _FallbackSettings(SETTINGS_FILE)
        fallback.reload()
        return fallback  # type: ignore[return-value]
    return Dynaconf(
        settings_files=[str(SETTINGS_FILE)],
        envvar_prefix="POH",
        environments=True,
        load_dotenv=True,
        merge_enabled=True,
    )


class _FallbackSettings:
    """Fallback settings loader when Dynaconf is unavailable."""

    def __init__(self, settings_file: Path) -> None:
        self._settings_file = settings_file
        self._data: dict[str, Any] = {}

    def reload(self) -> None:
        raw_yaml = self._settings_file.read_text(encoding="utf-8")
        self._data = parse_settings_yaml(raw_yaml)

    def as_dict(self) -> dict[str, Any]:
        return self._data


def backup_settings_file() -> Path | None:
    if not SETTINGS_FILE.exists():
        return None

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = SETTINGS_FILE.with_name(f"settings.{timestamp}.yaml")
    backup_path.write_text(SETTINGS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def serialize_settings(settings: Any) -> dict[str, Any]:
    if not hasattr(settings, "as_dict"):
        raise ValueError("Settings object does not support serialization.")
    return settings.as_dict()


def load_settings_yaml() -> str:
    ensure_settings_file()
    return SETTINGS_FILE.read_text(encoding="utf-8")


def parse_settings_yaml(raw_yaml: str) -> dict[str, Any]:
    parsed = yaml.load(raw_yaml, Loader=_SettingsLoader) or {}
    if not isinstance(parsed, dict):
        raise ValueError("Settings YAML must be a mapping at the top level.")
    return parsed


def render_settings_yaml(parsed: dict[str, Any]) -> str:
    return yaml.dump(parsed, Dumper=_SettingsDumper, sort_keys=False, allow_unicode=True)


def save_settings_yaml(raw_yaml: str) -> dict[str, Any]:
    parsed = parse_settings_yaml(raw_yaml)
    backup_settings_file()
    SETTINGS_FILE.write_text(render_settings_yaml(parsed), encoding="utf-8")
    return parsed
