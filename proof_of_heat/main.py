from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict

import yaml

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
HEATING_CURVE_DEFAULTS: Dict[str, Any] = {
    "slope": 6.0,
    "exponent": 0.4,
    "force_max_power_below_target": True,
    "force_max_power_margin_c": 5.0,
    "min_supply_temp_c": 25.0,
    "max_supply_temp_c": 60.0,
}

try:  # Lazy import to allow a diagnostic ASGI fallback if dependencies are missing
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
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
    BackgroundScheduler = None  # type: ignore[assignment]
    IntervalTrigger = None  # type: ignore[assignment]
    FastAPI = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment]
    Request = Any  # type: ignore[assignment]
    StaticFiles = None  # type: ignore[assignment]
    HTMLResponse = JSONResponse = None  # type: ignore[assignment]
    DEFAULT_CONFIG = AppConfig = human_readable_mode = Whatsminer = TemperatureController = None  # type: ignore[assignment]
    load_settings_yaml = parse_settings_yaml = save_settings_yaml = None  # type: ignore[assignment]
    _startup_error = exc


def _extract_whatsminer_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    payload = response.get("msg")
    if not isinstance(payload, dict):
        payload = response.get("Msg")
    if not isinstance(payload, dict):
        payload = response.get("message")
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary")
    return summary if isinstance(summary, dict) else None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_control_interval_seconds(settings_data: Dict[str, Any]) -> int:
    if not isinstance(settings_data, dict):
        return 30
    devices = settings_data.get("devices")
    if not isinstance(devices, dict):
        return 30
    try:
        interval = int(devices.get("refresh_interval", 30) or 30)
    except (TypeError, ValueError):
        interval = 30
    return max(1, interval)


def _build_whatsminer_from_settings(settings_data: Dict[str, Any]) -> Any | None:
    if not isinstance(settings_data, dict):
        logger.debug("Fixed power mode skipped: settings payload is not a mapping")
        return None
    devices = settings_data.get("devices")
    if not isinstance(devices, dict):
        logger.debug("Fixed power mode skipped: devices section is missing or invalid")
        return None
    whatsminers = devices.get("whatsminer")
    if not isinstance(whatsminers, list) or not whatsminers:
        logger.debug("Fixed power mode skipped: no whatsminer devices configured")
        return None
    device = whatsminers[0]
    if not isinstance(device, dict):
        logger.warning("Fixed power mode skipped: first whatsminer device config is invalid")
        return None
    return Whatsminer(
        host=device.get("host"),
        port=device.get("port") or config_defaults_port(),
        login=device.get("login"),
        password=device.get("password"),
        timeout=device.get("timeout") or config_defaults_timeout(),
    )


def config_defaults_port() -> int:
    try:
        return int(DEFAULT_CONFIG.miner.port)
    except Exception:
        return 4433


def config_defaults_timeout() -> int:
    try:
        return int(DEFAULT_CONFIG.miner.timeout)
    except Exception:
        return 10


