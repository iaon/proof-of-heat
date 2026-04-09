from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from proof_of_heat.app_builder import AppBuilderDependencies, create_app as build_app
from proof_of_heat.heating_modes import (
    ControlDecision,
    FixedSupplyTempRuntimeState,
    apply_fixed_power_heating_mode,
    apply_room_target_heating_mode,
    apply_fixed_supply_temp_heating_mode,
    build_whatsminer_kwargs_from_settings,
    clear_fixed_supply_temp_runtime_state,
    resolve_control_interval_seconds,
)
from proof_of_heat.logging_utils import (
    build_uvicorn_log_config,
    configure_logging,
    ensure_trace_level,
)
from proof_of_heat.version import get_display_version

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
APP_VERSION = get_display_version()
_APP_STARTED_AT_UNIX = int(datetime.now(timezone.utc).timestamp())

try:  # Lazy import to allow a diagnostic ASGI fallback if dependencies are missing
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from proof_of_heat.config import DEFAULT_CONFIG, AppConfig
    from proof_of_heat.plugins.base import human_readable_mode
    from proof_of_heat.plugins.whatsminer import Whatsminer
    from proof_of_heat.settings import (
        load_settings_yaml,
        parse_settings_yaml,
        render_settings_yaml,
        save_settings_yaml,
    )
    from proof_of_heat.services.device_polling import DevicePoller
    from proof_of_heat.services.temperature_control import TemperatureController
except Exception as exc:  # pragma: no cover - defensive import guard
    BackgroundScheduler = None  # type: ignore[assignment]
    IntervalTrigger = None  # type: ignore[assignment]
    FastAPI = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment]
    HTMLResponse = JSONResponse = None  # type: ignore[assignment]
    StaticFiles = None  # type: ignore[assignment]
    DEFAULT_CONFIG = AppConfig = human_readable_mode = Whatsminer = TemperatureController = None  # type: ignore[assignment]
    load_settings_yaml = parse_settings_yaml = render_settings_yaml = save_settings_yaml = None  # type: ignore[assignment]
    DevicePoller = None  # type: ignore[assignment]
    _startup_error = exc


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


def _build_whatsminer_from_settings(settings_data: dict[str, Any]) -> Any | None:
    kwargs = build_whatsminer_kwargs_from_settings(
        settings_data,
        logger=logger,
        default_port=config_defaults_port(),
        default_timeout=config_defaults_timeout(),
    )
    if kwargs is None:
        return None
    return Whatsminer(**kwargs)


def _apply_fixed_power_heating_mode(
    miner: Any,
    settings_data: dict[str, Any],
    decision_state: ControlDecision | None = None,
) -> dict[str, Any] | None:
    return apply_fixed_power_heating_mode(
        miner,
        settings_data,
        logger=logger,
        decision_state=decision_state,
    )


def _apply_fixed_supply_temp_heating_mode(
    miner: Any,
    settings_data: dict[str, Any],
    control_inputs: dict[str, Any] | None,
    runtime_state: FixedSupplyTempRuntimeState | None = None,
    decision_state: ControlDecision | None = None,
) -> dict[str, Any] | None:
    return apply_fixed_supply_temp_heating_mode(
        miner,
        settings_data,
        control_inputs,
        logger=logger,
        app_started_at_unix=_APP_STARTED_AT_UNIX,
        default_port=config_defaults_port(),
        runtime_state=runtime_state,
        decision_state=decision_state,
    )


def _apply_room_target_heating_mode(
    miner: Any,
    settings_data: dict[str, Any],
    control_inputs: dict[str, Any] | None,
    runtime_state: FixedSupplyTempRuntimeState | None = None,
    decision_state: ControlDecision | None = None,
) -> dict[str, Any] | None:
    return apply_room_target_heating_mode(
        miner,
        settings_data,
        control_inputs,
        logger=logger,
        app_started_at_unix=_APP_STARTED_AT_UNIX,
        default_port=config_defaults_port(),
        runtime_state=runtime_state,
        decision_state=decision_state,
    )


def _clear_fixed_supply_temp_runtime_state() -> None:
    clear_fixed_supply_temp_runtime_state()


def _run_heating_mode_control(device_poller: Any | None = None) -> None:
    try:
        logger.debug("Heating mode control tick started")
        settings_data = parse_settings_yaml(load_settings_yaml())
        heating_mode = settings_data.get("heating_mode") if isinstance(settings_data, dict) else None
        mode_enabled = not isinstance(heating_mode, dict) or heating_mode.get("enabled", True) is not False
        mode_type = heating_mode.get("type") if isinstance(heating_mode, dict) else None
        decision_state = ControlDecision()
        if not mode_enabled or mode_type not in {"fixed_supply_temp", "room_target"}:
            _clear_fixed_supply_temp_runtime_state()
        miner = _build_whatsminer_from_settings(settings_data)
        if miner is None:
            return
        if mode_enabled and mode_type in {"fixed_supply_temp", "room_target"}:
            control_inputs = None
            if device_poller is not None and hasattr(device_poller, "get_latest_control_inputs"):
                control_inputs = device_poller.get_latest_control_inputs()
            if mode_type == "fixed_supply_temp":
                _apply_fixed_supply_temp_heating_mode(
                    miner,
                    settings_data,
                    control_inputs,
                    decision_state=decision_state,
                )
            else:
                _apply_room_target_heating_mode(
                    miner,
                    settings_data,
                    control_inputs,
                    decision_state=decision_state,
                )
            if (
                device_poller is not None
                and hasattr(device_poller, "record_control_decision")
                and decision_state.mode
            ):
                device_poller.record_control_decision(decision_state.as_dict())
            return
        _apply_fixed_power_heating_mode(
            miner,
            settings_data,
            decision_state=decision_state,
        )
        if (
            device_poller is not None
            and hasattr(device_poller, "record_control_decision")
            and decision_state.mode
        ):
            device_poller.record_control_decision(decision_state.as_dict())
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Heating mode control iteration failed")


def create_app(config: AppConfig = DEFAULT_CONFIG) -> FastAPI:
    deps = AppBuilderDependencies(
        fastapi_cls=FastAPI,
        http_exception_cls=HTTPException,
        html_response_cls=HTMLResponse,
        json_response_cls=JSONResponse,
        static_files_cls=StaticFiles,
        background_scheduler_cls=BackgroundScheduler,
        interval_trigger_cls=IntervalTrigger,
        whatsminer_cls=Whatsminer,
        temperature_controller_cls=TemperatureController,
        device_poller_cls=DevicePoller,
        human_readable_mode=human_readable_mode,
        load_settings_yaml=load_settings_yaml,
        parse_settings_yaml=parse_settings_yaml,
        render_settings_yaml=render_settings_yaml,
        save_settings_yaml=save_settings_yaml,
        run_heating_mode_control=_run_heating_mode_control,
        resolve_control_interval_seconds=resolve_control_interval_seconds,
    )
    return build_app(
        config,
        deps,
        logger=logger,
        app_version=APP_VERSION,
    )


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

    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=build_uvicorn_log_config())


if __name__ == "__main__":
    run()
