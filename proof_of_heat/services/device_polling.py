from __future__ import annotations

import json
import logging
import re
import shutil
import socket
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable

import httpx
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from whatsminer_cli import DEFAULT_PORT, DEFAULT_TIMEOUT, call_whatsminer

from proof_of_heat.logging_utils import TRACE_LEVEL, ensure_trace_level
from proof_of_heat.services.weather import fetch_met_no_weather, fetch_open_meteo_weather

ensure_trace_level()
logger = logging.getLogger("proof_of_heat.device_polling")


@dataclass(frozen=True)
class DeviceKey:
    device_type: str
    device_id: str


@dataclass(frozen=True)
class MetricSample:
    name: str
    value: float
    unit: str | None = None


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
        seen_weather_device_ids: set[int] = set()

        for device in devices.get("zont", []) or []:
            device_id = str(device.get("device_id") or device.get("serial") or "unknown")
            poll_jobs.append((DeviceKey("zont", device_id), device, self.poll_zont_device))

        for device in devices.get("whatsminer", []) or []:
            device_id = str(device.get("device_id", "unknown"))
            poll_jobs.append((DeviceKey("whatsminer", device_id), device, self.poll_whatsminer_device))

        for device in devices.get("open_meteo", []) or []:
            weather_device_id = self._normalize_weather_device_id(device)
            if weather_device_id is None:
                continue
            if weather_device_id in seen_weather_device_ids:
                logger.warning("Duplicate weather device_id=%s; skipping open_meteo device", weather_device_id)
                continue
            seen_weather_device_ids.add(weather_device_id)
            poll_jobs.append((DeviceKey("open_meteo", str(weather_device_id)), device, self.poll_open_meteo_device))

        for device in devices.get("met_no", []) or []:
            weather_device_id = self._normalize_weather_device_id(device)
            if weather_device_id is None:
                continue
            if weather_device_id in seen_weather_device_ids:
                logger.warning("Duplicate weather device_id=%s; skipping met_no device", weather_device_id)
                continue
            seen_weather_device_ids.add(weather_device_id)
            poll_jobs.append((DeviceKey("met_no", str(weather_device_id)), device, self.poll_met_no_device))

        if not poll_jobs:
            logger.info("No devices configured for polling")
            return

        executor = ThreadPoolExecutor(max_workers=max(1, len(poll_jobs)))
        self._scheduler = BackgroundScheduler(executors={"default": executor})

        for key, device, handler in poll_jobs:
            interval_default = 180 if key.device_type == "zont" else default_interval
            interval = int(device.get("refresh_interval", interval_default) or interval_default)
            interval = max(1, interval)
            trigger = IntervalTrigger(seconds=interval)
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
        for key, device, handler in poll_jobs:
            self._poll_device(key, device, handler)

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

    def list_metric_device_types(self) -> list[str]:
        if not self._db_path:
            return []
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_tables(conn)
                rows = conn.execute(
                    "SELECT DISTINCT device_type FROM metrics ORDER BY device_type"
                ).fetchall()
        return [row[0] for row in rows if row and row[0]]

    def list_metric_device_ids(self, device_type: str) -> list[str]:
        if not self._db_path:
            return []
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_tables(conn)
                rows = conn.execute(
                    """
                    SELECT DISTINCT device_id
                    FROM metrics
                    WHERE device_type = :device_type
                    ORDER BY device_id
                    """,
                    {"device_type": device_type},
                ).fetchall()
        return [row[0] for row in rows if row and row[0]]

    def list_metric_names(self, device_type: str, device_id: str) -> list[str]:
        if not self._db_path:
            return []
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_tables(conn)
                rows = conn.execute(
                    """
                    SELECT DISTINCT metric
                    FROM metrics
                    WHERE device_type = :device_type
                      AND device_id = :device_id
                    ORDER BY metric
                    """,
                    {"device_type": device_type, "device_id": device_id},
                ).fetchall()
        return [row[0] for row in rows if row and row[0]]

    def get_metric_series(
        self,
        device_type: str,
        device_id: str,
        metric: str,
        start_ms: int | None,
        end_ms: int | None,
    ) -> list[dict[str, Any]]:
        if not self._db_path:
            return []
        params: dict[str, Any] = {
            "device_type": device_type,
            "device_id": device_id,
            "metric": metric,
        }
        clauses = [
            "device_type = :device_type",
            "device_id = :device_id",
            "metric = :metric",
        ]
        if start_ms is not None:
            clauses.append("ts >= :start_ms")
            params["start_ms"] = start_ms
        if end_ms is not None:
            clauses.append("ts <= :end_ms")
            params["end_ms"] = end_ms
        where_clause = " AND ".join(clauses)
        query = f"""
            SELECT ts, value
            FROM metrics
            WHERE {where_clause}
            ORDER BY ts
        """
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_tables(conn)
                rows = conn.execute(query, params).fetchall()
        return [{"ts": int(ts), "value": float(value)} for ts, value in rows]

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
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            }

    def poll_zont_device(self, device: dict[str, Any], request: dict[str, Any] | None = None) -> dict[str, Any]:
        del request
        serial = str(device.get("serial", "")).strip()
        if not serial:
            return {
                "error": "Missing zont serial",
                "device_id": str(device.get("device_id") or "unknown"),
            }

        integration = self._resolve_zont_integration(device)
        if integration is None:
            return {
                "error": "Missing zont integration credentials",
                "serial": serial,
            }

        headers = integration.get("headers")
        if not isinstance(headers, dict):
            headers = {}
        zont_client = headers.get("X-ZONT-Client")
        login = integration.get("login")
        password = integration.get("password")
        if not zont_client or not login or not password:
            return {
                "error": "Missing zont login/password or X-ZONT-Client header",
                "serial": serial,
            }

        timeout_s = float(device.get("timeout_s", 15.0) or 15.0)
        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.post(
                    "https://my.zont.online/api/devices",
                    headers={
                        **headers,
                        "Content-Type": "application/json",
                    },
                    json={"load_io": bool(device.get("load_io", True))},
                    auth=(str(login), str(password)),
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # pragma: no cover - defensive fallback
            return {"error": f"ZONT request failed: {exc}", "serial": serial}

        devices_payload = payload.get("devices")
        if not payload.get("ok") or not isinstance(devices_payload, list):
            return {
                "error": "Unexpected ZONT response",
                "serial": serial,
                "response": payload,
            }

        serial_norm = serial.upper()
        matched = next(
            (
                item
                for item in devices_payload
                if isinstance(item, dict) and str(item.get("serial", "")).upper() == serial_norm
            ),
            None,
        )
        if not matched:
            return {
                "error": "ZONT device with configured serial not found",
                "serial": serial,
                "available_serials": [
                    str(item.get("serial"))
                    for item in devices_payload
                    if isinstance(item, dict) and item.get("serial")
                ],
            }

        device_id = str(device.get("device_id") or serial)
        result = {
            "provider": "zont",
            "serial": serial,
            "device_id": device_id,
            "integration_id": integration.get("id"),
            "device": matched,
        }
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if self._db_path:
            self._write_raw_event(
                ts_ms=ts_ms,
                device_type="zont",
                device_id=device_id,
                payload=result,
            )
            metrics = self._extract_zont_metrics(matched)
            if metrics:
                self._write_metrics(
                    ts_ms=ts_ms,
                    device_type="zont",
                    device_id=device_id,
                    metrics=metrics,
                )
        return result

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

    def poll_open_meteo_device(
        self, device: dict[str, Any], request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        del request
        location = self._load_location()
        weather_device_id = self._normalize_weather_device_id(device)
        device_id = str(weather_device_id) if weather_device_id is not None else "unknown"
        if location is None:
            return {
                "provider": "open_meteo",
                "device_id": device_id,
                "type": str(device.get("type", "virtual")),
                "error": "Missing or invalid location settings",
            }
        payload = fetch_open_meteo_weather(
            latitude=location["latitude"],
            longitude=location["longitude"],
            timezone=str(location.get("timezone") or "auto"),
            timeout_s=float(device.get("timeout_s", 10.0) or 10.0),
        )
        return self._persist_weather_payload(
            device_type="open_meteo",
            device=device,
            payload=payload,
            location=location,
        )

    def poll_met_no_device(
        self, device: dict[str, Any], request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        del request
        location = self._load_location()
        weather_device_id = self._normalize_weather_device_id(device)
        device_id = str(weather_device_id) if weather_device_id is not None else "unknown"
        if location is None:
            return {
                "provider": "met_no",
                "device_id": device_id,
                "type": str(device.get("type", "virtual")),
                "error": "Missing or invalid location settings",
            }
        payload = fetch_met_no_weather(
            latitude=location["latitude"],
            longitude=location["longitude"],
            altitude_m=location.get("altitude_m"),
            timeout_s=float(device.get("timeout_s", 10.0) or 10.0),
        )
        return self._persist_weather_payload(
            device_type="met_no",
            device=device,
            payload=payload,
            location=location,
        )

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
        metrics: list[MetricSample],
    ) -> None:
        if not self._db_path or not metrics:
            return
        rows = [
            {
                "ts": ts_ms,
                "device_type": device_type,
                "device_id": device_id,
                "metric": sample.name,
                "value": sample.value,
                "unit": sample.unit,
            }
            for sample in metrics
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
                        value,
                        unit
                    ) VALUES (
                        :ts,
                        :device_type,
                        :device_id,
                        :metric,
                        :value,
                        :unit
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
        self._ensure_columns(
            conn,
            table_name="raw_events",
            expected_columns={
                "ts": "INTEGER NOT NULL",
                "device_type": "TEXT NOT NULL",
                "device_id": "TEXT NOT NULL",
                "payload": "TEXT NOT NULL",
            },
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
        self._ensure_columns(
            conn,
            table_name="metrics",
            expected_columns={
                "ts": "INTEGER NOT NULL",
                "device_type": "TEXT NOT NULL",
                "device_id": "TEXT NOT NULL",
                "metric": "TEXT NOT NULL",
                "value": "REAL NOT NULL",
                "unit": "TEXT",
                "labels": "TEXT",
                "component": "TEXT",
            },
        )

    def _ensure_columns(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        expected_columns: dict[str, str],
    ) -> None:
        existing_columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            if len(row) > 1
        }
        for column_name, column_spec in expected_columns.items():
            if column_name in existing_columns:
                continue
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_spec}"
            )
            logger.info(
                "Migrated SQLite table %s by adding missing column %s",
                table_name,
                column_name,
            )

    def _extract_whatsminer_metrics(self, summary: dict[str, Any]) -> list[MetricSample]:
        metrics: list[MetricSample] = []
        for key, value in summary.items():
            if key == "board-temperature":
                continue
            numeric = self._safe_float(value)
            if numeric is None:
                continue
            metrics.append(MetricSample(name=key.replace("-", "_"), value=numeric))
        board_temps = summary.get("board-temperature")
        if isinstance(board_temps, list):
            for idx, value in enumerate(board_temps):
                numeric = self._safe_float(value)
                if numeric is None:
                    continue
                metrics.append(MetricSample(name=f"board_temperature_{idx}", value=numeric))
        return metrics

    def _persist_weather_payload(
        self,
        device_type: str,
        device: dict[str, Any],
        payload: dict[str, Any],
        location: dict[str, Any],
    ) -> dict[str, Any]:
        device_id = str(device.get("device_id", device_type))
        provider_ts_ms = self._to_epoch_ms(payload.get("timestamp"))
        polled_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        enriched_payload = {
            **payload,
            "device_id": device_id,
            "type": str(device.get("type", "virtual")),
            "location": location,
            "provider_ts_ms": provider_ts_ms,
        }
        if self._db_path:
            self._write_raw_event(
                ts_ms=polled_ts_ms,
                device_type=device_type,
                device_id=device_id,
                payload=enriched_payload,
            )
            metrics = self._extract_weather_metrics(
                enriched_payload.get("current"),
                enriched_payload.get("units"),
            )
            if metrics:
                self._write_metrics(
                    ts_ms=polled_ts_ms,
                    device_type=device_type,
                    device_id=device_id,
                    metrics=metrics,
                )
        return enriched_payload

    def _extract_weather_metrics(
        self,
        payload: Any,
        units: dict[str, Any] | None = None,
    ) -> list[MetricSample]:
        metrics: list[MetricSample] = []
        if not isinstance(payload, dict):
            return metrics
        for key, value in payload.items():
            metric_name = str(key).replace("-", "_")
            unit = units.get(key) if isinstance(units, dict) else None
            metrics.extend(self._flatten_metric_samples(metric_name, value, unit))
        return metrics

    def _flatten_metric_samples(
        self,
        name: str,
        value: Any,
        unit: str | None = None,
    ) -> list[MetricSample]:
        if isinstance(value, dict):
            samples: list[MetricSample] = []
            for child_key, child_value in value.items():
                child_name = f"{name}_{str(child_key).replace('-', '_')}"
                samples.extend(self._flatten_metric_samples(child_name, child_value))
            return samples
        if isinstance(value, list):
            samples: list[MetricSample] = []
            for idx, item in enumerate(value):
                samples.extend(self._flatten_metric_samples(f"{name}_{idx}", item))
            return samples
        numeric = self._safe_float(value)
        if numeric is None:
            return []
        return [MetricSample(name=name, value=numeric, unit=unit)]

    def _load_location(self) -> dict[str, Any] | None:
        if not isinstance(self._settings, dict):
            return None
        location = self._settings.get("location")
        if not isinstance(location, dict):
            return None
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        altitude_m = location.get("altitude_m")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            return None
        return {
            "name": location.get("name"),
            "latitude": float(latitude),
            "longitude": float(longitude),
            "altitude_m": int(altitude_m) if isinstance(altitude_m, (int, float)) else None,
            "timezone": location.get("timezone", "auto"),
        }

    def _normalize_weather_device_id(self, device: dict[str, Any]) -> int | None:
        try:
            return int(device.get("device_id"))
        except (TypeError, ValueError):
            logger.warning("Weather device is missing integer device_id: %s", device)
            return None

    def _resolve_zont_integration(self, device: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(self._settings, dict):
            return None
        integrations = self._settings.get("integrations")
        if not isinstance(integrations, dict):
            return None
        zont_integrations = integrations.get("zont_api")
        if not isinstance(zont_integrations, list):
            return None
        requested_id = device.get("integration_id")
        if requested_id is None:
            return zont_integrations[0] if zont_integrations else None
        requested_id_str = str(requested_id)
        for integration in zont_integrations:
            if not isinstance(integration, dict):
                continue
            if str(integration.get("id")) == requested_id_str:
                return integration
        return None

    def _extract_zont_metrics(self, payload: dict[str, Any]) -> list[MetricSample]:
        metrics: list[MetricSample] = []
        self._collect_zont_metrics(payload, prefix="", metrics=metrics)
        return metrics

    def _collect_zont_metrics(
        self,
        value: Any,
        prefix: str,
        metrics: list[MetricSample],
    ) -> None:
        if isinstance(value, dict):
            for key, child_value in value.items():
                key_part = self._sanitize_metric_name(str(key))
                child_prefix = f"{prefix}_{key_part}" if prefix else key_part
                self._collect_zont_metrics(child_value, child_prefix, metrics)
            return

        if isinstance(value, list):
            for idx, child_value in enumerate(value):
                child_prefix = f"{prefix}_{idx}" if prefix else str(idx)
                self._collect_zont_metrics(child_value, child_prefix, metrics)
            return

        if not prefix:
            return

        numeric = self._safe_float(value)
        if numeric is None:
            return
        metrics.append(MetricSample(name=prefix, value=numeric))

    def _sanitize_metric_name(self, raw_name: str) -> str:
        normalized = re.sub(r"[^0-9a-zA-Z_]+", "_", raw_name.strip())
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized.lower() or "value"

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
        if isinstance(when_ts, str):
            try:
                dt = datetime.fromisoformat(when_ts.replace("Z", "+00:00"))
            except ValueError:
                return int(datetime.utcnow().timestamp() * 1000)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        value = self._safe_int(when_ts)
        if value is None:
            return int(datetime.utcnow().timestamp() * 1000)
        if value < 1_000_000_000_000:
            return value * 1000
        return value
