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
from proof_of_heat.services.economic_polling import (
    ECONOMICS_DEVICE_ID,
    ECONOMICS_DEVICE_TYPE,
    EconomicsPoller,
)
from proof_of_heat.services.metrics import MetricSample
from proof_of_heat.services.weather import fetch_met_no_weather, fetch_open_meteo_weather

ensure_trace_level()
logger = logging.getLogger("proof_of_heat.device_polling")

CONTROL_DEVICE_TYPE = "control"
CONTROL_DEVICE_ID = "main"

@dataclass(frozen=True)
class DeviceKey:
    device_type: str
    device_id: str

@dataclass(frozen=True)
class ResolvedControlInput:
    value: float | None
    source: str | None = None
    sources: list[str] | None = None


class DevicePoller:
    def __init__(self, settings: dict[str, Any], data_dir: Path | None = None) -> None:
        self._settings = settings
        self._lock = Lock()
        self._db_lock = Lock()
        self._latest_payloads: dict[DeviceKey, dict[str, Any]] = {}
        self._scheduler: BackgroundScheduler | None = None
        self._db_path = (data_dir / "telemetry.sqlite3") if data_dir else None
        self._schema_ready = False
        self._economics_poller = EconomicsPoller(
            settings=settings,
            db_path=self._db_path,
            db_lock=self._db_lock,
            ensure_schema=self._ensure_schema,
        )

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

        economics = self._economics_poller.load_settings()
        if economics is not None:
            poll_jobs.append(
                (
                    DeviceKey(ECONOMICS_DEVICE_TYPE, ECONOMICS_DEVICE_ID),
                    economics,
                    self.poll_economics,
                )
            )

        if not poll_jobs:
            logger.info("No devices or economics polling configured")
            return

        executor = ThreadPoolExecutor(max_workers=max(1, len(poll_jobs)))
        self._scheduler = BackgroundScheduler(executors={"default": executor})

        for key, device, handler in poll_jobs:
            if key.device_type == ECONOMICS_DEVICE_TYPE:
                interval = self._economics_poller.resolve_interval_seconds(device)
            else:
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
        self._economics_poller.update_settings(settings)
        if self._scheduler:
            self.shutdown()
            self.start()

    def get_latest_payloads(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                f"{key.device_type}:{key.device_id}": payload.copy()
                for key, payload in self._latest_payloads.items()
            }

    def get_economics_metadata(self) -> dict[str, Any]:
        return self._economics_poller.get_metadata().as_dict()

    def list_metric_device_types(self) -> list[str]:
        if not self._db_path:
            return []
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    "SELECT DISTINCT device_type FROM metrics ORDER BY device_type"
                ).fetchall()
        return [row[0] for row in rows if row and row[0]]

    def list_metric_device_ids(self, device_type: str) -> list[str]:
        if not self._db_path:
            return []
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_schema(conn)
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
                self._ensure_schema(conn)
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

    def get_metric_catalog(self) -> dict[str, dict[str, list[str]]]:
        if not self._db_path:
            return {}
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    """
                    SELECT device_type, device_id, metric
                    FROM metrics
                    GROUP BY device_type, device_id, metric
                    ORDER BY device_type, device_id, metric
                    """
                ).fetchall()
        catalog: dict[str, dict[str, list[str]]] = {}
        for device_type, device_id, metric in rows:
            if not device_type or not device_id or not metric:
                continue
            catalog.setdefault(str(device_type), {}).setdefault(str(device_id), []).append(str(metric))
        return catalog

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
                self._ensure_schema(conn)
                rows = conn.execute(query, params).fetchall()
        return [{"ts": int(ts), "value": float(value)} for ts, value in rows]

    def get_latest_control_inputs(self) -> dict[str, Any] | None:
        if not self._db_path:
            return None
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    """
                    SELECT
                        ts,
                        indoor_temp,
                        indoor_temp_source,
                        outdoor_temp,
                        outdoor_temp_source,
                        supply_temp,
                        supply_temp_source,
                        power,
                        power_sources
                    FROM control_inputs
                    ORDER BY ts DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
        if row is None:
            return None
        power_sources: list[str] = []
        if row[8]:
            try:
                parsed = json.loads(row[8])
                if isinstance(parsed, list):
                    power_sources = [str(item) for item in parsed]
            except (TypeError, ValueError, json.JSONDecodeError):
                power_sources = []
        return {
            "ts": int(row[0]),
            "indoor_temp": row[1],
            "indoor_temp_source": row[2],
            "outdoor_temp": row[3],
            "outdoor_temp_source": row[4],
            "supply_temp": row[5],
            "supply_temp_source": row[6],
            "power": row[7],
            "power_sources": power_sources,
        }

    def get_latest_control_decision(self) -> dict[str, Any] | None:
        if not self._db_path:
            return None
        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    """
                    SELECT
                        ts,
                        mode,
                        resolved_target_room_temp_c,
                        resolved_target_supply_temp_c,
                        requested_power_percent,
                        requested_power_w,
                        override_reason
                    FROM control_decisions
                    ORDER BY ts DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
        if row is None:
            return None
        return {
            "ts": int(row[0]),
            "mode": row[1],
            "resolved_target_room_temp_c": row[2],
            "resolved_target_supply_temp_c": row[3],
            "requested_power_percent": row[4],
            "requested_power_w": row[5],
            "override_reason": row[6],
        }

    def record_control_decision(self, decision: dict[str, Any] | None) -> None:
        if not self._db_path or not isinstance(decision, dict):
            return
        ts_ms = self._safe_int(decision.get("ts"))
        if ts_ms is None or ts_ms < 0:
            ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        row = {
            "ts": ts_ms,
            "mode": str(decision.get("mode")) if decision.get("mode") is not None else None,
            "resolved_target_room_temp_c": self._safe_float(decision.get("resolved_target_room_temp_c")),
            "resolved_target_supply_temp_c": self._safe_float(decision.get("resolved_target_supply_temp_c")),
            "requested_power_percent": self._safe_float(decision.get("requested_power_percent")),
            "requested_power_w": self._safe_float(decision.get("requested_power_w")),
            "override_reason": (
                str(decision.get("override_reason"))
                if decision.get("override_reason") is not None
                else None
            ),
        }

        metric_rows = []
        if row["resolved_target_room_temp_c"] is not None:
            metric_rows.append(
                {
                    "ts": ts_ms,
                    "device_type": CONTROL_DEVICE_TYPE,
                    "device_id": CONTROL_DEVICE_ID,
                    "metric": "resolved_target_room_temp_c",
                    "value": row["resolved_target_room_temp_c"],
                    "unit": "celsius",
                }
            )
        if row["resolved_target_supply_temp_c"] is not None:
            metric_rows.append(
                {
                    "ts": ts_ms,
                    "device_type": CONTROL_DEVICE_TYPE,
                    "device_id": CONTROL_DEVICE_ID,
                    "metric": "resolved_target_supply_temp_c",
                    "value": row["resolved_target_supply_temp_c"],
                    "unit": "celsius",
                }
            )
        if row["requested_power_percent"] is not None:
            metric_rows.append(
                {
                    "ts": ts_ms,
                    "device_type": CONTROL_DEVICE_TYPE,
                    "device_id": CONTROL_DEVICE_ID,
                    "metric": "requested_power_percent",
                    "value": row["requested_power_percent"],
                    "unit": "%",
                }
            )
        if row["requested_power_w"] is not None:
            metric_rows.append(
                {
                    "ts": ts_ms,
                    "device_type": CONTROL_DEVICE_TYPE,
                    "device_id": CONTROL_DEVICE_ID,
                    "metric": "requested_power_w",
                    "value": row["requested_power_w"],
                    "unit": "w",
                }
            )

        with self._db_lock:
            with sqlite3.connect(self._db_path) as conn:
                self._ensure_schema(conn)
                conn.execute(
                    """
                    INSERT INTO control_decisions (
                        ts,
                        mode,
                        resolved_target_room_temp_c,
                        resolved_target_supply_temp_c,
                        requested_power_percent,
                        requested_power_w,
                        override_reason
                    ) VALUES (
                        :ts,
                        :mode,
                        :resolved_target_room_temp_c,
                        :resolved_target_supply_temp_c,
                        :requested_power_percent,
                        :requested_power_w,
                        :override_reason
                    )
                    """,
                    row,
                )
                if metric_rows:
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
                        metric_rows,
                    )

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
            summary_response = self._call_whatsminer_status(
                host=str(host),
                login=str(login),
                password=str(password),
                device=device,
                param="summary",
            )
            pools_response = self._call_whatsminer_status(
                host=str(host),
                login=str(login),
                password=str(password),
                device=device,
                param="pools",
            )
            device_info_response = self._call_whatsminer(
                host=str(host),
                login=str(login),
                password=str(password),
                device=device,
                cmd="get.device.info",
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return {
                "error": f"Whatsminer call failed: {exc}",
                "device_id": device.get("device_id"),
            }
        logger.debug(
            "Whatsminer summary response code=%s for device %s",
            summary_response.get("code"),
            device.get("device_id"),
        )
        logger.log(TRACE_LEVEL, "Whatsminer combined response payload: %s", {
            "summary": summary_response,
            "pools": pools_response,
            "device_info": device_info_response,
        })

        response = {
            "provider": "whatsminer",
            "device_id": str(device.get("device_id", "unknown")),
            "summary": summary_response,
            "pools": pools_response,
            "device_info": device_info_response,
        }

        if self._db_path:
            device_id = str(device.get("device_id", "unknown"))
            ts_ms = self._to_epoch_ms(summary_response.get("when"))
            self._write_raw_event(
                ts_ms=ts_ms,
                device_type="whatsminer",
                device_id=device_id,
                payload=response,
            )
            summary = self._extract_whatsminer_summary(summary_response)
            metrics: list[MetricSample] = []
            if summary:
                metrics.extend(self._extract_whatsminer_metrics(summary))
            metrics.extend(self._extract_whatsminer_pool_metrics(pools_response))
            metrics.extend(self._extract_whatsminer_device_info_metrics(device_info_response))
            if metrics:
                self._write_metrics(
                    ts_ms=ts_ms,
                    device_type="whatsminer",
                    device_id=device_id,
                    metrics=metrics,
                )
        return response

    def _call_whatsminer(
        self,
        host: str,
        login: str,
        password: str,
        device: dict[str, Any],
        cmd: str,
        param: Any | None = None,
    ) -> dict[str, Any]:
        return call_whatsminer(
            host=host,
            port=int(device.get("port", DEFAULT_PORT) or DEFAULT_PORT),
            account=login,
            account_password=password,
            cmd=cmd,
            param=param,
            timeout=int(device.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT),
        )

    def _call_whatsminer_status(
        self,
        host: str,
        login: str,
        password: str,
        device: dict[str, Any],
        param: str,
    ) -> dict[str, Any]:
        return self._call_whatsminer(
            host=host,
            login=login,
            password=password,
            device=device,
            cmd="get.miner.status",
            param=param,
        )

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

    def poll_economics(
        self, device: dict[str, Any], request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        del request
        economics = device if isinstance(device, dict) else self._economics_poller.load_settings()
        result = self._economics_poller.poll(economics)
        if self._db_path:
            self._write_raw_event(
                ts_ms=result.ts_ms,
                device_type=ECONOMICS_DEVICE_TYPE,
                device_id=ECONOMICS_DEVICE_ID,
                payload=result.payload,
            )
            if result.metrics:
                self._write_metrics(
                    ts_ms=result.ts_ms,
                    device_type=ECONOMICS_DEVICE_TYPE,
                    device_id=ECONOMICS_DEVICE_ID,
                    metrics=result.metrics,
                )
        return result.payload

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
                self._ensure_schema(conn)
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
                self._ensure_schema(conn)
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
                self._refresh_control_inputs(conn=conn, ts_ms=ts_ms)

    def _refresh_control_inputs(self, conn: sqlite3.Connection, ts_ms: int) -> None:
        control_inputs = self._settings.get("control_inputs") if isinstance(self._settings, dict) else None
        if not isinstance(control_inputs, dict):
            return

        max_age_seconds = self._safe_int(control_inputs.get("max_age_seconds"))
        if max_age_seconds is None or max_age_seconds < 0:
            return
        max_age_ms = max_age_seconds * 1000

        resolved = {
            "indoor_temp": self._resolve_control_input(
                conn=conn,
                spec=control_inputs.get("indoor_temp"),
                max_age_ms=max_age_ms,
                reference_ts_ms=ts_ms,
            ),
            "outdoor_temp": self._resolve_control_input(
                conn=conn,
                spec=control_inputs.get("outdoor_temp"),
                max_age_ms=max_age_ms,
                reference_ts_ms=ts_ms,
            ),
            "supply_temp": self._resolve_control_input(
                conn=conn,
                spec=control_inputs.get("supply_temp"),
                max_age_ms=max_age_ms,
                reference_ts_ms=ts_ms,
            ),
            "power": self._resolve_control_input(
                conn=conn,
                spec=control_inputs.get("power"),
                max_age_ms=max_age_ms,
                reference_ts_ms=ts_ms,
            ),
        }

        conn.execute(
            """
            INSERT INTO control_inputs (
                ts,
                indoor_temp,
                indoor_temp_source,
                outdoor_temp,
                outdoor_temp_source,
                supply_temp,
                supply_temp_source,
                power,
                power_sources
            ) VALUES (
                :ts,
                :indoor_temp,
                :indoor_temp_source,
                :outdoor_temp,
                :outdoor_temp_source,
                :supply_temp,
                :supply_temp_source,
                :power,
                :power_sources
            )
            """,
            {
                "ts": ts_ms,
                "indoor_temp": resolved["indoor_temp"].value,
                "indoor_temp_source": resolved["indoor_temp"].source,
                "outdoor_temp": resolved["outdoor_temp"].value,
                "outdoor_temp_source": resolved["outdoor_temp"].source,
                "supply_temp": resolved["supply_temp"].value,
                "supply_temp_source": resolved["supply_temp"].source,
                "power": resolved["power"].value if resolved["power"].value is not None else 0.0,
                "power_sources": json.dumps(resolved["power"].sources or [], ensure_ascii=False),
            },
        )

    def _resolve_control_input(
        self,
        conn: sqlite3.Connection,
        spec: Any,
        max_age_ms: int,
        reference_ts_ms: int,
    ) -> ResolvedControlInput:
        if not isinstance(spec, dict):
            return ResolvedControlInput(value=None)

        select = str(spec.get("select") or "").strip()
        sources = spec.get("sources")
        if not isinstance(sources, list) or not sources:
            default_value = self._safe_float(spec.get("default")) if select == "sum_all_available" else None
            return ResolvedControlInput(value=default_value)

        if select == "highest_priority_available":
            for item in sources:
                resolved = self._resolve_source_metric(
                    conn=conn,
                    spec=item,
                    max_age_ms=max_age_ms,
                    reference_ts_ms=reference_ts_ms,
                )
                if resolved is not None:
                    return ResolvedControlInput(
                        value=resolved["value"],
                        source=resolved["source"],
                    )
            return ResolvedControlInput(value=None)

        if select == "sum_all_available":
            total = 0.0
            used_sources: list[str] = []
            for item in sources:
                resolved = self._resolve_source_metric(
                    conn=conn,
                    spec=item,
                    max_age_ms=max_age_ms,
                    reference_ts_ms=reference_ts_ms,
                )
                if resolved is None:
                    continue
                total += resolved["value"]
                used_sources.append(resolved["source"])
            if used_sources:
                return ResolvedControlInput(value=total, sources=used_sources)
            default_value = self._safe_float(spec.get("default"))
            return ResolvedControlInput(value=default_value if default_value is not None else 0.0, sources=[])

        return ResolvedControlInput(value=None)

    def _resolve_source_metric(
        self,
        conn: sqlite3.Connection,
        spec: Any,
        max_age_ms: int,
        reference_ts_ms: int,
    ) -> dict[str, Any] | None:
        if not isinstance(spec, dict):
            return None
        device_type = spec.get("device_type")
        device_id = spec.get("device_id")
        metric = spec.get("metric")
        if not device_type or device_id is None or not metric:
            return None

        row = conn.execute(
            """
            SELECT ts, value
            FROM metrics
            WHERE device_type = :device_type
              AND device_id = :device_id
              AND metric = :metric
            ORDER BY ts DESC
            LIMIT 1
            """,
            {
                "device_type": str(device_type),
                "device_id": str(device_id),
                "metric": str(metric),
            },
        ).fetchone()
        if row is None:
            return None

        sample_ts = self._safe_int(row[0])
        sample_value = self._safe_float(row[1])
        if sample_ts is None or sample_value is None:
            return None
        if reference_ts_ms - sample_ts > max_age_ms:
            return None

        correction = self._safe_float(spec.get("correction")) or 0.0
        return {
            "value": sample_value + correction,
            "source": f"{device_type}:{device_id}:{metric}",
        }

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        self._ensure_tables(conn)
        self._schema_ready = True

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
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_metrics_type_device_id
            ON metrics (device_type, device_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_metrics_type_device_metric_ts
            ON metrics (device_type, device_id, metric, ts)
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS control_inputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                indoor_temp REAL,
                indoor_temp_source TEXT,
                outdoor_temp REAL,
                outdoor_temp_source TEXT,
                supply_temp REAL,
                supply_temp_source TEXT,
                power REAL NOT NULL DEFAULT 0,
                power_sources TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_control_inputs_ts
            ON control_inputs (ts)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS control_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                mode TEXT,
                resolved_target_room_temp_c REAL,
                resolved_target_supply_temp_c REAL,
                requested_power_percent REAL,
                requested_power_w REAL,
                override_reason TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_control_decisions_ts
            ON control_decisions (ts)
            """
        )
        self._ensure_columns(
            conn,
            table_name="control_inputs",
            expected_columns={
                "ts": "INTEGER NOT NULL",
                "indoor_temp": "REAL",
                "indoor_temp_source": "TEXT",
                "outdoor_temp": "REAL",
                "outdoor_temp_source": "TEXT",
                "supply_temp": "REAL",
                "supply_temp_source": "TEXT",
                "power": "REAL NOT NULL DEFAULT 0",
                "power_sources": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            table_name="control_decisions",
            expected_columns={
                "ts": "INTEGER NOT NULL",
                "mode": "TEXT",
                "resolved_target_room_temp_c": "REAL",
                "resolved_target_supply_temp_c": "REAL",
                "requested_power_percent": "REAL",
                "requested_power_w": "REAL",
                "override_reason": "TEXT",
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

    def _extract_whatsminer_device_info_metrics(
        self, response: dict[str, Any]
    ) -> list[MetricSample]:
        payload = response.get("msg") or response.get("Msg") or response.get("message")
        if not isinstance(payload, dict):
            return []
        power_payload = payload.get("power")
        if not isinstance(power_payload, dict):
            return []

        metrics: list[MetricSample] = []
        for key, value in power_payload.items():
            if not self._is_whatsminer_psu_metric_key(str(key)):
                continue
            numeric = self._safe_float(value)
            if numeric is None:
                continue
            metrics.append(MetricSample(name=f"psu_{self._sanitize_metric_name(str(key))}", value=numeric))
        return metrics

    def _extract_whatsminer_pool_metrics(
        self, response: dict[str, Any]
    ) -> list[MetricSample]:
        payload = response.get("msg") or response.get("Msg") or response.get("message")
        if not isinstance(payload, dict):
            return []
        pools = payload.get("pools")
        if not isinstance(pools, list):
            return []

        metrics: list[MetricSample] = []
        for idx, pool in enumerate(pools, start=1):
            if not isinstance(pool, dict):
                continue
            pool_id = self._safe_int(pool.get("id")) or idx
            reject_rate = self._safe_float(pool.get("reject-rate"))
            last_share_time = self._safe_float(pool.get("last-share-time"))
            if reject_rate is not None:
                metrics.append(
                    MetricSample(
                        name=f"pool_{pool_id}_reject_rate",
                        value=reject_rate,
                    )
                )
            if last_share_time is not None:
                metrics.append(
                    MetricSample(
                        name=f"pool_{pool_id}_last_share_time",
                        value=last_share_time,
                    )
                )
        return metrics

    def _is_whatsminer_psu_metric_key(self, key: str) -> bool:
        normalized = self._sanitize_metric_name(key)
        return normalized in {"iin", "vin", "vout", "pin", "fanspeed"} or bool(
            re.fullmatch(r"temp\d+", normalized)
        )

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
