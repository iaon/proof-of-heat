from __future__ import annotations

from typing import Any, Dict

from whatsminer_cli import DEFAULT_PORT, DEFAULT_TIMEOUT, call_whatsminer

from .base import Miner


class Whatsminer(Miner):
    """Adapter around the ya-whatsminer-cli library."""

    def __init__(
        self,
        host: str | None = None,
        port: int = DEFAULT_PORT,
        login: str | None = None,
        password: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.login = login
        self.password = password
        self.timeout = timeout
        self.name = "whatsminer"

    def _validate_credentials(self) -> Dict[str, str] | None:
        if not self.host:
            return {"error": "Missing Whatsminer host"}
        if not self.login or not self.password:
            return {"error": "Missing Whatsminer login/password"}
        return None

    def _get_salt(self) -> str | None:
        response = call_whatsminer(
            host=self.host,  # type: ignore[arg-type]
            port=self.port,
            account=self.login or "",
            account_password=self.password or "",
            cmd="get.device.info",
            param="salt",
            timeout=self.timeout,
        )
        payload = response.get("Msg") or response.get("msg") or response.get("message")
        if isinstance(payload, dict):
            salt = payload.get("salt")
            if isinstance(salt, str):
                return salt
        return None

    def _call(self, cmd: str, param: Any | None = None) -> Dict[str, Any]:
        missing = self._validate_credentials()
        if missing:
            return missing
        salt = None
        if cmd.startswith("set."):
            salt = self._get_salt()
            if not salt:
                return {"error": "Failed to obtain salt for set.* command"}
        try:
            return call_whatsminer(
                host=self.host,  # type: ignore[arg-type]
                port=self.port,
                account=self.login or "",
                account_password=self.password or "",
                cmd=cmd,
                param=param,
                salt=salt,
                timeout=self.timeout,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return {"error": f"Whatsminer call failed: {exc}"}

    def fetch_status(self) -> Dict[str, Any]:
        return self._call("get.miner.status", param="summary")

    def set_power_limit(self, watts: int) -> Dict[str, Any]:
        return self._call("set.miner.power_limit", param=watts)

    def stop(self) -> Dict[str, Any]:
        return self._call("set.miner.power_mode", param=2)

    def start(self) -> Dict[str, Any]:
        return self._call("set.miner.power_mode", param=0)
