from __future__ import annotations

from pathlib import Path

DEFAULT_VERSION = "unknown"
REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "VERSION"


def get_display_version() -> str:
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_VERSION
    return version or DEFAULT_VERSION
