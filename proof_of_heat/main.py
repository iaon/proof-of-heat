from __future__ import annotations

import json
import logging
import math
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict

import yaml

from proof_of_heat.logging_utils import configure_logging, ensure_trace_level

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
configure_logging(_resolve_log_level(os.getenv("LOG_LEVEL", "INFO")))
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
FIXED_SUPPLY_TEMP_PERCENT_PER_C = 15.0

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


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _response_has_error(response: Any) -> bool:
    return isinstance(response, dict) and bool(response.get("error"))


def _extract_whatsminer_current_power(summary: dict[str, Any]) -> tuple[float | None, str | None]:
    for key in ("power", "power-realtime", "power-5min"):
        value = _safe_float(summary.get(key))
        if value is not None and value > 0:
            return value, key
    return None, None


@dataclass
class FixedSupplyTempRuntimeState:
    signature: tuple[Any, ...] | None = None
    normal_mode_requested: bool = False
    calibration_requested: bool = False
    calibration_complete: bool = False
    baseline_power_w: float | None = None
    last_power_percent: int | None = None

    def reset(self, signature: tuple[Any, ...] | None = None) -> None:
        self.signature = signature
        self.normal_mode_requested = False
        self.calibration_requested = False
        self.calibration_complete = False
        self.baseline_power_w = None
        self.last_power_percent = None


_FIXED_SUPPLY_TEMP_RUNTIME_STATE = FixedSupplyTempRuntimeState()


@dataclass
class FixedSupplyTempMeasurement:
    raw_value_c: float
    corrected_value_c: float
    source: str | None
    age_ms: int | None


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


