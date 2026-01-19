from __future__ import annotations

import json
import logging
from html import escape
from pathlib import Path
from typing import Any, Dict

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


app: Any = _diagnostic_app(Exception("proof-of-heat app not initialized"))
logger = logging.getLogger("proof_of_heat")
logging.basicConfig(level=logging.INFO)
CONFIG_MARKUP = """
        <!doctype html>
        <html lang="en">
        <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>proof-of-heat Configuration</title>
            <style>
                body { font-family: system-ui, sans-serif; margin: 24px; color: #0f172a; background: #f8fafc; }
                h1 { margin-top: 0; }
                textarea { width: 100%; min-height: 360px; padding: 12px; border-radius: 6px; border: 1px solid #cbd5e1; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
                button { cursor: pointer; padding: 8px 12px; border: none; background: #2563eb; color: white; border-radius: 4px; }
                .card { background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); margin-bottom: 16px; }
                .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
                .muted { color: #64748b; }
                pre { background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 6px; overflow-x: auto; }
            </style>
        </head>
        <body>
            <h1>Configuration</h1>
            <p class="muted">Edit integrations and devices. Saving will create a timestamped backup in the conf folder.</p>

            <div class="card">
                <div class="row" style="margin-bottom:8px;">
                    <button id="load">Reload</button>
                    <button id="save">Save</button>
                </div>
                <textarea id="settings"></textarea>
            </div>

            <div class="card">
                <strong>Parsed preview</strong>
                <pre id="preview">Loading...</pre>
            </div>

            <script>
                const settingsEl = document.getElementById('settings');
                const previewEl = document.getElementById('preview');

                async function refreshPreview(data) {
                    previewEl.textContent = JSON.stringify(data, null, 2);
                }

                async function loadSettings() {
                    previewEl.textContent = 'Loading...';
                    const res = await fetch('/api/config');
                    const data = await res.json();
                    if (!res.ok) {
                        previewEl.textContent = 'Error: ' + (data.detail || 'Failed to load');
                        return;
                    }
                    settingsEl.value = data.raw_yaml || '';
                    await refreshPreview(data.parsed || {});
                }

                async function saveSettings() {
                    const res = await fetch('/api/config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ raw_yaml: settingsEl.value }),
                    });
                    const data = await res.json();
                    if (res.ok) {
                        await refreshPreview(data.parsed || {});
                    } else {
                        previewEl.textContent = 'Error: ' + (data.detail || 'Failed to save');
                    }
                }

                document.getElementById('load').addEventListener('click', loadSettings);
                document.getElementById('save').addEventListener('click', saveSettings);

                loadSettings();
            </script>
        </body>
        </html>
        """

try:  # Lazy import to allow a diagnostic ASGI fallback if dependencies are missing
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    from proof_of_heat.config import DEFAULT_CONFIG, AppConfig
    from proof_of_heat.plugins.base import human_readable_mode
    from proof_of_heat.plugins.whatsminer import Whatsminer
    from proof_of_heat.settings import (
        load_settings_yaml,
        parse_settings_yaml,
        save_settings_yaml,
    )
    from proof_of_heat.services.temperature_control import TemperatureController
    from proof_of_heat.services.weather import (
        fetch_met_no_weather,
        fetch_open_meteo_weather,
    )
except Exception as exc:  # pragma: no cover - defensive import guard
    FastAPI = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment]
    HTMLResponse = JSONResponse = None  # type: ignore[assignment]
    DEFAULT_CONFIG = AppConfig = human_readable_mode = Whatsminer = TemperatureController = None  # type: ignore[assignment]
    load_settings_yaml = parse_settings_yaml = save_settings_yaml = None  # type: ignore[assignment]
    fetch_met_no_weather = fetch_open_meteo_weather = None  # type: ignore[assignment]
    _startup_error = exc


