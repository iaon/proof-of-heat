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
    from proof_of_heat.services.device_polling import DevicePoller
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
                <p>
                    <a href="/config">Edit configuration</a>
                    · <a href="/metrics">Metrics chart</a>
                </p>
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
    metrics_markup = """
            <!doctype html>
            <html lang="en">
            <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="width=device-width, initial-scale=1" />
                <title>Metrics chart</title>
                <style>
                    body { font-family: system-ui, sans-serif; margin: 24px; color: #0f172a; background: #f8fafc; }
                    h1 { margin-top: 0; }
                    .card { background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); margin-bottom: 16px; }
                    .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
                    label { font-weight: 600; }
                    select, input { padding: 6px 8px; border-radius: 4px; border: 1px solid #cbd5e1; }
                    button { cursor: pointer; padding: 8px 12px; border: none; background: #2563eb; color: white; border-radius: 4px; }
                    .muted { color: #64748b; }
                    canvas { max-width: 100%; }
                </style>
                <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            </head>
            <body>
                <h1>Metrics chart</h1>
                <p class="muted">Select a device + metric to plot history over time.</p>

                <div class="card">
                    <div class="row" style="margin-bottom:12px;">
                        <label for="device-type">Device type</label>
                        <select id="device-type"></select>

                        <label for="device-id">Device id</label>
                        <select id="device-id"></select>

                        <label for="metric">Metric</label>
                        <select id="metric"></select>
                    </div>
                    <div class="row">
                        <label for="start">Start</label>
                        <input id="start" type="datetime-local" />
                        <label for="end">End</label>
                        <input id="end" type="datetime-local" />
                        <button id="apply">Load</button>
                    </div>
                </div>

                <div class="card">
                    <canvas id="chart" height="120"></canvas>
                    <p id="empty" class="muted"></p>
                </div>

                <script>
                    const deviceTypeEl = document.getElementById('device-type');
                    const deviceIdEl = document.getElementById('device-id');
                    const metricEl = document.getElementById('metric');
                    const startEl = document.getElementById('start');
                    const endEl = document.getElementById('end');
                    const emptyEl = document.getElementById('empty');
                    const ctx = document.getElementById('chart').getContext('2d');
                    let chart;

                    function setOptions(select, options) {
                        select.innerHTML = '';
                        const placeholder = document.createElement('option');
                        placeholder.value = '';
                        placeholder.textContent = '—';
                        select.appendChild(placeholder);
                        options.forEach((item) => {
                            const opt = document.createElement('option');
                            opt.value = item;
                            opt.textContent = item;
                            select.appendChild(opt);
                        });
                    }

                    function toInputValue(date) {
                        const pad = (num) => String(num).padStart(2, '0');
                        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
                    }

                    function toIsoWithOffset(value) {
                        if (!value) {
                            return '';
                        }
                        const date = new Date(value);
                        if (Number.isNaN(date.getTime())) {
                            return '';
                        }
                        const pad = (num) => String(num).padStart(2, '0');
                        const tzOffset = -date.getTimezoneOffset();
                        const sign = tzOffset >= 0 ? '+' : '-';
                        const offsetHours = pad(Math.floor(Math.abs(tzOffset) / 60));
                        const offsetMinutes = pad(Math.abs(tzOffset) % 60);
                        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${sign}${offsetHours}:${offsetMinutes}`;
                    }

                    function formatDateTime(value) {
                        return new Intl.DateTimeFormat('ru-RU', {
                            year: 'numeric',
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit',
                            second: '2-digit',
                            hour12: false,
                        }).format(new Date(value));
                    }

                    async function loadDeviceTypes() {
                        const res = await fetch('/api/metrics/device-types');
                        const data = await res.json();
                        setOptions(deviceTypeEl, data.device_types || []);
                    }

                    async function loadDeviceIds() {
                        const type = deviceTypeEl.value;
                        if (!type) {
                            setOptions(deviceIdEl, []);
                            return;
                        }
                        const res = await fetch(`/api/metrics/device-ids?device_type=${encodeURIComponent(type)}`);
                        const data = await res.json();
                        setOptions(deviceIdEl, data.device_ids || []);
                    }

                    async function loadMetrics() {
                        const type = deviceTypeEl.value;
                        const id = deviceIdEl.value;
                        if (!type || !id) {
                            setOptions(metricEl, []);
                            return;
                        }
                        const res = await fetch(`/api/metrics/metric-names?device_type=${encodeURIComponent(type)}&device_id=${encodeURIComponent(id)}`);
                        const data = await res.json();
                        setOptions(metricEl, data.metrics || []);
                    }

                    async function loadChart() {
                        const type = deviceTypeEl.value;
                        const id = deviceIdEl.value;
                        const metric = metricEl.value;
                        if (!type || !id || !metric) {
                            return;
                        }
                        const start = startEl.value;
                        const end = endEl.value;
                        const params = new URLSearchParams({
                            device_type: type,
                            device_id: id,
                            metric: metric,
                        });
                        if (start) {
                            params.set('start', toIsoWithOffset(start));
                        }
                        if (end) {
                            params.set('end', toIsoWithOffset(end));
                        }
                        const res = await fetch(`/api/metrics/data?${params.toString()}`);
                        const data = await res.json();
                        const points = data.points || [];
                        const gapMs = 10 * 60 * 1000;
                        const series = [];
                        points.forEach((point, index) => {
                            const ts = point.ts;
                            if (index > 0) {
                                const prevTs = points[index - 1].ts;
                                if (ts - prevTs > gapMs) {
                                    series.push({ x: prevTs + gapMs, y: null });
                                }
                            }
                            series.push({ x: ts, y: point.value });
                        });

                        if (chart) {
                            chart.destroy();
                        }
                        if (!points.length) {
                            emptyEl.textContent = 'No data for the selected range.';
                        } else {
                            emptyEl.textContent = '';
                        }
                        chart = new Chart(ctx, {
                            type: 'line',
                            data: {
                                datasets: [{
                                    label: `${type} ${id} · ${metric}`,
                                    data: series,
                                    borderColor: '#2563eb',
                                    backgroundColor: 'rgba(37, 99, 235, 0.15)',
                                    tension: 0.25,
                                    fill: true,
                                    spanGaps: false,
                                }],
                            },
                            options: {
                                responsive: true,
                                scales: {
                                    x: {
                                        type: 'linear',
                                        ticks: {
                                            callback: (value) => formatDateTime(value),
                                        },
                                    },
                                    y: { beginAtZero: false },
                                },
                                plugins: {
                                    tooltip: {
                                        callbacks: {
                                            title: (items) => {
                                                if (!items.length) {
                                                    return '';
                                                }
                                                return formatDateTime(items[0].parsed.x);
                                            },
                                        },
                                    },
                                },
                            },
                        });
                    }

                    deviceTypeEl.addEventListener('change', async () => {
                        await loadDeviceIds();
                        await loadMetrics();
                        loadChart();
                    });
                    deviceIdEl.addEventListener('change', async () => {
                        await loadMetrics();
                        loadChart();
                    });
                    metricEl.addEventListener('change', loadChart);
                    document.getElementById('apply').addEventListener('click', loadChart);

                    const now = new Date();
                    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
                    startEl.value = toInputValue(yesterday);
                    endEl.value = toInputValue(now);

                    loadDeviceTypes();
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

    @app.get("/metrics", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/metrics/", response_class=HTMLResponse, include_in_schema=False)
    def metrics_view() -> HTMLResponse:
        return HTMLResponse(metrics_markup)

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
        latest_payloads = device_poller.get_latest_payloads()

        cards = []
        for device in zont_devices or []:
            label = f"zont {device.get('device_id', 'unknown')}"
            payload = latest_payloads.get(f"zont:{device.get('device_id', 'unknown')}", {})
            cards.append((label, payload))

        for device in whatsminer_devices or []:
            label = f"whatsminer {device.get('device_id', 'unknown')}"
            payload = latest_payloads.get(
                f"whatsminer:{device.get('device_id', 'unknown')}",
                {},
            )
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
