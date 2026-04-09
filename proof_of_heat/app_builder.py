from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable

try:  # pragma: no cover - imported lazily for endpoint typing when FastAPI is available
    from fastapi import Request
except Exception:  # pragma: no cover - diagnostic fallback path
    Request = Any  # type: ignore[misc,assignment]

TEMPLATES_DIR = Path(__file__).with_name("templates")
STATIC_DIR = Path(__file__).with_name("static")

HEATING_CURVE_DEFAULTS: dict[str, Any] = {
    "slope": 6.0,
    "exponent": 0.4,
    "offset": 0.0,
    "force_max_power_below_target": True,
    "force_max_power_margin_c": 5.0,
    "min_supply_temp_c": 25.0,
    "max_supply_temp_c": 60.0,
}


def load_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def render_template_text(template_name: str, replacements: dict[str, str]) -> str:
    markup = load_template(template_name)
    for key, value in replacements.items():
        markup = markup.replace(key, value)
    return markup


def _compute_static_version() -> str:
    mtimes = [
        path.stat().st_mtime_ns
        for path in STATIC_DIR.rglob("*")
        if path.is_file()
    ]
    if not mtimes:
        return "0"
    return str(max(mtimes))


STATIC_VERSION = _compute_static_version()
CONFIG_MARKUP = load_template("config.html")


@dataclass(frozen=True)
class AppBuilderDependencies:
    fastapi_cls: Any
    http_exception_cls: Any
    html_response_cls: Any
    json_response_cls: Any
    static_files_cls: Any
    background_scheduler_cls: Any
    interval_trigger_cls: Any
    whatsminer_cls: Any
    temperature_controller_cls: Any
    device_poller_cls: Any
    human_readable_mode: Callable[[str], str]
    load_settings_yaml: Callable[[], str]
    parse_settings_yaml: Callable[[str], dict[str, Any]]
    render_settings_yaml: Callable[[dict[str, Any]], str]
    save_settings_yaml: Callable[[str], dict[str, Any]]
    run_heating_mode_control: Callable[[Any | None], None]
    resolve_control_interval_seconds: Callable[[dict[str, Any]], int]


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


def _normalize_heating_curve(value: Any) -> dict[str, Any]:
    curve = value if isinstance(value, dict) else {}
    slope = _coerce_float(curve.get("slope"), HEATING_CURVE_DEFAULTS["slope"])
    exponent = _coerce_float(
        curve.get("exponent"),
        HEATING_CURVE_DEFAULTS["exponent"],
    )
    offset = _coerce_float(
        curve.get("offset"),
        HEATING_CURVE_DEFAULTS["offset"],
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
        "offset": offset,
        "force_max_power_below_target": force_max_power_below_target,
        "force_max_power_margin_c": force_max_power_margin_c,
        "min_supply_temp_c": min_supply_temp_c,
        "max_supply_temp_c": max_supply_temp_c,
    }


def _load_configured_devices(
    settings_data: dict[str, Any],
    device_type: str,
) -> list[dict[str, Any]]:
    if not isinstance(settings_data, dict):
        return []
    devices = settings_data.get("devices")
    if not isinstance(devices, dict):
        return []
    configured = devices.get(device_type)
    if not isinstance(configured, list):
        return []
    normalized: list[dict[str, Any]] = []
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


def _load_weather_devices(settings_data: dict[str, Any]) -> list[dict[str, Any]]:
    devices = _load_configured_devices(settings_data, "open_meteo")
    devices.extend(_load_configured_devices(settings_data, "met_no"))
    return devices