def create_app(config: AppConfig = DEFAULT_CONFIG) -> FastAPI:
    logger.info("Starting proof-of-heat FastAPI app")
    config.ensure_data_dir()

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

    app = FastAPI(title="proof-of-heat MVP", version="0.1.0")

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.on_event("startup")
    async def log_routes() -> None:
        route_paths = sorted({route.path for route in app.router.routes})
        logger.info("Available routes: %s", ", ".join(route_paths))

    @app.get("/debug/routes")
    def debug_routes() -> Dict[str, Any]:
        return {"routes": sorted({route.path for route in app.router.routes})}

    ui_markup = """
            <!doctype html>
            <html lang=\"en\">
            <head>
                <meta charset=\"utf-8\" />
                <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
                <title>proof-of-heat UI</title>
                <style>
                    body { font-family: system-ui, sans-serif; margin: 24px; color: #0f172a; background: #f8fafc; }
                    h1 { margin-top: 0; }
                    .card { background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); margin-bottom: 16px; }
                    .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
                    label { font-weight: 600; }
                    input, select { padding: 6px 8px; border-radius: 4px; border: 1px solid #cbd5e1; }
                    button { cursor: pointer; padding: 8px 12px; border: none; background: #2563eb; color: white; border-radius: 4px; }
                    button.secondary { background: #0ea5e9; }
                    pre { background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 6px; overflow-x: auto; }
                    .muted { color: #64748b; }
                </style>
            </head>
            <body>
                <h1>proof-of-heat MVP</h1>
                <p><a href="/config">Edit configuration</a></p>
                <p class=\"muted\">Quick control panel for the miner-backed heating MVP. Data refreshes live on load and whenever you click refresh.</p>

                <div class=\"card\">
                    <div class=\"row\">
                        <strong>Status</strong>
                        <button id=\"refresh\">Refresh</button>
                    </div>
                    <pre id=\"status\">Loading...</pre>
                </div>

                <div class=\"card\">
                    <div class=\"row\">
                        <strong>Weather</strong>
                        <span class=\"muted\" id=\"weather-location\"></span>
                    </div>
                    <pre id=\"weather\">Loading...</pre>
                </div>

                <div class=\"card\">
                    <div class=\"row\">
                        <label for=\"target\">Target °C</label>
                        <input id=\"target\" type=\"number\" step=\"0.5\" min=\"5\" max=\"35\" />
                        <button id=\"apply-target\" class=\"secondary\">Set</button>
                    </div>
                    <div class=\"row\">
                        <label for=\"mode\">Mode</label>
                        <select id=\"mode\">
                            <option value=\"comfort\">Comfort</option>
                            <option value=\"eco\">Eco</option>
                            <option value=\"off\">Off</option>
                        </select>
                        <button id=\"apply-mode\">Apply</button>
                    </div>
                </div>

                <div class=\"card\">
                    <div class=\"row\" style=\"margin-bottom:8px;\">
                        <strong>Miner</strong>
                        <button id=\"start\" class=\"secondary\">Start</button>
                        <button id=\"stop\">Stop</button>
                    </div>
                    <div class=\"row\">
                        <label for=\"power\">Power limit (W)</label>
                        <input id=\"power\" type=\"number\" min=\"500\" max=\"7000\" step=\"50\" />
                        <button id=\"apply-power\">Update</button>
                    </div>
                </div>

                <script>
                    const statusEl = document.getElementById('status');
                    const weatherEl = document.getElementById('weather');
                    const weatherLocationEl = document.getElementById('weather-location');
                    const targetEl = document.getElementById('target');
                    const modeEl = document.getElementById('mode');
                    const powerEl = document.getElementById('power');

                    async function loadStatus() {
                        statusEl.textContent = 'Loading...';
                        weatherEl.textContent = 'Loading...';
                        weatherLocationEl.textContent = '';
                        try {
                            const res = await fetch('/status');
                            const data = await res.json();
                            statusEl.textContent = JSON.stringify(data, null, 2);
                            if (data.target_temperature_c !== undefined) {
                                targetEl.value = data.target_temperature_c;
                            }
                            if (data.mode) {
                                modeEl.value = data.mode;
                            }
                            if (data.weather) {
                                weatherEl.textContent = JSON.stringify(data.weather, null, 2);
                                if (data.weather.location && data.weather.location.name) {
                                    weatherLocationEl.textContent = data.weather.location.name;
                                }
                            } else {
                                weatherEl.textContent = 'No weather data configured.';
                            }
                        } catch (err) {
                            statusEl.textContent = 'Failed to load status: ' + err;
                            weatherEl.textContent = 'Failed to load weather: ' + err;
                        }
                    }

                    async function setTarget() {
                        const temp = targetEl.value;
                        const res = await fetch(`/target-temperature?temp_c=${encodeURIComponent(temp)}`, { method: 'POST' });
                        const data = await res.json();
                        statusEl.textContent = JSON.stringify(data, null, 2);
                    }

                    async function setMode() {
                        const mode = modeEl.value;
                        const res = await fetch(`/mode/${mode}`, { method: 'POST' });
                        const data = await res.json();
                        statusEl.textContent = JSON.stringify(data, null, 2);
                    }

                    async function startMiner() {
                        const res = await fetch('/miner/start', { method: 'POST' });
                        statusEl.textContent = JSON.stringify(await res.json(), null, 2);
                    }

                    async function stopMiner() {
                        const res = await fetch('/miner/stop', { method: 'POST' });
                        statusEl.textContent = JSON.stringify(await res.json(), null, 2);
                    }

                    async function setPower() {
                        const watts = powerEl.value;
                        const res = await fetch(`/miner/power-limit?watts=${encodeURIComponent(watts)}`, { method: 'POST' });
                        statusEl.textContent = JSON.stringify(await res.json(), null, 2);
                    }

                    document.getElementById('refresh').addEventListener('click', loadStatus);
                    document.getElementById('apply-target').addEventListener('click', setTarget);
                    document.getElementById('apply-mode').addEventListener('click', setMode);
                    document.getElementById('start').addEventListener('click', startMiner);
                    document.getElementById('stop').addEventListener('click', stopMiner);
                    document.getElementById('apply-power').addEventListener('click', setPower);

                    loadStatus();
                </script>
            </body>
            </html>
            """

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def ui() -> HTMLResponse:
        return HTMLResponse(ui_markup)

    @app.get("/config", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/config/", response_class=HTMLResponse, include_in_schema=False)
    def config_editor() -> HTMLResponse:
        return HTMLResponse(CONFIG_MARKUP)

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
        return {"parsed": parsed}

    def _load_location(settings_data: Dict[str, Any]) -> Dict[str, Any] | None:
        if not isinstance(settings_data, dict):
            return None
        location = settings_data.get("location")
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

    def _load_weather_sources(settings_data: Dict[str, Any]) -> list[Dict[str, Any]]:
        if not isinstance(settings_data, dict):
            return []
        integrations = settings_data.get("integrations")
        if not isinstance(integrations, dict):
            return []
        sources = integrations.get("weather")
        if not isinstance(sources, list):
            return []
        normalized: list[Dict[str, Any]] = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            provider = source.get("provider")
            if not provider:
                continue
            priority = source.get("priority", 100)
            enabled = bool(source.get("enabled", True))
            try:
                priority_value = int(priority)
            except (TypeError, ValueError):
                priority_value = 100
            normalized.append(
                {
                    "provider": str(provider),
                    "priority": priority_value,
                    "enabled": enabled,
                }
            )
        return sorted(normalized, key=lambda item: item["priority"])

    @app.get("/status")
    def status() -> Dict[str, Any]:
        miner_status = miner.fetch_status()
        snapshot = controller.record_snapshot(indoor_temp_c=21.0, miner_status=miner_status)
        raw_yaml = load_settings_yaml()
        settings_data = parse_settings_yaml(raw_yaml)
        location = _load_location(settings_data)
        weather_payload: Dict[str, Any] | None = None
        if location:
            sources = _load_weather_sources(settings_data)
            last_error: Dict[str, Any] | None = None
            for source in sources:
                if not source["enabled"]:
                    continue
                provider = source["provider"]
                if provider == "open_meteo":
                    try:
                        weather_payload = fetch_open_meteo_weather(
                            latitude=location["latitude"],
                            longitude=location["longitude"],
                            timezone=location["timezone"],
                        )
                        weather_payload["priority"] = source["priority"]
                        break
                    except Exception as exc:  # pragma: no cover - network defensive fallback
                        last_error = {
                            "provider": provider,
                            "error": str(exc),
                            "priority": source["priority"],
                        }
                        continue
                if provider == "met_no":
                    try:
                        weather_payload = fetch_met_no_weather(
                            latitude=location["latitude"],
                            longitude=location["longitude"],
                            altitude_m=location.get("altitude_m"),
                        )
                        weather_payload["priority"] = source["priority"]
                        break
                    except Exception as exc:  # pragma: no cover - network defensive fallback
                        last_error = {
                            "provider": provider,
                            "error": str(exc),
                            "priority": source["priority"],
                        }
                        continue
                else:
                    last_error = {
                        "provider": provider,
                        "error": "Unsupported weather provider.",
                        "priority": source["priority"],
                    }
                    continue
            if weather_payload is None and last_error is not None:
                weather_payload = last_error
        if weather_payload is not None:
            weather_payload = {"location": location, **weather_payload}
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
        devices = settings_data.get("devices", {}) if isinstance(settings_data, dict) else {}
        zont_devices = devices.get("zont", []) if isinstance(devices, dict) else []
        whatsminer_devices = devices.get("whatsminer", []) if isinstance(devices, dict) else []

        cards = []
        for device in zont_devices or []:
            label = f"zont {device.get('device_id', 'unknown')}"
            payload = {}
            cards.append((label, payload))

        for device in whatsminer_devices or []:
            label = f"whatsminer {device.get('device_id', 'unknown')}"
            instance = Whatsminer(
                host=device.get("host"),
                port=device.get("port", config.miner.port),
                login=device.get("login"),
                password=device.get("password"),
                timeout=config.miner.timeout,
            )
            payload = instance.fetch_status()
            cards.append((label, payload))

        card_markup = ""
        for label, payload in cards:
            card_markup += (
                "<div class=\"card\">"
                f"<div class=\"row\"><strong>{escape(str(label))}</strong></div>"
                f"<pre>{escape(json.dumps(payload, ensure_ascii=False, indent=2))}</pre>"
                "</div>"
            )

        page_markup = f"""
            <!doctype html>
            <html lang="en">
            <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="width=device-width, initial-scale=1" />
                <title>Devices</title>
                <style>
                    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #0f172a; background: #f8fafc; }}
                    h1 {{ margin-top: 0; }}
                    .card {{ background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); margin-bottom: 16px; }}
                    .row {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
                    pre {{ background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 6px; overflow-x: auto; }}
                    .muted {{ color: #64748b; }}
                </style>
            </head>
            <body>
                <h1>Devices</h1>
                <p class="muted">Live status snapshot per device.</p>
                {card_markup or '<p class="muted">No devices configured.</p>'}
            </body>
            </html>
            """
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
