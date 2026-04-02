from __future__ import annotations

import os
import subprocess
from pathlib import Path

DEFAULT_BASE_VERSION = "0.1.0"
DISPLAY_VERSION_ENV = "PROOF_OF_HEAT_DISPLAY_VERSION"
BASE_VERSION_ENV = "PROOF_OF_HEAT_VERSION"
COMMIT_ENV = "PROOF_OF_HEAT_COMMIT"
REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "VERSION"


def get_base_version() -> str:
    env_version = os.getenv(BASE_VERSION_ENV, "").strip()
    if env_version:
        return env_version
    try:
        file_version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_BASE_VERSION
    return file_version or DEFAULT_BASE_VERSION


def _run_git(*args: str) -> str | None:
    if not (REPO_ROOT / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    output = result.stdout.strip()
    return output or None


def _get_commit_id() -> str | None:
    env_commit = os.getenv(COMMIT_ENV, "").strip()
    if env_commit:
        return env_commit[:12]
    git_commit = _run_git("rev-parse", "--short", "HEAD")
    return git_commit[:12] if git_commit else None


def _is_release_commit(base_version: str) -> bool:
    tags_output = _run_git("tag", "--points-at", "HEAD")
    if not tags_output:
        return False
    release_tags = {base_version, f"v{base_version}"}
    return any(tag.strip() in release_tags for tag in tags_output.splitlines())


def get_display_version() -> str:
    explicit_version = os.getenv(DISPLAY_VERSION_ENV, "").strip()
    if explicit_version:
        return explicit_version

    base_version = get_base_version()
    if _is_release_commit(base_version):
        return base_version

    commit_id = _get_commit_id()
    if commit_id:
        return f"{base_version}-{commit_id}"
    return base_version
