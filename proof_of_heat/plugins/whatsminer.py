from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List

from .base import Miner


class Whatsminer(Miner):
    """Thin wrapper around the WhatsMiner CLI tool.

    The CLI is expected to output JSON when called with `-json status`.
    This adapter keeps the MVP flexible: replace the commands as needed.
    """

    def __init__(self, cli_path: str = "whatsminer", host: str | None = None) -> None:
        self.cli_path = cli_path
        self.host = host
        self.name = "whatsminer"

    def _build_base_cmd(self, extra: List[str]) -> List[str]:
        cmd = [self.cli_path]
        if self.host:
            cmd.extend(["-host", self.host])
        cmd.extend(extra)
        return cmd

    def _run(self, extra: List[str]) -> Dict[str, Any]:
        cmd = self._build_base_cmd(extra)
        try:
            raw = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        except FileNotFoundError:
            return {"error": f"CLI not found: {cmd[0]}", "command": " ".join(cmd)}
        except subprocess.CalledProcessError as exc:
            return {
                "error": "CLI returned non-zero exit code",
                "command": " ".join(cmd),
                "output": exc.output,
                "returncode": exc.returncode,
            }
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw_output": raw, "error": "Failed to parse JSON output"}

    def fetch_status(self) -> Dict[str, Any]:
        return self._run(["-json", "status"])

    def set_power_limit(self, watts: int) -> Dict[str, Any]:
        return self._run(["-json", "-pl", str(watts)])

    def stop(self) -> Dict[str, Any]:
        return self._run(["-json", "-stop"])

    def start(self) -> Dict[str, Any]:
        return self._run(["-json", "-start"])