def _apply_fixed_power_heating_mode(miner: Any, settings_data: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(settings_data, dict):
        logger.debug("Fixed power mode skipped: settings payload is not a mapping")
        return None
    heating_mode = settings_data.get("heating_mode")
    if not isinstance(heating_mode, dict):
        logger.debug("Fixed power mode skipped: heating_mode section is missing or invalid")
        return None
    if heating_mode.get("enabled", True) is False:
        logger.debug("Fixed power mode skipped: heating_mode is disabled")
        return None
    mode_type = heating_mode.get("type")
    if mode_type != "fixed_power":
        logger.debug("Fixed power mode skipped: active heating_mode type is %r", mode_type)
        return None
    params = heating_mode.get("params")
    if not isinstance(params, dict):
        logger.warning("Fixed power mode skipped: params section is missing or invalid")
        return None

    target_power = _safe_int(params.get("power_w"))
    if target_power is None:
        logger.warning("Fixed power mode skipped: params.power_w is missing or invalid")
        return None

    logger.debug("Fixed power mode evaluating target power %sW", target_power)

    status = miner.fetch_status()
    logger.debug("Fixed power mode raw miner status: %r", status)
    summary = _extract_whatsminer_summary(status)
    if summary is None:
        logger.warning("Fixed power mode skipped: unable to extract Whatsminer summary from status response")
        return None

    up_freq_finish = _safe_int(summary.get("up-freq-finish"))
    current_power_limit = _safe_int(summary.get("power-limit"))
    logger.debug(
        "Fixed power mode status: up-freq-finish=%r, power-limit=%r, target=%sW",
        up_freq_finish,
        current_power_limit,
        target_power,
    )
    if up_freq_finish != 1:
        logger.debug("Fixed power mode skipped: up-freq-finish=%r, waiting for frequency ramp to complete", up_freq_finish)
        return None
    if current_power_limit is None:
        logger.warning("Fixed power mode skipped: summary does not contain a valid power-limit")
        return None
    if current_power_limit == target_power:
        logger.debug("Fixed power mode skipped: power-limit already set to target %sW", target_power)
        return None

    logger.info(
        "Fixed power mode updating miner power limit from %sW to %sW",
        current_power_limit,
        target_power,
    )
    return miner.set_power_limit(target_power)


def _run_heating_mode_control() -> None:
    try:
        logger.debug("Heating mode control tick started")
        settings_data = parse_settings_yaml(load_settings_yaml())
        miner = _build_whatsminer_from_settings(settings_data)
        if miner is None:
            return
        _apply_fixed_power_heating_mode(miner, settings_data)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Heating mode control iteration failed")


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
    control_scheduler: BackgroundScheduler | None = None

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.on_event("startup")
    async def log_routes() -> None:
        route_paths = sorted({route.path for route in app.router.routes})
        logger.info("Available routes: %s", ", ".join(route_paths))

    @app.on_event("startup")
    async def start_device_polling() -> None:
        nonlocal control_scheduler
        device_poller.start()
        control_scheduler = BackgroundScheduler()
        control_scheduler.add_job(
            _run_heating_mode_control,
            trigger=IntervalTrigger(seconds=_resolve_control_interval_seconds(settings_data)),
            id="heating-mode-control",
            replace_existing=True,
        )
        control_scheduler.start()
        _run_heating_mode_control()

    @app.on_event("shutdown")
    async def stop_device_polling() -> None:
        device_poller.shutdown()
        if control_scheduler:
            control_scheduler.shutdown(wait=False)

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
    heating_curve_markup = load_template("heating_curve.html")

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

    @app.get("/heating-curve", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/heating-curve/", response_class=HTMLResponse, include_in_schema=False)
    def heating_curve_view(request: Request) -> HTMLResponse:
        return HTMLResponse(render_markup(heating_curve_markup, request))

    @app.get("/api/config")
    @app.get("/api/config/")
    def get_config() -> Dict[str, Any]:
        raw_yaml = load_settings_yaml()
        parsed = parse_settings_yaml(raw_yaml)
        return {"raw_yaml": raw_yaml, "parsed": parsed}

    @app.post("/api/config")
    @app.post("/api/config/")
    def update_config(payload: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal settings_data
        raw_yaml = payload.get("raw_yaml")
        if not isinstance(raw_yaml, str):
            raise HTTPException(status_code=400, detail="raw_yaml must be a string")
        parsed = save_settings_yaml(raw_yaml)
        settings_data = parsed
        device_poller.update_settings(parsed)
        if control_scheduler:
            control_scheduler.reschedule_job(
                "heating-mode-control",
                trigger=IntervalTrigger(seconds=_resolve_control_interval_seconds(parsed)),
            )
        return {"parsed": parsed}

    @app.get("/api/heating-curve")
    @app.get("/api/heating-curve/")
    def get_heating_curve() -> Dict[str, Any]:
        raw_yaml = load_settings_yaml()
        parsed = parse_settings_yaml(raw_yaml)
        return {"data": _normalize_heating_curve(parsed.get("heating_curve"))}

    @app.post("/api/heating-curve")
    @app.post("/api/heating-curve/")
    def update_heating_curve(payload: Dict[str, Any]) -> Dict[str, Any]:
        heating_curve = _normalize_heating_curve(payload)
        raw_yaml = load_settings_yaml()
        parsed = parse_settings_yaml(raw_yaml)
        parsed["heating_curve"] = heating_curve
        rendered_yaml = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True)
        saved = save_settings_yaml(rendered_yaml)
        device_poller.update_settings(saved)
        return {"data": _normalize_heating_curve(saved.get("heating_curve"))}

    @app.get("/api/metrics/device-types")
    def list_metric_device_types() -> Dict[str, list[str]]:
        return {"device_types": device_poller.list_metric_device_types()}

    @app.get("/api/metrics/catalog")
    def get_metrics_catalog() -> Dict[str, Dict[str, Dict[str, list[str]]]]:
        return {"catalog": device_poller.get_metric_catalog()}

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

    def _normalize_heating_curve(value: Any) -> Dict[str, Any]:
        curve = value if isinstance(value, dict) else {}
        slope = _coerce_float(curve.get("slope"), HEATING_CURVE_DEFAULTS["slope"])
        exponent = _coerce_float(
            curve.get("exponent"),
            HEATING_CURVE_DEFAULTS["exponent"],
        )
        force_max_power_below_target = _coerce_bool(
            curve.get("force_max_power_below_target"),
            HEATING_CURVE_DEFAULTS["force_max_power_below_target"],
        )
        force_max_power_margin_c = _coerce_float(
            curve.get("force_max_power_margin_c"),
            HEATING_CURVE_DEFAULTS["force_max_power_margin_c"],
        )
        min_supply_temp_c = _coerce_float(
            curve.get("min_supply_temp_c"),
            HEATING_CURVE_DEFAULTS["min_supply_temp_c"],
        )
        max_supply_temp_c = _coerce_float(
            curve.get("max_supply_temp_c"),
            HEATING_CURVE_DEFAULTS["max_supply_temp_c"],
        )
        if max_supply_temp_c < min_supply_temp_c:
            max_supply_temp_c = min_supply_temp_c
        return {
            "slope": slope,
            "exponent": exponent,
            "force_max_power_below_target": force_max_power_below_target,
            "force_max_power_margin_c": force_max_power_margin_c,
            "min_supply_temp_c": min_supply_temp_c,
            "max_supply_temp_c": max_supply_temp_c,
        }

    def _coerce_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _coerce_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        return default

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
