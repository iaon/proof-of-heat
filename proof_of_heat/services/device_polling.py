from __future__ import annotations

import json
import logging
import shutil
import socket
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from whatsminer_cli import DEFAULT_PORT, DEFAULT_TIMEOUT, call_whatsminer

logger = logging.getLogger("proof_of_heat.device_polling")
TRACE_LEVEL = 5
if not logging.getLevelName(TRACE_LEVEL) == "TRACE":
    logging.addLevelName(TRACE_LEVEL, "TRACE")


@dataclass(frozen=True)
class DeviceKey:
    device_type: str
    device_id: str


class DevicePoller:
    def __init__(self, settings: dict[str, Any], data_dir: Path | None = None) -> None:
        self._settings = settings
        self._lock = Lock()
        self._db_lock = Lock()
        self._latest_payloads: dict[DeviceKey, dict[str, Any]] = {}
        self._scheduler: BackgroundScheduler | None = None
        self._db_path = (data_dir / "telemetry.sqlite3") if data_dir else None

    def start(self) -> None:
        devices = self._settings.get("devices", {}) if isinstance(self._settings, dict) else {}
        if not isinstance(devices, dict):
            logger.warning("Devices settings are not a mapping; polling disabled")
            return

        default_interval = int(devices.get("refresh_interval", 30) or 30)
        poll_jobs: list[tuple[DeviceKey, dict[str, Any], Callable[..., dict[str, Any]]]] = []

        for device in devices.get("zont", []) or []:
            device_id = str(device.get("device_id", "unknown"))
            poll_jobs.append((DeviceKey("zont", device_id), device, self.poll_zont_device))

        for device in devices.get("whatsminer", []) or []:
            device_id = str(device.get("device_id", "unknown"))
            poll_jobs.append((DeviceKey("whatsminer", device_id), device, self.poll_whatsminer_device))

        if not poll_jobs:
            logger.info("No devices configured for polling")
            return

        executor = ThreadPoolExecutor(max_workers=max(1, len(poll_jobs)))
        self._scheduler = BackgroundScheduler(executors={"default": executor})

        for key, device, handler in poll_jobs:
            interval = int(device.get("refresh_interval", default_interval) or default_interval)
            trigger = CronTrigger(second=f"*/{interval}")
            job_id = f"{key.device_type}-{key.device_id}"
            self._scheduler.add_job(
                self._poll_device,
                trigger=trigger,
                id=job_id,
                name=f"Poll {job_id}",
                args=[key, device, handler],
                replace_existing=True,
            )
            logger.info(
                "Scheduled polling for %s (%s seconds)",
                job_id,
                interval,
            )

        self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def update_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        if self._scheduler:
            self.shutdown()
            self.start()

    def get_latest_payloads(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                f"{key.device_type}:{key.device_id}": payload.copy()
                for key, payload in self._latest_payloads.items()
            }

    def _poll_device(
        self,
        key: DeviceKey,
        device: dict[str, Any],
        handler: Callable[..., dict[str, Any]],
    ) -> None:
        try:
            payload = handler(device)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Polling failed for %s", key)
            return

        with self._lock:
            self._latest_payloads[key] = {
                "timestamp": datetime.utcnow().isoformat(),
                "payload": payload,
            }

    def poll_zont_device(self, device: dict[str, Any], request: dict[str, Any] | None = None) -> dict[str, Any]:
        logger.debug("Polling Zont device %s with request %s", device, request)
        return {
            "status": "stub",
            "device_id": device.get("device_id"),
            "request": request,
        }

    def poll_whatsminer_device(
        self, device: dict[str, Any], request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        logger.debug("Polling Whatsminer device %s with request %s", device, request)
        host = device.get("host")
        if not host:
            return {"error": "Missing Whatsminer host", "device_id": device.get("device_id")}
        if not self._ping_host(str(host), int(device.get("port", DEFAULT_PORT) or DEFAULT_PORT)):
            return {
                "error": "Ping failed",
                "device_id": device.get("device_id"),
                "host": host,
            }
        logger.debug("Ping successful for Whatsminer host %s", host)

        login = device.get("login")
        password = device.get("password")
        if not login or not password:
            return {"error": "Missing Whatsminer login/password", "device_id": device.get("device_id")}

        try:
            response = call_whatsminer(
                host=str(host),
                port=int(device.get("port", DEFAULT_PORT) or DEFAULT_PORT),
                account=str(login),
                account_password=str(password),
                cmd="get.miner.status",
                param="summary",
                timeout=int(device.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT),
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return {
                "error": f"Whatsminer call failed: {exc}",
                "device_id": device.get("device_id"),
            }
        logger.debug("Whatsminer response code=%s for device %s", response.get("code"), device.get("device_id"))
        logger.log(TRACE_LEVEL, "Whatsminer response payload: %s", response)

        if self._db_path:
            device_id = str(device.get("device_id", "unknown"))
            ts_ms = self._to_epoch_ms(response.get("when"))
            self._write_raw_event(
                ts_ms=ts_ms,
                device_type="whatsminer",
                device_id=device_id,
                payload=response,
            )
            summary = self._extract_whatsminer_summary(response)
            if summary:
                metrics = self._extract_whatsminer_metrics(summary)
                if metrics:
                    self._write_metrics(
                        ts_ms=ts_ms,
                        device_type="whatsminer",
                        device_id=device_id,
                        metrics=metrics,
                    )
        return response

    def _ping_host(self, host: str, port: int, timeout_s: int = 1) -> bool:
        if not host:
            return False
        ping_cmd = shutil.which("ping")
        if ping_cmd:
            try:
                result = subprocess.run(
                    [ping_cmd, "-c", "1", "-W", str(timeout_s), host],
                    capture_output=True,
                    check=False,
                    timeout=timeout_s + 1,
                )
                return result.returncode == 0
            except subprocess.SubprocessError:
                logger.warning("Ping command failed for host %s", host)
                return False
        try:
            with socket.create_connection((host, port), timeout=timeout_s):
                return True
        except OSError:
            return False

    def _extract_whatsminer_summary(self, response: dict[str, Any]) -> dict[str, Any] | None:
        payload = response.get("msg") or response.get("Msg") or response.get("message")
        if not isinstance(payload, dict):
            return None
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            return None
        return summary

    def _write_raw_event(
        self,
        ts_ms: int,
        device_type: str,
        device_id: str,
        payload: dict[str, Any],
    ) -> None:
        if not self._db_path:
            return
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_tables(conn)
                conn.execute(
                    """
                    INSERT INTO raw_events (
                        ts,
                        device_type,
                        device_id,
                        payload
                    ) VALUES (
                        :ts,
                        :device_type,
                        :device_id,
                        :payload
                    )
                    """,
                    {
                        "ts": ts_ms,
                        "device_type": device_type,
                        "device_id": device_id,
                        "payload": payload_json,
                    },
                )

    def _write_metrics(
        self,
        ts_ms: int,
        device_type: str,
        device_id: str,
        metrics: dict[str, float],
    ) -> None:
        if not self._db_path or not metrics:
            return
        rows = [
            {
                "ts": ts_ms,
                "device_type": device_type,
                "device_id": device_id,
                "metric": name,
                "value": value,
            }
            for name, value in metrics.items()
        ]
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_tables(conn)
                conn.executemany(
                    """
                    INSERT INTO metrics (
                        ts,
                        device_type,
                        device_id,
                        metric,
                        value
                    ) VALUES (
                        :ts,
                        :device_type,
                        :device_id,
                        :metric,
                        :value
                    )
                    """,
                    rows,
                )

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                device_type TEXT NOT NULL,
                device_id TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_raw_events_device_ts
            ON raw_events (device_id, ts)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_raw_events_type_ts
            ON raw_events (device_type, ts)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                device_type TEXT NOT NULL,
                device_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                labels TEXT,
                component TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_metrics_device_metric_ts
            ON metrics (device_id, metric, ts)
            """
        )

    def _extract_whatsminer_metrics(self, summary: dict[str, Any]) -> dict[str, float]:
        metrics: dict[str, float] = {}
        for key, value in summary.items():
            if key == "board-temperature":
                continue
            numeric = self._safe_float(value)
            if numeric is None:
                continue
            metrics[key.replace("-", "_")] = numeric
        board_temps = summary.get("board-temperature")
        if isinstance(board_temps, list):
            for idx, value in enumerate(board_temps):
                numeric = self._safe_float(value)
                if numeric is None:
                    continue
                metrics[f"board_temperature_{idx}"] = numeric
        return metrics

    def _safe_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _safe_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _to_epoch_ms(self, when_ts: Any) -> int:
        if when_ts is None:
            return int(datetime.utcnow().timestamp() * 1000)
        value = self._safe_int(when_ts)
        if value is None:
            return int(datetime.utcnow().timestamp() * 1000)
        if value < 1_000_000_000_000:
            return value * 1000
        return value
