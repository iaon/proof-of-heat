from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict

from proof_of_heat.logging_utils import ensure_trace_level

_startup_error: Exception | None = None


def _diagnostic_app(error: Exception):  # pragma: no cover - defensive fallback
    async def app(scope, receive, send):
        if scope.get("type") != "http":
            await send({"type": "http.response.start", "status": 500, "headers": []})
            await send({"type": "http.response.body", "body": b"ASGI app unavailable"})
            return

        body = f"proof-of-heat failed to start: {error}".encode()
        headers = [(b"content-type", b"text/plain; charset=utf-8")]
        await send({"type": "http.response.start", "status": 500, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return app


def _resolve_log_level(value: str) -> int:
    level = logging.getLevelName(value.upper())
    return level if isinstance(level, int) else logging.INFO


ensure_trace_level()
app: Any = _diagnostic_app(Exception("proof-of-heat app not initialized"))
logger = logging.getLogger("proof_of_heat")
logging.basicConfig(level=_resolve_log_level(os.getenv("LOG_LEVEL", "INFO")))
TEMPLATES_DIR = Path(__file__).with_name("templates")
STATIC_DIR = Path(__file__).with_name("static")


def load_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _compute_static_version() -> str:
    mtimes = [
        path.stat().st_mtime_ns
        for path in STATIC_DIR.rglob("*")
        if path.is_file()
    ]
    if not mtimes:
        return "0"
    return str(max(mtimes))


def render_template_text(template_name: str, replacements: Dict[str, str]) -> str:
    markup = load_template(template_name)
    for key, value in replacements.items():
        markup = markup.replace(key, value)
    return markup


CONFIG_MARKUP = load_template("config.html")
STATIC_VERSION = _compute_static_version()

try:  # Lazy import to allow a diagnostic ASGI fallback if dependencies are missing
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from proof_of_heat.config import DEFAULT_CONFIG, AppConfig
    from proof_of_heat.plugins.base import human_readable_mode
    from proof_of_heat.plugins.whatsminer import Whatsminer
    from proof_of_heat.settings import (
        load_settings_yaml,
        parse_settings_yaml,
        save_settings_yaml,
    )
    from proof_of_heat.services.device_polling import DevicePoller
    from proof_of_heat.services.temperature_control import TemperatureController
except Exception as exc:  # pragma: no cover - defensive import guard
    FastAPI = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment]
    Request = Any  # type: ignore[assignment]
    StaticFiles = None  # type: ignore[assignment]
    HTMLResponse = JSONResponse = None  # type: ignore[assignment]
    DEFAULT_CONFIG = AppConfig = human_readable_mode = Whatsminer = TemperatureController = None  # type: ignore[assignment]
    load_settings_yaml = parse_settings_yaml = save_settings_yaml = None  # type: ignore[assignment]
    _startup_error = exc


def create_app(config: AppConfig = DEFAULT_CONFIG) -> FastAPI:
    logger.info("Starting proof-of-heat FastAPI app")
    config.ensure_data_dir()
    root_path = os.getenv("ROOT_PATH", "").rstrip("/")

    logger.debug("Data directory ready at %s", config.data_dir)

    history_file = Path(config.data_dir) / "history.csv"
    miner = Whatsminer(
        host=config.miner.host,
        port=config.miner.port,
        login=config.miner.login,
        password=config.miner.password,
        timeout=config.miner.timeout,
    )
    controller = TemperatureController(
        config=config, miner=miner, history_file=history_file
    )

    app = FastAPI(title="proof-of-heat MVP", version="0.1.0", root_path=root_path)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    settings_data = parse_settings_yaml(load_settings_yaml())
    device_poller = DevicePoller(settings_data, data_dir=config.data_dir)
    app.state.device_poller = device_poller

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.on_event("startup")
    async def log_routes() -> None:
        route_paths = sorted({route.path for route in app.router.routes})
        logger.info("Available routes: %s", ", ".join(route_paths))

    @app.on_event("startup")
    async def start_device_polling() -> None:
        device_poller.start()

    @app.on_event("shutdown")
    async def stop_device_polling() -> None:
        device_poller.shutdown()

    @app.get("/debug/routes")
    def debug_routes() -> Dict[str, Any]:
        return {"routes": sorted({route.path for route in app.router.routes})}

    def render_markup(markup: str, request: Request) -> str:
        root_path = request.scope.get("root_path", "").rstrip("/")
        return (
            markup.replace("__ROOT_PATH_JSON__", json.dumps(root_path))
            .replace("__ROOT_PATH__", escape(root_path, quote=True))
            .replace("__STATIC_VERSION__", STATIC_VERSION)
        )

    ui_markup = load_template("ui.html")
    metrics_markup = load_template("metrics.html")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def ui(request: Request) -> HTMLResponse:
        return HTMLResponse(render_markup(ui_markup, request))

    @app.get("/config", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/config/", response_class=HTMLResponse, include_in_schema=False)
    def config_editor(request: Request) -> HTMLResponse:
        return HTMLResponse(render_markup(CONFIG_MARKUP, request))

    @app.get("/metrics", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/metrics/", response_class=HTMLResponse, include_in_schema=False)
    def metrics_view(request: Request) -> HTMLResponse:
        return HTMLResponse(render_markup(metrics_markup, request))

    @app.get("/api/config")
    @app.get("/api/config/")
    def get_config() -> Dict[str, Any]:
        raw_yaml = load_settings_yaml()
        parsed = parse_settings_yaml(raw_yaml)
        return {"raw_yaml": raw_yaml, "parsed": parsed}

    @app.post("/api/config")
    @app.post("/api/config/")
    def update_config(payload: Dict[str, Any]) -> Dict[str, Any]:
        raw_yaml = payload.get("raw_yaml")
        if not isinstance(raw_yaml, str):
            raise HTTPException(status_code=400, detail="raw_yaml must be a string")
        parsed = save_settings_yaml(raw_yaml)
        device_poller.update_settings(parsed)
        return {"parsed": parsed}

    @app.get("/api/metrics/device-types")
    def list_metric_device_types() -> Dict[str, list[str]]:
        return {"device_types": device_poller.list_metric_device_types()}

    @app.get("/api/metrics/device-ids")
    def list_metric_device_ids(device_type: str) -> Dict[str, list[str]]:
        if not device_type:
            raise HTTPException(status_code=400, detail="device_type is required")
        return {"device_ids": device_poller.list_metric_device_ids(device_type)}

    @app.get("/api/metrics/metric-names")
    def list_metric_names(device_type: str, device_id: str) -> Dict[str, list[str]]:
        if not device_type or not device_id:
            raise HTTPException(status_code=400, detail="device_type and device_id required")
        return {"metrics": device_poller.list_metric_names(device_type, device_id)}

    @app.get("/api/metrics/data")
    def get_metric_data(
        device_type: str,
        device_id: str,
        metric: str,
        start: str | None = None,
        end: str | None = None,
    ) -> Dict[str, Any]:
        if not device_type or not device_id or not metric:
            raise HTTPException(status_code=400, detail="device_type, device_id, metric required")
        start_ms = _parse_iso_datetime(start)
        end_ms = _parse_iso_datetime(end)
        points = device_poller.get_metric_series(
            device_type=device_type,
            device_id=device_id,
            metric=metric,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        return {"points": points}

    @app.get("/api/control-inputs/latest")
    @app.get("/api/control-inputs/latest/")
    def get_latest_control_inputs() -> Dict[str, Any]:
        return {"data": device_poller.get_latest_control_inputs()}

    def _parse_iso_datetime(value: str | None) -> int | None:
        if not value:
            return None
        try:
            cleaned = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def _load_configured_devices(
        settings_data: Dict[str, Any],
        device_type: str,
    ) -> list[Dict[str, Any]]:
        if not isinstance(settings_data, dict):
            return []
        devices = settings_data.get("devices")
        if not isinstance(devices, dict):
            return []
        configured = devices.get(device_type)
        if not isinstance(configured, list):
            return []
        normalized: list[Dict[str, Any]] = []
        for device in configured:
            if not isinstance(device, dict):
                continue
            device_id = device.get("device_id", device_type)
            if device_type in {"open_meteo", "met_no"}:
                try:
                    device_id = int(device_id)
                except (TypeError, ValueError):
                    continue
            normalized.append(
                {
                    "device_type": device_type,
                    "device_id": str(device_id),
                    "type": device.get("type"),
                    "config": device,
                }
            )
        return normalized

    def _load_weather_devices(settings_data: Dict[str, Any]) -> list[Dict[str, Any]]:
        devices = _load_configured_devices(settings_data, "open_meteo")
        devices.extend(_load_configured_devices(settings_data, "met_no"))
        return devices

    @app.get("/status")
    def status() -> Dict[str, Any]:
        miner_status = miner.fetch_status()
        snapshot = controller.record_snapshot(indoor_temp_c=21.0, miner_status=miner_status)
        raw_yaml = load_settings_yaml()
        settings_data = parse_settings_yaml(raw_yaml)
        latest_payloads = device_poller.get_latest_payloads()
        weather_payload: Dict[str, Any] | None = None
        for source in _load_weather_devices(settings_data):
            latest = latest_payloads.get(f"{source['device_type']}:{source['device_id']}")
            if not latest:
                continue
            payload = latest.get("payload")
            if not isinstance(payload, dict):
                continue
            weather_payload = {
                **payload,
                "polled_at": latest.get("timestamp"),
            }
            break
        return {
            "mode": config.mode,
            "mode_label": human_readable_mode(config.mode),
            "target_temperature_c": config.target_temperature_c,
            "weather": weather_payload,
            "latest_snapshot": {
                "timestamp": snapshot.timestamp,
                "indoor_temp_c": snapshot.indoor_temp_c,
                "miner_status": snapshot.miner_status,
            },
        }

    @app.post("/mode/{mode}")
    def change_mode(mode: str) -> JSONResponse:
        if mode not in {"comfort", "eco", "off"}:
            raise HTTPException(status_code=400, detail="Unsupported mode")
        controller.set_mode(mode)
        return JSONResponse({"mode": mode, "mode_label": human_readable_mode(mode)})

    @app.post("/target-temperature")
    def set_target(temp_c: float) -> Dict[str, float]:
        controller.set_target(temp_c)
        return {"target_temperature_c": temp_c}

    @app.post("/miner/{action}")
    def control_miner(action: str) -> Dict[str, Any]:
        if action == "start":
            return miner.start()
        if action == "stop":
            return miner.stop()
        raise HTTPException(status_code=400, detail="Unsupported action")

    @app.post("/miner/power-limit")
    def set_power_limit(watts: int) -> Dict[str, Any]:
        return miner.set_power_limit(watts)

    @app.get("/devices", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/devices/", response_class=HTMLResponse, include_in_schema=False)
    def devices_view() -> HTMLResponse:
        raw_yaml = load_settings_yaml()
        settings_data = parse_settings_yaml(raw_yaml)
        latest_payloads = device_poller.get_latest_payloads()

        cards = []
        for device_type in ("zont", "whatsminer", "open_meteo", "met_no"):
            for device in _load_configured_devices(settings_data, device_type):
                label = f"{device_type} {device['device_id']}"
                if device.get("type"):
                    label = f"{label} ({device['type']})"
                payload = latest_payloads.get(f"{device_type}:{device['device_id']}", {})
                cards.append((label, payload))

        card_markup = "".join(
            render_template_text(
                "device_card.html",
                {
                    "__DEVICE_LABEL__": escape(str(label)),
                    "__DEVICE_PAYLOAD__": escape(
                        json.dumps(payload, ensure_ascii=False, indent=2)
                    ),
                },
            )
            for label, payload in cards
        )

        page_markup = render_template_text(
            "devices.html",
            {
                "__ROOT_PATH__": escape(root_path, quote=True),
                "__STATIC_VERSION__": STATIC_VERSION,
                "__DEVICE_CARDS__": card_markup
                or '<p class="muted">No devices configured.</p>',
            },
        )
        return HTMLResponse(page_markup)

    return app

def _safe_create_app() -> Any:
    """Create the FastAPI app but never leave it undefined.

    If initialization fails for any reason (e.g., bad config, missing CLI or even
    missing FastAPI import), return a small diagnostics ASGI app so uvicorn still
    exposes something instead of erroring with "Attribute app not found".
    """

    if _startup_error is not None:
        logger.error("proof-of-heat failed during imports: %s", _startup_error)
        return _diagnostic_app(_startup_error)

    try:
        app_instance = create_app()
        logger.info("proof-of-heat app created successfully")
        return app_instance
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception("proof-of-heat failed to create the ASGI app: %s", exc)
        return _diagnostic_app(exc)


app: FastAPI = _safe_create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