def create_app(
    config: Any,
    deps: AppBuilderDependencies,
    *,
    logger: Any,
    app_version: str,
) -> Any:
    logger.info("Starting proof-of-heat FastAPI app version %s", app_version)
    config.ensure_data_dir()
    root_path = os.getenv("ROOT_PATH", "").rstrip("/")

    logger.debug("Data directory ready at %s", config.data_dir)

    history_file = Path(config.data_dir) / "history.csv"
    miner = deps.whatsminer_cls(
        host=config.miner.host,
        port=config.miner.port,
        login=config.miner.login,
        password=config.miner.password,
        timeout=config.miner.timeout,
        max_power=config.miner.max_power,
    )
    controller = deps.temperature_controller_cls(
        config=config,
        miner=miner,
        history_file=history_file,
    )

    app = deps.fastapi_cls(title="proof-of-heat MVP", version=app_version, root_path=root_path)
    app.mount("/static", deps.static_files_cls(directory=STATIC_DIR), name="static")

    settings_data = deps.parse_settings_yaml(deps.load_settings_yaml())
    device_poller = deps.device_poller_cls(settings_data, data_dir=config.data_dir)
    app.state.device_poller = device_poller
    control_scheduler: Any | None = None

    @asynccontextmanager
    async def lifespan(app_instance: Any):
        nonlocal control_scheduler
        route_paths = sorted({route.path for route in app_instance.router.routes})
        logger.info("Available routes: %s", ", ".join(route_paths))
        try:
            device_poller.start()
            control_scheduler = deps.background_scheduler_cls()
            control_scheduler.add_job(
                deps.run_heating_mode_control,
                trigger=deps.interval_trigger_cls(
                    seconds=deps.resolve_control_interval_seconds(settings_data)
                ),
                args=[device_poller],
                id="heating-mode-control",
                replace_existing=True,
            )
            control_scheduler.start()
            deps.run_heating_mode_control(device_poller)
            yield
        finally:
            device_poller.shutdown()
            if control_scheduler:
                control_scheduler.shutdown(wait=False)
                control_scheduler = None

    app.router.lifespan_context = lifespan

    def render_markup(markup: str, request: Request) -> str:
        request_root_path = request.scope.get("root_path", "").rstrip("/")
        return (
            markup.replace("__ROOT_PATH_JSON__", json.dumps(request_root_path))
            .replace("__ROOT_PATH__", escape(request_root_path, quote=True))
            .replace("__STATIC_VERSION__", STATIC_VERSION)
            .replace("__APP_VERSION__", escape(app_version, quote=True))
        )

    ui_markup = load_template("ui.html")
    metrics_markup = load_template("metrics.html")
    economics_markup = load_template("economics.html")
    heating_curve_markup = load_template("heating_curve.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/debug/routes")
    def debug_routes() -> dict[str, Any]:
        return {"routes": sorted({route.path for route in app.router.routes})}

    @app.get("/", response_class=deps.html_response_cls, include_in_schema=False)
    @app.get("/ui", response_class=deps.html_response_cls, include_in_schema=False)
    def ui(request: Request) -> Any:
        return deps.html_response_cls(render_markup(ui_markup, request))

    @app.get("/config", response_class=deps.html_response_cls, include_in_schema=False)
    @app.get("/config/", response_class=deps.html_response_cls, include_in_schema=False)
    def config_editor(request: Request) -> Any:
        return deps.html_response_cls(render_markup(CONFIG_MARKUP, request))

    @app.get("/metrics", response_class=deps.html_response_cls, include_in_schema=False)
    @app.get("/metrics/", response_class=deps.html_response_cls, include_in_schema=False)
    def metrics_view(request: Request) -> Any:
        return deps.html_response_cls(render_markup(metrics_markup, request))

    @app.get("/economics", response_class=deps.html_response_cls, include_in_schema=False)
    @app.get("/economics/", response_class=deps.html_response_cls, include_in_schema=False)
    def economics_view(request: Request) -> Any:
        return deps.html_response_cls(render_markup(economics_markup, request))

    @app.get("/heating-curve", response_class=deps.html_response_cls, include_in_schema=False)
    @app.get("/heating-curve/", response_class=deps.html_response_cls, include_in_schema=False)
    def heating_curve_view(request: Request) -> Any:
        return deps.html_response_cls(render_markup(heating_curve_markup, request))

    @app.get("/api/config")
    @app.get("/api/config/")
    def get_config() -> dict[str, Any]:
        raw_yaml = deps.load_settings_yaml()
        parsed = deps.parse_settings_yaml(raw_yaml)
        return {"raw_yaml": raw_yaml, "parsed": parsed}

    @app.post("/api/config")
    @app.post("/api/config/")
    def update_config(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal settings_data
        raw_yaml = payload.get("raw_yaml")
        if not isinstance(raw_yaml, str):
            raise deps.http_exception_cls(status_code=400, detail="raw_yaml must be a string")
        try:
            parsed = deps.save_settings_yaml(raw_yaml)
        except ValueError as exc:
            raise deps.http_exception_cls(status_code=400, detail=str(exc)) from exc
        settings_data = parsed
        device_poller.update_settings(parsed)
        if control_scheduler:
            control_scheduler.reschedule_job(
                "heating-mode-control",
                trigger=deps.interval_trigger_cls(
                    seconds=deps.resolve_control_interval_seconds(parsed)
                ),
            )
        return {"parsed": parsed}

    @app.get("/api/heating-curve")
    @app.get("/api/heating-curve/")
    def get_heating_curve() -> dict[str, Any]:
        raw_yaml = deps.load_settings_yaml()
        parsed = deps.parse_settings_yaml(raw_yaml)
        return {"data": _normalize_heating_curve(parsed.get("heating_curve"))}

    @app.post("/api/heating-curve")
    @app.post("/api/heating-curve/")
    def update_heating_curve(payload: dict[str, Any]) -> dict[str, Any]:
        heating_curve = _normalize_heating_curve(payload)
        raw_yaml = deps.load_settings_yaml()
        parsed = deps.parse_settings_yaml(raw_yaml)
        parsed["heating_curve"] = heating_curve
        rendered_yaml = deps.render_settings_yaml(parsed)
        saved = deps.save_settings_yaml(rendered_yaml)
        device_poller.update_settings(saved)
        return {"data": _normalize_heating_curve(saved.get("heating_curve"))}

    @app.get("/api/metrics/device-types")
    def list_metric_device_types() -> dict[str, list[str]]:
        return {
            "device_types": [
                device_type
                for device_type in device_poller.list_metric_device_types()
                if device_type != "economics"
            ]
        }

    @app.get("/api/metrics/catalog")
    def get_metrics_catalog() -> dict[str, dict[str, dict[str, list[str]]]]:
        catalog = device_poller.get_metric_catalog()
        catalog.pop("economics", None)
        return {"catalog": catalog}

    @app.get("/api/metrics/device-ids")
    def list_metric_device_ids(device_type: str) -> dict[str, list[str]]:
        if not device_type:
            raise deps.http_exception_cls(status_code=400, detail="device_type is required")
        return {"device_ids": device_poller.list_metric_device_ids(device_type)}

    @app.get("/api/metrics/metric-names")
    def list_metric_names(device_type: str, device_id: str) -> dict[str, list[str]]:
        if not device_type or not device_id:
            raise deps.http_exception_cls(status_code=400, detail="device_type and device_id required")
        return {"metrics": device_poller.list_metric_names(device_type, device_id)}

    @app.get("/api/metrics/data")
    def get_metric_data(
        device_type: str,
        device_id: str,
        metric: str,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        if not device_type or not device_id or not metric:
            raise deps.http_exception_cls(
                status_code=400,
                detail="device_type, device_id, metric required",
            )
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
    def get_latest_control_inputs() -> dict[str, Any]:
        return {"data": device_poller.get_latest_control_inputs()}

    @app.get("/api/control-decisions/latest")
    @app.get("/api/control-decisions/latest/")
    def get_latest_control_decision() -> dict[str, Any]:
        return {"data": device_poller.get_latest_control_decision()}

    @app.get("/api/economics/current")
    @app.get("/api/economics/current/")
    def get_latest_economics() -> dict[str, Any]:
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
    def get_economics_catalog() -> dict[str, Any]:
        return device_poller.get_economics_metadata()

    @app.get("/api/economics/data")
    @app.get("/api/economics/data/")
    def get_economics_data(
        metric: str,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        if not metric:
            raise deps.http_exception_cls(status_code=400, detail="metric required")
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

    @app.get("/api/database/vacuum")
    @app.get("/api/database/vacuum/")
    def get_database_vacuum_status() -> dict[str, Any]:
        return device_poller.get_database_vacuum_status()

    @app.post("/api/database/vacuum")
    @app.post("/api/database/vacuum/")
    def run_database_vacuum(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise deps.http_exception_cls(status_code=400, detail="payload must be a JSON object")
        return device_poller.run_database_vacuum(force=bool(payload.get("force")))

    @app.get("/status")
    def status() -> dict[str, Any]:
        miner_status = miner.fetch_status()
        snapshot = controller.record_snapshot(indoor_temp_c=21.0, miner_status=miner_status)
        raw_yaml = deps.load_settings_yaml()
        parsed = deps.parse_settings_yaml(raw_yaml)
        latest_payloads = device_poller.get_latest_payloads()
        weather_payload: dict[str, Any] | None = None
        for source in _load_weather_devices(parsed):
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
            "mode_label": deps.human_readable_mode(config.mode),
            "target_temperature_c": config.target_temperature_c,
            "weather": weather_payload,
            "latest_snapshot": {
                "timestamp": snapshot.timestamp,
                "indoor_temp_c": snapshot.indoor_temp_c,
                "miner_status": snapshot.miner_status,
            },
        }

    @app.post("/mode/{mode}")
    def change_mode(mode: str) -> Any:
        if mode not in {"comfort", "eco", "off"}:
            raise deps.http_exception_cls(status_code=400, detail="Unsupported mode")
        controller.set_mode(mode)
        return deps.json_response_cls(
            {"mode": mode, "mode_label": deps.human_readable_mode(mode)}
        )

    @app.post("/target-temperature")
    def set_target(temp_c: float) -> dict[str, float]:
        controller.set_target(temp_c)
        return {"target_temperature_c": temp_c}

    @app.post("/miner/{action}")
    def control_miner(action: str) -> dict[str, Any]:
        if action == "start":
            return miner.start()
        if action == "stop":
            return miner.stop()
        raise deps.http_exception_cls(status_code=400, detail="Unsupported action")

    @app.post("/miner/power-limit")
    def set_power_limit(watts: int) -> dict[str, Any]:
        return miner.set_power_limit(watts)

    @app.get("/devices", response_class=deps.html_response_cls, include_in_schema=False)
    @app.get("/devices/", response_class=deps.html_response_cls, include_in_schema=False)
    def devices_view() -> Any:
        raw_yaml = deps.load_settings_yaml()
        parsed = deps.parse_settings_yaml(raw_yaml)
        latest_payloads = device_poller.get_latest_payloads()

        cards = []
        for device_type in ("zont", "whatsminer", "open_meteo", "met_no"):
            for device in _load_configured_devices(parsed, device_type):
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
                "__APP_VERSION__": escape(app_version, quote=True),
                "__DEVICE_CARDS__": card_markup
                or '<p class="muted">No devices configured.</p>',
            },
        )
        return deps.html_response_cls(page_markup)

    return app
