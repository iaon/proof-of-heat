from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any, Callable

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("proof_of_heat.device_polling")


@dataclass(frozen=True)
class DeviceKey:
    device_type: str
    device_id: str


class DevicePoller:
    def __init__(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._lock = Lock()
        self._latest_payloads: dict[DeviceKey, dict[str, Any]] = {}
        self._scheduler: BackgroundScheduler | None = None

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
        return {
            "status": "stub",
            "device_id": device.get("device_id"),
            "request": request,
        }