def _get_primary_whatsminer_device_config(settings_data: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(settings_data, dict):
        return None
    devices = settings_data.get("devices")
    if not isinstance(devices, dict):
        return None
    whatsminers = devices.get("whatsminer")
    if not isinstance(whatsminers, list) or not whatsminers:
        return None
    device = whatsminers[0]
    return device if isinstance(device, dict) else None


def _build_whatsminer_from_settings(settings_data: Dict[str, Any]) -> Any | None:
    if not isinstance(settings_data, dict):
        logger.debug("Fixed power mode skipped: settings payload is not a mapping")
        return None
    device = _get_primary_whatsminer_device_config(settings_data)
    if device is None:
        logger.debug("Fixed power mode skipped: no valid whatsminer device configured")
        return None
    kwargs: Dict[str, Any] = {
        "host": device.get("host"),
        "port": device.get("port") or config_defaults_port(),
        "login": device.get("login"),
        "password": device.get("password"),
        "timeout": device.get("timeout") or config_defaults_timeout(),
    }
    if "max_power" in device:
        kwargs["max_power"] = device.get("max_power")
    return Whatsminer(**kwargs)


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


def _build_fixed_supply_temp_signature(device: Dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(device.get("device_id") or ""),
        str(device.get("host") or ""),
        _safe_int(device.get("port")) or config_defaults_port(),
        _safe_int(device.get("max_power")),
        _safe_int(device.get("min_power")),
    )


def _resolve_control_inputs_max_age_ms(settings_data: Dict[str, Any]) -> int | None:
    if not isinstance(settings_data, dict):
        return None
    control_inputs = settings_data.get("control_inputs")
    if not isinstance(control_inputs, dict):
        return None
    max_age_seconds = _safe_int(control_inputs.get("max_age_seconds"))
    if max_age_seconds is None or max_age_seconds < 0:
        return None
    return max_age_seconds * 1000


def _resolve_fixed_supply_temp_measurement(
    settings_data: Dict[str, Any],
    control_inputs: Dict[str, Any] | None,
    correction: float,
) -> FixedSupplyTempMeasurement | None:
    if not isinstance(control_inputs, dict):
        logger.debug("Fixed supply temp mode skipped: latest control inputs unavailable")
        return None
    supply_temp = _safe_float(control_inputs.get("supply_temp"))
    if supply_temp is None:
        logger.debug("Fixed supply temp mode skipped: supply_temp is unavailable in control inputs")
        return None

    max_age_ms = _resolve_control_inputs_max_age_ms(settings_data)
    ts_ms = _safe_int(control_inputs.get("ts"))
    age_ms: int | None = None
    if max_age_ms is not None and ts_ms is not None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        age_ms = now_ms - ts_ms
        if age_ms > max_age_ms:
            logger.debug(
                "Fixed supply temp mode skipped: latest control inputs are stale (source=%r, age=%sms, max=%sms)",
                control_inputs.get("supply_temp_source"),
                age_ms,
                max_age_ms,
            )
            return None

    measurement = FixedSupplyTempMeasurement(
        raw_value_c=supply_temp,
        corrected_value_c=supply_temp + correction,
        source=str(control_inputs.get("supply_temp_source")) if control_inputs.get("supply_temp_source") else None,
        age_ms=age_ms,
    )
    logger.debug(
        "Fixed supply temp measurement accepted: raw=%.2fC corrected=%.2fC correction=%.2fC source=%r age_ms=%r",
        measurement.raw_value_c,
        measurement.corrected_value_c,
        correction,
        measurement.source,
        measurement.age_ms,
    )
    return measurement


def _apply_fixed_supply_temp_heating_mode(
    miner: Any,
    settings_data: Dict[str, Any],
    control_inputs: Dict[str, Any] | None,
    runtime_state: FixedSupplyTempRuntimeState | None = None,
) -> Dict[str, Any] | None:
    if not isinstance(settings_data, dict):
        logger.debug("Fixed supply temp mode skipped: settings payload is not a mapping")
        return None
    heating_mode = settings_data.get("heating_mode")
    if not isinstance(heating_mode, dict):
        logger.debug("Fixed supply temp mode skipped: heating_mode section is missing or invalid")
        return None
    if heating_mode.get("enabled", True) is False:
        logger.debug("Fixed supply temp mode skipped: heating_mode is disabled")
        return None
    if heating_mode.get("type") != "fixed_supply_temp":
        logger.debug(
            "Fixed supply temp mode skipped: active heating_mode type is %r",
            heating_mode.get("type"),
        )
        return None

    params = heating_mode.get("params")
    if not isinstance(params, dict):
        logger.warning("Fixed supply temp mode skipped: params section is missing or invalid")
        return None

    target_supply_temp = _safe_float(params.get("target_supply_temp_c"))
    if target_supply_temp is None:
        logger.warning(
            "Fixed supply temp mode skipped: params.target_supply_temp_c is missing or invalid"
        )
        return None
    tolerance_c = _safe_float(params.get("tolerance_c"))
    tolerance_c = max(0.0, tolerance_c if tolerance_c is not None else 1.0)
    correction = _safe_float(params.get("correction")) or 0.0

    device = _get_primary_whatsminer_device_config(settings_data)
    if device is None:
        logger.warning("Fixed supply temp mode skipped: no valid whatsminer device configured")
        return None
    max_power = _safe_int(device.get("max_power"))
    if max_power is None or max_power <= 0:
        logger.warning("Fixed supply temp mode skipped: whatsminer max_power is missing or invalid")
        return None
    min_power = _safe_float(device.get("min_power"))
    if min_power is not None and min_power < 0:
        min_power = None

    state = runtime_state or _FIXED_SUPPLY_TEMP_RUNTIME_STATE
    signature = _build_fixed_supply_temp_signature(device)
    if state.signature != signature:
        logger.info("Fixed supply temp mode resetting runtime state for device %s", device.get("device_id"))
        state.reset(signature)

    if not state.normal_mode_requested:
        logger.info("Fixed supply temp mode switching miner to normal mode")
        response = miner.start()
        if not _response_has_error(response):
            state.normal_mode_requested = True
        return response

    status = miner.fetch_status()
    logger.debug("Fixed supply temp mode raw miner status: %r", status)
    summary = _extract_whatsminer_summary(status)
    if summary is None:
        logger.warning(
            "Fixed supply temp mode skipped: unable to extract Whatsminer summary from status response"
        )
        return None

    current_power_limit = _safe_int(summary.get("power-limit"))
    up_freq_finish = _safe_int(summary.get("up-freq-finish"))
    current_power, current_power_key = _extract_whatsminer_current_power(summary)

    if not state.calibration_complete and current_power_limit != max_power and not state.calibration_requested:
        logger.info(
            "Fixed supply temp mode setting miner power limit to calibration max %sW (current=%r)",
            max_power,
            current_power_limit,
        )
        response = miner.set_power_limit(max_power)
        logger.debug("Fixed supply temp mode set_power_limit response: %r", response)
        if not _response_has_error(response):
            state.calibration_requested = True
            state.calibration_complete = False
            state.baseline_power_w = None
            state.last_power_percent = None
        return response

    if not state.calibration_complete and up_freq_finish != 1:
        logger.debug(
            "Fixed supply temp mode waiting for frequency ramp to complete: up-freq-finish=%r",
            up_freq_finish,
        )
        return None

    if not state.calibration_complete:
        if current_power is None or current_power <= 0:
            available_power_fields = {
                key: summary.get(key)
                for key in ("power", "power-realtime", "power-5min", "power-limit")
                if key in summary
            }
            logger.warning(
                "Fixed supply temp mode skipped: summary does not contain a valid current power for calibration; available_power_fields=%r",
                available_power_fields,
            )
            return None
        if current_power_limit != max_power and state.calibration_requested:
            logger.info(
                "Fixed supply temp mode proceeding with calibration at reported power-limit=%r after request for %sW",
                current_power_limit,
                max_power,
            )
        state.calibration_complete = True
        state.baseline_power_w = current_power
        state.last_power_percent = 100
        logger.info(
            "Fixed supply temp mode captured baseline power %.1fW from %s as 100%%",
            current_power,
            current_power_key or "unknown",
        )

    measurement = _resolve_fixed_supply_temp_measurement(
        settings_data=settings_data,
        control_inputs=control_inputs,
        correction=correction,
    )
    if measurement is None:
        logger.info(
            "Fixed supply temp mode waiting for fresh supply_temp measurement; keeping power_percent at %r%%",
            state.last_power_percent,
        )
        return None
    measured_supply_temp = measurement.corrected_value_c

    error_c = target_supply_temp - measured_supply_temp
    if abs(error_c) <= tolerance_c:
        logger.info(
            "Fixed supply temp mode holding power_percent at %r%% within tolerance (raw=%.2fC corrected=%.2fC target=%.2fC error=%.2fC tolerance=%.2fC source=%r)",
            state.last_power_percent,
            measurement.raw_value_c,
            measured_supply_temp,
            target_supply_temp,
            error_c,
            tolerance_c,
            measurement.source,
        )
        return None

    if error_c > tolerance_c:
        effective_error_c = error_c - tolerance_c
    else:
        effective_error_c = error_c + tolerance_c

    baseline_power_w = state.baseline_power_w or 0.0
    min_percent = 0
    if baseline_power_w > 0 and min_power is not None and min_power > 0:
        min_percent = min(100, max(0, int(math.ceil((min_power / baseline_power_w) * 100))))

    desired_percent = int(
        round(
            max(
                min_percent,
                min(
                    100.0,
                    100.0 + (effective_error_c * FIXED_SUPPLY_TEMP_PERCENT_PER_C),
                ),
            )
        )
    )
    logger.debug(
        "Fixed supply temp control decision: raw=%.2fC corrected=%.2fC target=%.2fC error=%.2fC effective_error=%.2fC tolerance=%.2fC baseline_power_w=%.1f min_power=%r min_percent=%s desired_percent=%s last_percent=%r source=%r",
        measurement.raw_value_c,
        measured_supply_temp,
        target_supply_temp,
        error_c,
        effective_error_c,
        tolerance_c,
        baseline_power_w,
        min_power,
        min_percent,
        desired_percent,
        state.last_power_percent,
        measurement.source,
    )
    if state.last_power_percent == desired_percent:
        logger.info(
            "Fixed supply temp mode keeping power_percent at %s%% (raw=%.2fC corrected=%.2fC target=%.2fC error=%.2fC source=%r)",
            desired_percent,
            measurement.raw_value_c,
            measured_supply_temp,
            target_supply_temp,
            error_c,
            measurement.source,
        )
        return None

    logger.info(
        "Fixed supply temp mode updating power_percent from %s%% to %s%% (raw=%.2fC corrected=%.2fC target=%.2fC error=%.2fC source=%r)",
        state.last_power_percent,
        desired_percent,
        measurement.raw_value_c,
        measured_supply_temp,
        target_supply_temp,
        error_c,
        measurement.source,
    )
    response = miner.set_power_percent(desired_percent)
    if not _response_has_error(response):
        state.last_power_percent = desired_percent
    return response


def _clear_fixed_supply_temp_runtime_state() -> None:
    _FIXED_SUPPLY_TEMP_RUNTIME_STATE.reset()


def _run_heating_mode_control(device_poller: Any | None = None) -> None:
    try:
        logger.debug("Heating mode control tick started")
        settings_data = parse_settings_yaml(load_settings_yaml())
        heating_mode = settings_data.get("heating_mode") if isinstance(settings_data, dict) else None
        mode_enabled = not isinstance(heating_mode, dict) or heating_mode.get("enabled", True) is not False
        mode_type = heating_mode.get("type") if isinstance(heating_mode, dict) else None
        if not mode_enabled or mode_type != "fixed_supply_temp":
            _clear_fixed_supply_temp_runtime_state()
        miner = _build_whatsminer_from_settings(settings_data)
        if miner is None:
            return
        if mode_enabled and mode_type == "fixed_supply_temp":
            control_inputs = None
            if device_poller is not None and hasattr(device_poller, "get_latest_control_inputs"):
                control_inputs = device_poller.get_latest_control_inputs()
            _apply_fixed_supply_temp_heating_mode(miner, settings_data, control_inputs)
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
        max_power=config.miner.max_power,
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

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        nonlocal control_scheduler
        route_paths = sorted({route.path for route in app_instance.router.routes})
        logger.info("Available routes: %s", ", ".join(route_paths))
        try:
            device_poller.start()
            control_scheduler = BackgroundScheduler()
            control_scheduler.add_job(
                _run_heating_mode_control,
                trigger=IntervalTrigger(seconds=_resolve_control_interval_seconds(settings_data)),
                args=[device_poller],
                id="heating-mode-control",
                replace_existing=True,
            )
            control_scheduler.start()
            _run_heating_mode_control(device_poller)
            yield
        finally:
            device_poller.shutdown()
            if control_scheduler:
                control_scheduler.shutdown(wait=False)
                control_scheduler = None

    app.router.lifespan_context = lifespan

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

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
    economics_markup = load_template("economics.html")
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

    @app.get("/economics", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/economics/", response_class=HTMLResponse, include_in_schema=False)
    def economics_view(request: Request) -> HTMLResponse:
        return HTMLResponse(render_markup(economics_markup, request))

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
        return {
            "device_types": [
                device_type
                for device_type in device_poller.list_metric_device_types()
                if device_type != "economics"
            ]
        }

    @app.get("/api/metrics/catalog")
    def get_metrics_catalog() -> Dict[str, Dict[str, Dict[str, list[str]]]]:
        catalog = device_poller.get_metric_catalog()
        catalog.pop("economics", None)
        return {"catalog": catalog}

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

    @app.get("/api/economics/current")
    @app.get("/api/economics/current/")
    def get_latest_economics() -> Dict[str, Any]:
        latest_payload = device_poller.get_latest_payloads().get("economics:market")
        if not isinstance(latest_payload, dict):
            return {"data": None, "errors": [], "polled_at": None}
        payload = latest_payload.get("payload")
        if not isinstance(payload, dict):
            return {"data": None, "errors": [], "polled_at": latest_payload.get("timestamp")}
        derived = payload.get("derived")
        errors = payload.get("errors")
        return {
            "data": derived if isinstance(derived, dict) else None,
            "errors": errors if isinstance(errors, list) else [],
            "polled_at": latest_payload.get("timestamp"),
        }

    @app.get("/api/economics/catalog")
    @app.get("/api/economics/catalog/")
    def get_economics_catalog() -> Dict[str, Any]:
        return device_poller.get_economics_metadata()

    @app.get("/api/economics/data")
    @app.get("/api/economics/data/")
    def get_economics_data(
        metric: str,
        start: str | None = None,
        end: str | None = None,
    ) -> Dict[str, Any]:
        if not metric:
            raise HTTPException(status_code=400, detail="metric required")
        start_ms = _parse_iso_datetime(start)
        end_ms = _parse_iso_datetime(end)
        points = device_poller.get_metric_series(
            device_type="economics",
            device_id="market",
            metric=metric,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        return {"points": points}

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
