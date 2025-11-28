from __future__ import annotations

import logging
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

try:  # Lazy import to allow a diagnostic ASGI fallback if dependencies are missing
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    from proof_of_heat.config import DEFAULT_CONFIG, AppConfig
    from proof_of_heat.plugins.base import human_readable_mode
    from proof_of_heat.plugins.whatsminer import Whatsminer
    from proof_of_heat.services.temperature_control import TemperatureController
except Exception as exc:  # pragma: no cover - defensive import guard
    FastAPI = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment]
    HTMLResponse = JSONResponse = None  # type: ignore[assignment]
    DEFAULT_CONFIG = AppConfig = human_readable_mode = Whatsminer = TemperatureController = None  # type: ignore[assignment]
    _startup_error = exc


def create_app(config: AppConfig = DEFAULT_CONFIG) -> FastAPI:
    logger.info("Starting proof-of-heat FastAPI app")
    config.ensure_data_dir()

    logger.debug("Data directory ready at %s", config.data_dir)

    history_file = Path(config.data_dir) / "history.csv"
    miner = Whatsminer(cli_path=config.miner.cli_path, host=config.miner.host)
    controller = TemperatureController(
        config=config, miner=miner, history_file=history_file
    )

    app = FastAPI(title="proof-of-heat MVP", version="0.1.0")

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

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
                    const targetEl = document.getElementById('target');
                    const modeEl = document.getElementById('mode');
                    const powerEl = document.getElementById('power');

                    async function loadStatus() {
                        statusEl.textContent = 'Loading...';
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
                        } catch (err) {
                            statusEl.textContent = 'Failed to load status: ' + err;
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

    @app.get("/status")
    def status() -> Dict[str, Any]:
        miner_status = miner.fetch_status()
        snapshot = controller.record_snapshot(indoor_temp_c=21.0, miner_status=miner_status)
        return {
            "mode": config.mode,
            "mode_label": human_readable_mode(config.mode),
            "target_temperature_c": config.target_temperature_c,
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
