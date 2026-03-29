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
                const rootPath = __ROOT_PATH_JSON__;
                const apiUrl = (path) => `${rootPath}${path}`;
                const settingsEl = document.getElementById('settings');
                const previewEl = document.getElementById('preview');

                async function refreshPreview(data) {
                    previewEl.textContent = JSON.stringify(data, null, 2);
                }

                async function loadSettings() {
                    previewEl.textContent = 'Loading...';
                    const res = await fetch(apiUrl('/api/config'));
                    const data = await res.json();
                    if (!res.ok) {
                        previewEl.textContent = 'Error: ' + (data.detail || 'Failed to load');
                        return;
                    }
                    settingsEl.value = data.raw_yaml || '';
                    await refreshPreview(data.parsed || {});
                }

                async function saveSettings() {
                    const res = await fetch(apiUrl('/api/config'), {
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
    from fastapi import FastAPI, HTTPException, Request
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
except Exception as exc:  # pragma: no cover - defensive import guard
    FastAPI = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment]
    Request = Any  # type: ignore[assignment]
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
        )

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
                    <a href="__ROOT_PATH__/config">Edit configuration</a>
                    · <a href="__ROOT_PATH__/metrics">Metrics chart</a>
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
                    const rootPath = __ROOT_PATH_JSON__;
                    const apiUrl = (path) => `${rootPath}${path}`;
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
                            const res = await fetch(apiUrl('/status'));
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
                        const res = await fetch(apiUrl(`/target-temperature?temp_c=${encodeURIComponent(temp)}`), { method: 'POST' });
                        const data = await res.json();
                        statusEl.textContent = JSON.stringify(data, null, 2);
                    }

                    async function setMode() {
                        const mode = modeEl.value;
                        const res = await fetch(apiUrl(`/mode/${mode}`), { method: 'POST' });
                        const data = await res.json();
                        statusEl.textContent = JSON.stringify(data, null, 2);
                    }

                    async function startMiner() {
                        const res = await fetch(apiUrl('/miner/start'), { method: 'POST' });
                        statusEl.textContent = JSON.stringify(await res.json(), null, 2);
                    }

                    async function stopMiner() {
                        const res = await fetch(apiUrl('/miner/stop'), { method: 'POST' });
                        statusEl.textContent = JSON.stringify(await res.json(), null, 2);
                    }

                    async function setPower() {
                        const watts = powerEl.value;
                        const res = await fetch(apiUrl(`/miner/power-limit?watts=${encodeURIComponent(watts)}`), { method: 'POST' });
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
            <html lang="ru">
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
                    .metric-rows { display: flex; flex-direction: column; gap: 12px; min-width: 520px; flex: 1 1 auto; }
                    .metric-row { border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px; background: #f8fafc; }
                    .metric-row-head { display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap; }
                    .metric-row-head label { font-size: 12px; color: #334155; }
                    .metric-row-head select { min-width: 140px; }
                    .metric-picker { position: relative; flex: 1 1 320px; min-width: 260px; }
                    .metric-picker input { width: 100%; box-sizing: border-box; }
                    .metric-remove { background: #dc2626; padding: 6px 10px; font-size: 12px; }
                    .metric-dropdown {
                        position: absolute;
                        top: calc(100% + 6px);
                        left: 0;
                        right: 0;
                        max-height: 320px;
                        overflow-y: auto;
                        background: #fff;
                        border: 1px solid #cbd5e1;
                        border-radius: 8px;
                        box-shadow: 0 12px 28px rgba(15, 23, 42, 0.18);
                        z-index: 20;
                    }
                    .metric-option { padding: 8px 10px; border-bottom: 1px solid #e2e8f0; cursor: pointer; }
                    .metric-option:last-child { border-bottom: none; }
                    .metric-option:hover { background: #f1f5f9; }
                    .metric-option.active { background: #dbeafe; }
                    .metric-option .metric-db-name {
                        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
                        font-size: 12px;
                        color: #0f172a;
                        word-break: break-all;
                    }
                    .metric-option .metric-human-name { font-size: 12px; color: #475569; margin-top: 2px; }
                    .metric-empty { padding: 10px; color: #64748b; font-size: 13px; }
                    .metric-meta { margin-top: 8px; font-size: 13px; color: #334155; }
                    .metric-meta code { background: #e2e8f0; color: #0f172a; padding: 2px 6px; border-radius: 4px; }
                    button { cursor: pointer; padding: 8px 12px; border: none; background: #2563eb; color: white; border-radius: 4px; }
                    .muted { color: #64748b; }
                    canvas { max-width: 100%; }
                    @media (max-width: 900px) {
                        .metric-rows { min-width: 260px; width: 100%; }
                        .metric-row-head select, .metric-picker { width: 100%; }
                    }
                </style>
                <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            </head>
            <body>
                <h1>Metrics chart</h1>
                <p class="muted">Select one or more series (device type + device id + metric) to plot history.</p>

                <div class="card">
                    <div class="row" style="margin-bottom:12px;">
                        <label for="metric-rows">Series</label>
                        <div class="metric-rows" id="metric-rows"></div>
                        <button id="add-metric" type="button" title="Add metric">+</button>
                    </div>
                    <div class="row">
                        <label for="start-date">Start</label>
                        <input id="start-date" type="date" />
                        <input id="start-hour" type="number" min="0" max="23" placeholder="HH" />
                        <input id="start-minute" type="number" min="0" max="59" placeholder="MM" />
                        <input id="start-second" type="number" min="0" max="59" placeholder="SS" />
                        <label for="end-date">End</label>
                        <input id="end-date" type="date" />
                        <input id="end-hour" type="number" min="0" max="23" placeholder="HH" />
                        <input id="end-minute" type="number" min="0" max="59" placeholder="MM" />
                        <input id="end-second" type="number" min="0" max="59" placeholder="SS" />
                        <button id="apply">Load</button>
                    </div>
                </div>

                <div class="card">
                    <canvas id="chart" height="120"></canvas>
                    <p id="empty" class="muted"></p>
                </div>

                <script>
                    const rootPath = __ROOT_PATH_JSON__;
                    const apiUrl = (path) => `${rootPath}${path}`;
                    const metricRowsEl = document.getElementById('metric-rows');
                    const addMetricBtn = document.getElementById('add-metric');
                    const startDateEl = document.getElementById('start-date');
                    const startHourEl = document.getElementById('start-hour');
                    const startMinuteEl = document.getElementById('start-minute');
                    const startSecondEl = document.getElementById('start-second');
                    const endDateEl = document.getElementById('end-date');
                    const endHourEl = document.getElementById('end-hour');
                    const endMinuteEl = document.getElementById('end-minute');
                    const endSecondEl = document.getElementById('end-second');
                    const emptyEl = document.getElementById('empty');
                    const ctx = document.getElementById('chart').getContext('2d');
                    let chart;
                    let metricRowSeq = 0;
                    let deviceTypes = [];
                    const metricRows = [];
                    const STORAGE_KEY = 'proof_of_heat_metrics_view_v1';
                    const palette = ['#2563eb','#dc2626','#16a34a','#7c3aed','#ea580c','#0891b2','#db2777','#0f766e'];

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

                    function toDateInputValue(date) {
                        const pad = (num) => String(num).padStart(2, '0');
                        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
                    }

                    function toTimeParts(date) {
                        const pad = (num) => String(num).padStart(2, '0');
                        return { hour: pad(date.getHours()), minute: pad(date.getMinutes()), second: pad(date.getSeconds()) };
                    }

                    function parseDateTimeInput(dateValue, hourValue, minuteValue, secondValue) {
                        if (!dateValue) return null;
                        const [year, month, day] = dateValue.split('-').map(Number);
                        const hour = Number(hourValue);
                        const minute = Number(minuteValue);
                        const second = Number(secondValue);
                        if ([year, month, day, hour, minute, second].some((item) => Number.isNaN(item))) return null;
                        return new Date(year, month - 1, day, hour, minute, second, 0);
                    }

                    function toIsoWithOffset(dateValue, hourValue, minuteValue, secondValue) {
                        const date = parseDateTimeInput(dateValue, hourValue, minuteValue, secondValue);
                        if (!date) return '';
                        const pad = (num) => String(num).padStart(2, '0');
                        const tzOffset = -date.getTimezoneOffset();
                        const sign = tzOffset >= 0 ? '+' : '-';
                        const offsetHours = pad(Math.floor(Math.abs(tzOffset) / 60));
                        const offsetMinutes = pad(Math.abs(tzOffset) % 60);
                        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${sign}${offsetHours}:${offsetMinutes}`;
                    }

                    function formatDateTime(value) {
                        return new Intl.DateTimeFormat('ru-RU', {
                            year: 'numeric', month: '2-digit', day: '2-digit',
                            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
                        }).format(new Date(value));
                    }

                    function parseLocalInputToMs(dateValue, hourValue, minuteValue, secondValue) {
                        const date = parseDateTimeInput(dateValue, hourValue, minuteValue, secondValue);
                        return date ? date.getTime() : null;
                    }

                    function persistState() {
                        const payload = {
                            start: {
                                date: startDateEl.value || '',
                                hour: startHourEl.value || '',
                                minute: startMinuteEl.value || '',
                                second: startSecondEl.value || '',
                            },
                            end: {
                                date: endDateEl.value || '',
                                hour: endHourEl.value || '',
                                minute: endMinuteEl.value || '',
                                second: endSecondEl.value || '',
                            },
                            series: metricRows.map((row) => ({
                                device_type: row.deviceTypeEl.value || '',
                                device_id: row.deviceIdEl.value || '',
                                metric: row.metricValueEl.value || '',
                            })),
                        };
                        try {
                            localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
                        } catch (err) {
                            // Ignore storage failures (private mode/quota).
                        }
                    }

                    function loadState() {
                        try {
                            const raw = localStorage.getItem(STORAGE_KEY);
                            if (!raw) return null;
                            const parsed = JSON.parse(raw);
                            if (!parsed || typeof parsed !== 'object') return null;
                            return parsed;
                        } catch (err) {
                            return null;
                        }
                    }

                    function prettifyMetricName(name) {
                        return name.replaceAll('_', ' ');
                    }

                    function describeZontMetric(metricName) {
                        let match = metricName.match(/^io_thermometers_state_([a-zA-Z0-9]+)_last_value$/);
                        if (match) return `Thermometer ${match[1].slice(0, 8)}: temperature`;
                        match = metricName.match(/^io_thermometers_state_([a-zA-Z0-9]+)_last_value_time$/);
                        if (match) return `Thermometer ${match[1].slice(0, 8)}: last value time (epoch seconds)`;
                        match = metricName.match(/^io_last_boiler_state_(.+)$/);
                        if (match) return `Boiler state: ${prettifyMetricName(match[1])}`;
                        if (metricName.startsWith('io_')) return `I/O metric: ${prettifyMetricName(metricName.slice(3))}`;
                        return `ZONT metric: ${prettifyMetricName(metricName)}`;
                    }

                    function describeMetric(deviceType, metricName) {
                        if (!metricName) return '—';
                        if (deviceType === 'zont') return describeZontMetric(metricName);
                        return prettifyMetricName(metricName);
                    }

                    function updateRemoveButtons() {
                        metricRows.forEach((row) => { row.removeBtn.hidden = metricRows.length <= 1; });
                    }

                    function closeAllMetricDropdowns(exceptRowId = null) {
                        metricRows.forEach((row) => {
                            if (row.id !== exceptRowId) row.dropdownEl.hidden = true;
                        });
                    }

                    function renderRowDropdown(row, filterValue = '') {
                        const query = filterValue.trim().toLowerCase();
                        const filtered = row.options.filter((item) => {
                            if (!query) return true;
                            return item.value.toLowerCase().includes(query) || item.human.toLowerCase().includes(query);
                        });
                        if (!filtered.length) {
                            row.dropdownEl.innerHTML = '<div class="metric-empty">No matching metrics</div>';
                            return;
                        }
                        row.dropdownEl.innerHTML = filtered.map((item) => {
                            const activeClass = item.value === row.metricValueEl.value ? ' active' : '';
                            return `<div class="metric-option${activeClass}" data-value="${item.value}">
                                        <div class="metric-db-name">${item.value}</div>
                                        <div class="metric-human-name">${item.human}</div>
                                    </div>`;
                        }).join('');
                    }

                    function updateRowInfo(row) {
                        const metricName = row.metricValueEl.value || '';
                        row.fullNameEl.textContent = metricName || '—';
                        row.humanEl.textContent = describeMetric(row.deviceTypeEl.value, metricName);
                    }

                    function selectMetricForRow(row, value) {
                        const selected = row.options.find((item) => item.value === value);
                        if (!selected) {
                            row.metricValueEl.value = '';
                            row.searchEl.value = '';
                            updateRowInfo(row);
                            return;
                        }
                        row.metricValueEl.value = selected.value;
                        row.searchEl.value = selected.value;
                        updateRowInfo(row);
                        renderRowDropdown(row, row.searchEl.value);
                    }

                    async function loadDeviceTypes() {
                        const res = await fetch(apiUrl('/api/metrics/device-types'));
                        const data = await res.json();
                        deviceTypes = data.device_types || [];
                        metricRows.forEach((row) => {
                            const prevType = row.deviceTypeEl.value;
                            setOptions(row.deviceTypeEl, deviceTypes);
                            if (prevType && deviceTypes.includes(prevType)) row.deviceTypeEl.value = prevType;
                        });
                    }

                    async function loadDeviceIdsForRow(row) {
                        const type = row.deviceTypeEl.value;
                        if (!type) {
                            setOptions(row.deviceIdEl, []);
                            return;
                        }
                        const res = await fetch(apiUrl(`/api/metrics/device-ids?device_type=${encodeURIComponent(type)}`));
                        const data = await res.json();
                        const prevId = row.deviceIdEl.value;
                        const ids = data.device_ids || [];
                        setOptions(row.deviceIdEl, ids);
                        if (prevId && ids.includes(prevId)) row.deviceIdEl.value = prevId;
                    }

                    async function loadMetricsForRow(row) {
                        const type = row.deviceTypeEl.value;
                        const id = row.deviceIdEl.value;
                        if (!type || !id) {
                            row.options = [];
                            row.metricValueEl.value = '';
                            row.searchEl.value = '';
                            renderRowDropdown(row, '');
                            updateRowInfo(row);
                            return;
                        }
                        const res = await fetch(apiUrl(`/api/metrics/metric-names?device_type=${encodeURIComponent(type)}&device_id=${encodeURIComponent(id)}`));
                        const data = await res.json();
                        const prevMetric = row.metricValueEl.value;
                        row.options = (data.metrics || []).map((value) => ({
                            value,
                            human: describeMetric(type, value),
                        }));
                        if (prevMetric && row.options.some((item) => item.value === prevMetric)) {
                            selectMetricForRow(row, prevMetric);
                        } else {
                            row.metricValueEl.value = '';
                            row.searchEl.value = '';
                            updateRowInfo(row);
                        }
                        renderRowDropdown(row, row.searchEl.value);
                    }

                    function collectSelectedSeries() {
                        return metricRows
                            .map((row) => ({
                                deviceType: row.deviceTypeEl.value,
                                deviceId: row.deviceIdEl.value,
                                metric: row.metricValueEl.value,
                                label: row.searchEl.value || row.metricValueEl.value,
                            }))
                            .filter((item) => Boolean(item.deviceType && item.deviceId && item.metric));
                    }

                    function createMetricRow(initialState = null) {
                        metricRowSeq += 1;
                        const rowId = metricRowSeq;
                        const rowEl = document.createElement('div');
                        rowEl.className = 'metric-row';
                        rowEl.innerHTML = `
                            <div class="metric-row-head">
                                <label for="device-type-${rowId}">Device type</label>
                                <select id="device-type-${rowId}"></select>
                                <label for="device-id-${rowId}">Device id</label>
                                <select id="device-id-${rowId}"></select>
                                <div class="metric-picker">
                                    <input id="metric-search-${rowId}" type="text" autocomplete="off" placeholder="Select or search metric..." />
                                    <input id="metric-value-${rowId}" type="hidden" />
                                    <div id="metric-dropdown-${rowId}" class="metric-dropdown" hidden></div>
                                </div>
                                <button id="metric-remove-${rowId}" type="button" class="metric-remove">−</button>
                            </div>
                            <div class="metric-meta">
                                <div>Full DB metric name: <code id="metric-full-name-${rowId}">—</code></div>
                                <div>Description: <span id="metric-human-${rowId}">—</span></div>
                            </div>
                        `;
                        metricRowsEl.appendChild(rowEl);

                        const row = {
                            id: rowId,
                            rowEl,
                            deviceTypeEl: rowEl.querySelector(`#device-type-${rowId}`),
                            deviceIdEl: rowEl.querySelector(`#device-id-${rowId}`),
                            searchEl: rowEl.querySelector(`#metric-search-${rowId}`),
                            metricValueEl: rowEl.querySelector(`#metric-value-${rowId}`),
                            dropdownEl: rowEl.querySelector(`#metric-dropdown-${rowId}`),
                            removeBtn: rowEl.querySelector(`#metric-remove-${rowId}`),
                            fullNameEl: rowEl.querySelector(`#metric-full-name-${rowId}`),
                            humanEl: rowEl.querySelector(`#metric-human-${rowId}`),
                            options: [],
                        };
                        metricRows.push(row);
                        setOptions(row.deviceTypeEl, deviceTypes);
                        setOptions(row.deviceIdEl, []);
                        row.initialState = initialState;

                        row.deviceTypeEl.addEventListener('change', async () => {
                            await loadDeviceIdsForRow(row);
                            await loadMetricsForRow(row);
                            persistState();
                            loadChart();
                        });
                        row.deviceIdEl.addEventListener('change', async () => {
                            await loadMetricsForRow(row);
                            persistState();
                            loadChart();
                        });
                        row.searchEl.addEventListener('focus', () => {
                            closeAllMetricDropdowns(row.id);
                            renderRowDropdown(row, row.searchEl.value);
                            row.dropdownEl.hidden = false;
                        });
                        row.searchEl.addEventListener('input', () => {
                            row.metricValueEl.value = '';
                            updateRowInfo(row);
                            closeAllMetricDropdowns(row.id);
                            renderRowDropdown(row, row.searchEl.value);
                            row.dropdownEl.hidden = false;
                            persistState();
                            loadChart();
                        });
                        row.dropdownEl.addEventListener('click', (event) => {
                            const option = event.target.closest('.metric-option');
                            if (!option) return;
                            selectMetricForRow(row, option.getAttribute('data-value') || '');
                            row.dropdownEl.hidden = true;
                            persistState();
                            loadChart();
                        });
                        row.removeBtn.addEventListener('click', () => {
                            if (metricRows.length <= 1) return;
                            const idx = metricRows.findIndex((item) => item.id === row.id);
                            if (idx >= 0) {
                                metricRows.splice(idx, 1);
                                row.rowEl.remove();
                                updateRemoveButtons();
                                persistState();
                                loadChart();
                            }
                        });

                        updateRowInfo(row);
                        renderRowDropdown(row, '');
                        updateRemoveButtons();
                        return row;
                    }

                    async function loadChart() {
                        const selectedSeries = collectSelectedSeries();
                        if (!selectedSeries.length) return;

                        const startDate = startDateEl.value;
                        const startHour = startHourEl.value;
                        const startMinute = startMinuteEl.value;
                        const startSecond = startSecondEl.value;
                        const endDate = endDateEl.value;
                        const endHour = endHourEl.value;
                        const endMinute = endMinuteEl.value;
                        const endSecond = endSecondEl.value;
                        const startMs = parseLocalInputToMs(startDate, startHour, startMinute, startSecond);
                        const endMs = parseLocalInputToMs(endDate, endHour, endMinute, endSecond);

                        const requests = selectedSeries.map(async (seriesItem) => {
                            const params = new URLSearchParams({
                                device_type: seriesItem.deviceType,
                                device_id: seriesItem.deviceId,
                                metric: seriesItem.metric,
                            });
                            if (startDate) params.set('start', toIsoWithOffset(startDate, startHour, startMinute, startSecond));
                            if (endDate) params.set('end', toIsoWithOffset(endDate, endHour, endMinute, endSecond));
                            const res = await fetch(apiUrl(`/api/metrics/data?${params.toString()}`));
                            const data = await res.json();
                            return {
                                label: `${seriesItem.deviceType} ${seriesItem.deviceId} · ${seriesItem.label}`,
                                points: data.points || [],
                            };
                        });
                        const metricSeries = await Promise.all(requests);

                        if (chart) chart.destroy();
                        const datasets = metricSeries.map((seriesData, index) => {
                            const gapMs = 10 * 60 * 1000;
                            const series = [];
                            seriesData.points.forEach((point, pointIndex) => {
                                const ts = point.ts;
                                if (pointIndex > 0) {
                                    const prevTs = seriesData.points[pointIndex - 1].ts;
                                    if (ts - prevTs > gapMs) series.push({ x: prevTs + gapMs, y: null });
                                }
                                series.push({ x: ts, y: point.value });
                            });
                            const color = palette[index % palette.length];
                            return {
                                label: seriesData.label,
                                data: series,
                                borderColor: color,
                                backgroundColor: `${color}22`,
                                tension: 0.25,
                                fill: false,
                                spanGaps: false,
                            };
                        });

                        const hasPoints = metricSeries.some((seriesData) => seriesData.points.length > 0);
                        emptyEl.textContent = hasPoints ? '' : 'No data for the selected range.';
                        chart = new Chart(ctx, {
                            type: 'line',
                            data: { datasets },
                            options: {
                                responsive: true,
                                scales: {
                                    x: {
                                        type: 'linear',
                                        min: startMs ?? undefined,
                                        max: endMs ?? undefined,
                                        ticks: { callback: (value) => formatDateTime(value) },
                                    },
                                    y: { beginAtZero: false },
                                },
                                plugins: {
                                    tooltip: {
                                        callbacks: {
                                            title: (items) => items.length ? formatDateTime(items[0].parsed.x) : '',
                                        },
                                    },
                                },
                            },
                        });
                    }

                    document.addEventListener('click', (event) => {
                        const clickedInsideAnyRow = metricRows.some((row) => row.rowEl.contains(event.target));
                        if (!clickedInsideAnyRow) closeAllMetricDropdowns();
                    });
                    addMetricBtn.addEventListener('click', () => {
                        createMetricRow();
                        persistState();
                    });
                    document.getElementById('apply').addEventListener('click', () => {
                        persistState();
                        loadChart();
                    });

                    const savedState = loadState();
                    if (savedState && savedState.start) {
                        startDateEl.value = savedState.start.date || '';
                        startHourEl.value = savedState.start.hour || '';
                        startMinuteEl.value = savedState.start.minute || '';
                        startSecondEl.value = savedState.start.second || '';
                    }
                    if (savedState && savedState.end) {
                        endDateEl.value = savedState.end.date || '';
                        endHourEl.value = savedState.end.hour || '';
                        endMinuteEl.value = savedState.end.minute || '';
                        endSecondEl.value = savedState.end.second || '';
                    }
                    if (!startDateEl.value || !endDateEl.value) {
                        const now = new Date();
                        const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
                        startDateEl.value = toDateInputValue(yesterday);
                        endDateEl.value = toDateInputValue(now);
                        const startParts = toTimeParts(yesterday);
                        const endParts = toTimeParts(now);
                        startHourEl.value = startHourEl.value || startParts.hour;
                        startMinuteEl.value = startMinuteEl.value || startParts.minute;
                        startSecondEl.value = startSecondEl.value || startParts.second;
                        endHourEl.value = endHourEl.value || endParts.hour;
                        endMinuteEl.value = endMinuteEl.value || endParts.minute;
                        endSecondEl.value = endSecondEl.value || endParts.second;
                    }

                    const initialSeries = Array.isArray(savedState && savedState.series) && savedState.series.length
                        ? savedState.series
                        : [{}];
                    initialSeries.forEach((series) => createMetricRow(series));

                    async function restoreRowsFromState() {
                        for (const row of metricRows) {
                            const initial = row.initialState || {};
                            if (initial.device_type && deviceTypes.includes(initial.device_type)) {
                                row.deviceTypeEl.value = initial.device_type;
                                await loadDeviceIdsForRow(row);
                            }
                            if (initial.device_id) {
                                const idOptions = Array.from(row.deviceIdEl.options).map((opt) => opt.value);
                                if (idOptions.includes(initial.device_id)) {
                                    row.deviceIdEl.value = initial.device_id;
                                }
                            }
                            await loadMetricsForRow(row);
                            if (initial.metric) {
                                selectMetricForRow(row, initial.metric);
                            }
                        }
                    }

                    loadDeviceTypes().then(async () => {
                        await restoreRowsFromState();
                        persistState();
                        loadChart();
                    });
                </script>
            </body>
            </html>
            """

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
