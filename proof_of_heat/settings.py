from __future__ import annotations

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

DEFAULT_SETTINGS_YAML = """location:\n  name: \"Moscow\"\n  latitude: 55.7558\n  longitude: 37.6173\n  timezone: \"Europe/Moscow\"\nintegrations:\n  weather:\n    - provider: \"open_meteo\"\n      priority: 1\n      enabled: true\n    - provider: \"met_no\"\n      priority: 2\n      enabled: true\n  zont_api:\n    - id: 1\n      headers:\n        X-ZONT-Client: \"your@email.com\"\n      login: \"login\"\n      password: \"password\"\ndevices:\n  zont:\n    - integration_id: 1\n      device_id: 12000\n  whatsminer:\n    - device_id: 1\n      login: \"login\"\n      password: \"pass\"\n      host: \"example.com\"\n      port: 1111\n"""


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
    parsed = yaml.safe_load(raw_yaml) or {}
    if not isinstance(parsed, dict):
        raise ValueError("Settings YAML must be a mapping at the top level.")
    return parsed


def save_settings_yaml(raw_yaml: str) -> dict[str, Any]:
    parsed = parse_settings_yaml(raw_yaml)
    backup_settings_file()
    SETTINGS_FILE.write_text(
        yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return parsed
