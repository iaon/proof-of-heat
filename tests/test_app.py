from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request

from proof_of_heat import main
from proof_of_heat.config import AppConfig


class DummyMiner:
    def __init__(self, *args, **kwargs):
        self.name = "dummy"

    def fetch_status(self):
        return {"power": 1000, "fan_speed": 60}

    def set_power_limit(self, watts: int):
        return {"power_limit": watts}

    def start(self):
        return {"status": "started"}

    def stop(self):
        return {"status": "stopped"}


class DummyController:
    def __init__(self, config, miner, history_file):
        self.config = config
        self.miner = miner
        self.history_file = history_file

    def record_snapshot(self, indoor_temp_c, miner_status):
        return SimpleNamespace(
            timestamp="2026-01-01T00:00:00+00:00",
            indoor_temp_c=indoor_temp_c,
            miner_status=miner_status,
        )

    def set_mode(self, mode):
        self.config.mode = mode

    def set_target(self, temp_c):
        self.config.target_temperature_c = temp_c


class DummyDevicePoller:
    latest_payloads = {}
    latest_control_inputs = None

    def __init__(self, settings_data, data_dir=None):
        self.settings_data = settings_data
        self.data_dir = data_dir

    def start(self):
        return None

    def shutdown(self):
        return None

    def update_settings(self, parsed):
        self.settings_data = parsed

    def list_metric_device_types(self):
        return []

    def list_metric_device_ids(self, device_type):
        return []

    def list_metric_names(self, device_type, device_id):
        return []

    def get_metric_series(self, device_type, device_id, metric, start_ms=None, end_ms=None):
        return []

    def get_latest_payloads(self):
        return self.latest_payloads.copy()

    def get_latest_control_inputs(self):
        return self.latest_control_inputs


def build_routes(tmp_path, monkeypatch, parsed_settings=None, latest_payloads=None):
    settings = parsed_settings or {"devices": {}}
    DummyDevicePoller.latest_payloads = latest_payloads or {}
    DummyDevicePoller.latest_control_inputs = None
    monkeypatch.setattr(main, "Whatsminer", DummyMiner, raising=False)
    monkeypatch.setattr(main, "TemperatureController", DummyController, raising=False)
    monkeypatch.setattr(main, "DevicePoller", DummyDevicePoller, raising=False)
    monkeypatch.setattr(main, "human_readable_mode", lambda mode: mode.title(), raising=False)
    monkeypatch.setattr(main, "load_settings_yaml", lambda: "devices: {}\n", raising=False)
    monkeypatch.setattr(main, "parse_settings_yaml", lambda raw_yaml: settings, raising=False)
    monkeypatch.setattr(main, "save_settings_yaml", lambda raw_yaml: settings, raising=False)
    monkeypatch.setattr(main, "FastAPI", FastAPI, raising=False)
    monkeypatch.setattr(main, "HTMLResponse", HTMLResponse, raising=False)
    monkeypatch.setattr(main, "JSONResponse", JSONResponse, raising=False)
    monkeypatch.setattr(main, "_startup_error", None)

    app = main.create_app(AppConfig(data_dir=tmp_path))
    return {
        route.path: route.endpoint
        for route in app.routes
        if hasattr(route, "path") and hasattr(route, "endpoint")
    }


def make_request(path: str, root_path: str = "") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "root_path": root_path,
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def test_health(tmp_path, monkeypatch):
    routes = build_routes(tmp_path, monkeypatch)
    payload = routes["/health"]()
    assert payload == {"status": "ok"}


def test_ui_served(tmp_path, monkeypatch):
    routes = build_routes(tmp_path, monkeypatch)
    resp = routes["/"](make_request("/"))
    assert resp.status_code == 200
    assert "proof-of-heat MVP" in resp.body.decode()


def test_ui_respects_root_path(tmp_path, monkeypatch):
    routes = build_routes(tmp_path, monkeypatch)

    ui_resp = routes["/"](make_request("/", root_path="/app"))
    assert ui_resp.status_code == 200
    ui_markup = ui_resp.body.decode()
    assert 'href="/app/config"' in ui_markup
    assert 'href="/app/metrics"' in ui_markup
    assert 'id="control-inputs"' in ui_markup
    assert 'const rootPath = "/app";' in ui_markup

    config_resp = routes["/config"](make_request("/config", root_path="/app"))
    assert config_resp.status_code == 200
    assert 'const rootPath = "/app";' in config_resp.body.decode()


def test_status_snapshot(tmp_path, monkeypatch):
    routes = build_routes(tmp_path, monkeypatch)
    payload = routes["/status"]()
    assert payload["mode"] == "comfort"
    assert payload["target_temperature_c"]
    assert payload["latest_snapshot"]["miner_status"]["power"] == 1000


def test_status_uses_virtual_weather_device_payload(tmp_path, monkeypatch):
    parsed_settings = {
        "devices": {
            "open_meteo": [
                {
                    "device_id": 1001,
                    "type": "virtual",
                }
            ]
        }
    }
    latest_payloads = {
        "open_meteo:1001": {
            "timestamp": "2026-03-29T10:15:00",
            "payload": {
                "provider": "open_meteo",
                "device_id": "1001",
                "type": "virtual",
                "location": {"name": "Moscow"},
                "current": {"temperature": 4.2},
            },
        }
    }

    routes = build_routes(
        tmp_path,
        monkeypatch,
        parsed_settings=parsed_settings,
        latest_payloads=latest_payloads,
    )
    payload = routes["/status"]()

    assert payload["weather"]["provider"] == "open_meteo"
    assert payload["weather"]["type"] == "virtual"
    assert payload["weather"]["polled_at"] == "2026-03-29T10:15:00"


def test_devices_view_lists_virtual_weather_devices(tmp_path, monkeypatch):
    parsed_settings = {
        "devices": {
            "open_meteo": [
                {
                    "device_id": 1001,
                    "type": "virtual",
                }
            ]
        }
    }
    latest_payloads = {
        "open_meteo:1001": {
            "timestamp": "2026-03-29T10:15:00",
            "payload": {"provider": "open_meteo", "current": {"temperature": 4.2}},
        }
    }

    routes = build_routes(
        tmp_path,
        monkeypatch,
        parsed_settings=parsed_settings,
        latest_payloads=latest_payloads,
    )
    resp = routes["/devices"]()
    body = resp.body.decode()

    assert "open_meteo 1001 (virtual)" in body


def test_control_inputs_api_returns_latest_payload(tmp_path, monkeypatch):
    routes = build_routes(tmp_path, monkeypatch)
    DummyDevicePoller.latest_control_inputs = {
        "ts": 123,
        "indoor_temp": 21.5,
        "indoor_temp_source": "zont:12000:room_temp",
        "outdoor_temp": 3.0,
        "outdoor_temp_source": "open_meteo:1001:temperature_2m",
        "supply_temp": None,
        "supply_temp_source": None,
        "power": 900.0,
        "power_sources": ["whatsminer:1:power"],
    }

    payload = routes["/api/control-inputs/latest"]()

    assert payload["data"] is not None
    assert payload["data"]["indoor_temp"] == 21.5
    assert payload["data"]["power_sources"] == ["whatsminer:1:power"]
