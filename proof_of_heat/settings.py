from __future__ import annotations

import copy
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from proof_of_heat.settings_schema import SettingsValidationError, validate_settings_data

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

def load_default_settings_yaml() -> str:
    return SETTINGS_EXAMPLE_FILE.read_text(encoding="utf-8")


def ensure_settings_file() -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(
            load_default_settings_yaml(),
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
    try:
        parsed = yaml.load(raw_yaml, Loader=_SettingsLoader) or {}
    except yaml.YAMLError as exc:
        raise SettingsValidationError(f"Settings YAML is invalid: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SettingsValidationError("Settings YAML must be a mapping at the top level.")
    validate_settings_data(parsed)
    return parsed


def render_settings_yaml(parsed: dict[str, Any]) -> str:
    return yaml.dump(parsed, Dumper=_SettingsDumper, sort_keys=False, allow_unicode=True)


def save_settings_yaml(raw_yaml: str) -> dict[str, Any]:
    parsed = parse_settings_yaml(raw_yaml)
    backup_settings_file()
    SETTINGS_FILE.write_text(render_settings_yaml(parsed), encoding="utf-8")
    return parsed
