from types import SimpleNamespace

import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request

from proof_of_heat import main
from proof_of_heat.config import AppConfig


class DummyMiner:
    fetch_status_response = {"power": 1000, "fan_speed": 60}
    set_power_limit_calls = []
    set_power_percent_calls = []
    start_calls = 0
    start_response = {"status": "started"}
    init_kwargs = []

    def __init__(self, *args, **kwargs):
        self.name = "dummy"
        self.init_kwargs.append(kwargs)

    def fetch_status(self):
        return self.fetch_status_response

    def set_power_limit(self, watts: int):
        self.set_power_limit_calls.append(watts)
        return {"power_limit": watts}

    def set_power_percent(self, percent: int):
        self.set_power_percent_calls.append(percent)
        return {"power_percent": percent}

    def start(self):
        type(self).start_calls += 1
        return self.start_response

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
    metric_catalog = {}
    economics_metadata = {
        "enabled": True,
        "currencies": {"crypto": "BTC", "fiat": "RUB"},
        "metrics": [],
        "current_metrics": [],
        "labels": {},
        "presets": {},
        "stale_after_ms_by_metric": {},
        "device_type": "economics",
        "device_id": "market",
    }

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

    def get_metric_catalog(self):
        return self.metric_catalog.copy()

    def get_economics_metadata(self):
        return self.economics_metadata.copy()

    def get_metric_series(self, device_type, device_id, metric, start_ms=None, end_ms=None):
        return []

    def get_latest_payloads(self):
        return self.latest_payloads.copy()

    def get_latest_control_inputs(self):
        return self.latest_control_inputs


def build_routes(tmp_path, monkeypatch, parsed_settings=None, latest_payloads=None):
    app = build_test_app(
        tmp_path,
        monkeypatch,
        parsed_settings=parsed_settings,
        latest_payloads=latest_payloads,
    )
    routes = {}
    for route in app.routes:
        if not hasattr(route, "path") or not hasattr(route, "endpoint"):
            continue
        routes.setdefault(route.path, route.endpoint)
        methods = getattr(route, "methods", None) or []
        for method in methods:
            routes[f"{method} {route.path}"] = route.endpoint
    return routes


def build_test_app(tmp_path, monkeypatch, parsed_settings=None, latest_payloads=None):
    settings = parsed_settings or {"devices": {}}
    DummyDevicePoller.latest_payloads = latest_payloads or {}
    DummyDevicePoller.latest_control_inputs = None
    DummyDevicePoller.metric_catalog = {}
    DummyDevicePoller.economics_metadata = {
        "enabled": True,
        "currencies": {"crypto": "BTC", "fiat": "RUB"},
        "metrics": [],
        "current_metrics": [],
        "labels": {},
        "presets": {},
        "stale_after_ms_by_metric": {},
        "device_type": "economics",
        "device_id": "market",
    }
    DummyMiner.fetch_status_response = {"power": 1000, "fan_speed": 60}
    DummyMiner.set_power_limit_calls = []
    DummyMiner.set_power_percent_calls = []
    DummyMiner.start_calls = 0
    DummyMiner.start_response = {"status": "started"}
    DummyMiner.init_kwargs = []
    main._clear_fixed_supply_temp_runtime_state()

    def save_settings(raw_yaml):
        parsed = yaml.safe_load(raw_yaml) or {}
        settings.clear()
        settings.update(parsed)
        return settings

    monkeypatch.setattr(main, "Whatsminer", DummyMiner, raising=False)
    monkeypatch.setattr(main, "TemperatureController", DummyController, raising=False)
    monkeypatch.setattr(main, "DevicePoller", DummyDevicePoller, raising=False)
    monkeypatch.setattr(main, "human_readable_mode", lambda mode: mode.title(), raising=False)
    monkeypatch.setattr(main, "load_settings_yaml", lambda: "devices: {}\n", raising=False)
    monkeypatch.setattr(main, "parse_settings_yaml", lambda raw_yaml: settings, raising=False)
    monkeypatch.setattr(main, "save_settings_yaml", save_settings, raising=False)
    monkeypatch.setattr(main, "FastAPI", FastAPI, raising=False)
    monkeypatch.setattr(main, "HTMLResponse", HTMLResponse, raising=False)
    monkeypatch.setattr(main, "JSONResponse", JSONResponse, raising=False)
    monkeypatch.setattr(main, "_startup_error", None)
    monkeypatch.setattr(main, "APP_VERSION", "1.2.3-testsha", raising=False)

    return main.create_app(AppConfig(data_dir=tmp_path))


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
    markup = resp.body.decode()
    assert "proof-of-heat MVP" in markup
    assert "Version 1.2.3-testsha" in markup


def test_root_route_uses_fastapi_request_injection(tmp_path, monkeypatch):
    app = build_test_app(tmp_path, monkeypatch)
    route = next(route for route in app.routes if getattr(route, "path", None) == "/")

    assert route.dependant.request_param_name == "request"
    assert route.dependant.query_params == []


def test_create_app_logs_version_on_startup(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="proof_of_heat")
    build_routes(tmp_path, monkeypatch)

    assert "Starting proof-of-heat FastAPI app version 1.2.3-testsha" in caplog.text


def test_ui_respects_root_path(tmp_path, monkeypatch):
    routes = build_routes(tmp_path, monkeypatch)

    ui_resp = routes["/"](make_request("/", root_path="/app"))
    assert ui_resp.status_code == 200
    ui_markup = ui_resp.body.decode()
    assert 'href="/app/config"' in ui_markup
    assert 'href="/app/economics"' in ui_markup
    assert 'href="/app/metrics"' in ui_markup
    assert 'href="/app/heating-curve"' in ui_markup
    assert 'id="control-inputs"' in ui_markup
    assert 'const rootPath = "/app";' in ui_markup
    assert 'Version 1.2.3-testsha' in ui_markup

    config_resp = routes["/config"](make_request("/config", root_path="/app"))
    assert config_resp.status_code == 200
    config_markup = config_resp.body.decode()
    assert 'const rootPath = "/app";' in config_markup
    assert 'Version 1.2.3-testsha' in config_markup

    metrics_resp = routes["/metrics"](make_request("/metrics", root_path="/app"))
    assert metrics_resp.status_code == 200
    metrics_markup = metrics_resp.body.decode()
    assert 'const rootPath = "/app";' in metrics_markup
    assert 'data-preset="economics-rates"' not in metrics_markup
    assert 'data-hours="1"' in metrics_markup
    assert 'data-hours="3"' in metrics_markup
    assert 'data-hours="24"' in metrics_markup
    assert 'Version 1.2.3-testsha' in metrics_markup

    economics_resp = routes["/economics"](make_request("/economics", root_path="/app"))
    assert economics_resp.status_code == 200
    economics_markup = economics_resp.body.decode()
    assert 'const rootPath = "/app";' in economics_markup
    assert 'data-preset="rates"' in economics_markup
    assert 'id="economics-current"' in economics_markup
    assert 'data-hours="1"' in economics_markup
    assert 'data-hours="3"' in economics_markup
    assert 'data-hours="24"' in economics_markup
    assert 'Version 1.2.3-testsha' in economics_markup


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


def test_metrics_catalog_api_returns_catalog(tmp_path, monkeypatch):
    routes = build_routes(tmp_path, monkeypatch)
    DummyDevicePoller.metric_catalog = {
        "open_meteo": {"1001": ["temperature", "windspeed"]},
        "zont": {"12000": ["room_temp"]},
        "economics": {"market": ["exchange_rate_btc_rub"]},
    }

    payload = routes["/api/metrics/catalog"]()

    assert payload == {
        "catalog": {
            "open_meteo": {"1001": ["temperature", "windspeed"]},
            "zont": {"12000": ["room_temp"]},
        }
    }


def test_economics_api_returns_latest_payload_and_catalog(tmp_path, monkeypatch):
    routes = build_routes(tmp_path, monkeypatch)
    DummyDevicePoller.economics_metadata = {
        "enabled": True,
        "currencies": {"crypto": "BTC", "fiat": "EUR"},
        "metrics": [
            "exchange_rate_btc_usd",
            "exchange_rate_usd_eur",
            "exchange_rate_btc_eur",
            "hashprice_btc_th_day",
        ],
        "current_metrics": [
            "exchange_rate_btc_usd",
            "exchange_rate_usd_eur",
            "exchange_rate_btc_eur",
            "hashprice_btc_th_day",
        ],
        "labels": {
            "exchange_rate_btc_usd": "BTC price in USD",
            "exchange_rate_usd_eur": "USD to EUR exchange rate",
            "exchange_rate_btc_eur": "BTC price in EUR",
            "hashprice_btc_th_day": "Hashprice in BTC per TH per day",
        },
        "presets": {
            "rates": {
                "label": "Rates",
                "metrics": [
                    "exchange_rate_btc_usd",
                    "exchange_rate_usd_eur",
                    "exchange_rate_btc_eur",
                ],
            }
        },
        "stale_after_ms_by_metric": {
            "exchange_rate_btc_usd": 7200000,
            "exchange_rate_usd_eur": 7200000,
            "exchange_rate_btc_eur": 7200000,
            "hashprice_btc_th_day": 7200000,
        },
        "device_type": "economics",
        "device_id": "market",
    }
    DummyDevicePoller.latest_payloads = {
        "economics:market": {
            "timestamp": "2026-04-01T10:00:00+00:00",
            "payload": {
                "derived": {
                    "exchange_rate_btc_eur": 92000,
                    "hashprice_btc_th_day": 0.0000015,
                },
                "errors": [],
            },
        }
    }

    current_payload = routes["/api/economics/current"]()
    catalog_payload = routes["/api/economics/catalog"]()

    assert current_payload == {
        "data": {
            "exchange_rate_btc_eur": 92000,
            "hashprice_btc_th_day": 0.0000015,
        },
        "errors": [],
        "polled_at": "2026-04-01T10:00:00+00:00",
    }
    assert catalog_payload["currencies"] == {"crypto": "BTC", "fiat": "EUR"}
    assert catalog_payload["metrics"] == [
        "exchange_rate_btc_usd",
        "exchange_rate_usd_eur",
        "exchange_rate_btc_eur",
        "hashprice_btc_th_day",
    ]
    assert catalog_payload["labels"]["exchange_rate_btc_eur"] == "BTC price in EUR"
    assert catalog_payload["presets"]["rates"]["metrics"] == [
        "exchange_rate_btc_usd",
        "exchange_rate_usd_eur",
        "exchange_rate_btc_eur",
    ]
    assert catalog_payload["stale_after_ms_by_metric"]["exchange_rate_btc_eur"] == 7_200_000


def test_fixed_power_mode_sets_power_limit_when_summary_is_ready():
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {
            "summary": {
                "power-limit": 3600,
                "up-freq-finish": 1,
            }
        },
    }
    DummyMiner.set_power_limit_calls = []
    miner = DummyMiner()

    result = main._apply_fixed_power_heating_mode(
        miner,
        {
            "heating_mode": {
                "enabled": True,
                "type": "fixed_power",
                "params": {"power_w": 3200},
            }
        },
    )

    assert result == {"power_limit": 3200}
    assert DummyMiner.set_power_limit_calls == [3200]


def test_fixed_power_mode_waits_until_up_freq_finish():
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {
            "summary": {
                "power-limit": 3600,
                "up-freq-finish": 0,
            }
        },
    }
    DummyMiner.set_power_limit_calls = []
    miner = DummyMiner()

    result = main._apply_fixed_power_heating_mode(
        miner,
        {
            "heating_mode": {
                "enabled": True,
                "type": "fixed_power",
                "params": {"power_w": 3200},
            }
        },
    )

    assert result is None
    assert DummyMiner.set_power_limit_calls == []


def test_fixed_supply_temp_mode_sets_calibration_power_limit_on_first_tick():
    DummyMiner.set_power_limit_calls = []
    DummyMiner.set_power_percent_calls = []
    DummyMiner.fetch_status_response = {"code": 0, "msg": {"summary": {"power-limit": 3200, "up-freq-finish": 0, "power": 2500}}}
    state = main.FixedSupplyTempRuntimeState()
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1000,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 42.0,
                    "tolerance_c": 1.0,
                    "correction": 0.0,
                },
            },
        },
        {"ts": 1, "supply_temp": 40.0},
        runtime_state=state,
    )

    assert result == {"power_limit": 3800}
    assert DummyMiner.set_power_limit_calls == [3800]
    assert DummyMiner.set_power_percent_calls == []


def test_fixed_supply_temp_mode_sets_calibration_power_limit():
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {
            "summary": {
                "power-limit": 3200,
                "up-freq-finish": 0,
                "power": 2500,
            }
        },
    }
    DummyMiner.set_power_limit_calls = []
    DummyMiner.set_power_percent_calls = []
    DummyMiner.start_calls = 0
    state = main.FixedSupplyTempRuntimeState(
        signature=("miner01", "miner.local", 4433, 3800, 1000),
    )
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1000,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 42.0,
                    "tolerance_c": 1.0,
                    "correction": 0.0,
                },
            },
        },
        {"ts": 1, "supply_temp": 40.0},
        runtime_state=state,
    )

    assert result == {"power_limit": 3800}
    assert DummyMiner.set_power_limit_calls == [3800]
    assert DummyMiner.set_power_percent_calls == []
    assert state.calibration_requested is True
    assert state.calibration_complete is False
    assert state.baseline_power_w is None


def test_fixed_supply_temp_mode_forces_full_power_at_start_when_miner_is_older_than_app(monkeypatch):
    monkeypatch.setattr(main, "_APP_STARTED_AT_UNIX", 2_000, raising=False)
    DummyMiner.fetch_status_response = {
        "code": 0,
        "when": 10_000,
        "msg": {
            "summary": {
                "bootup-time": 9_000,
                "power-limit": 1644,
                "up-freq-finish": 1,
                "power-realtime": 1587,
            }
        },
    }
    DummyMiner.set_power_limit_calls = []
    DummyMiner.set_power_percent_calls = []
    state = main.FixedSupplyTempRuntimeState()
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1600,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 42.0,
                    "tolerance_c": 1.0,
                    "correction": 0.0,
                },
            },
        },
        {
            "ts": int(main.datetime.now(main.timezone.utc).timestamp() * 1000),
            "supply_temp": 40.6,
        },
        runtime_state=state,
    )

    assert result == {"power_percent": 100}
    assert DummyMiner.set_power_percent_calls == [100]
    assert DummyMiner.set_power_limit_calls == []
    assert state.startup_recalibration_decided is True
    assert state.startup_recalibration_needed is True
    assert state.startup_full_power_requested is True
    assert state.calibration_complete is False
    assert state.baseline_power_w is None


def test_fixed_supply_temp_mode_waits_for_existing_ramp_before_startup_full_power_request(monkeypatch):
    monkeypatch.setattr(main, "_APP_STARTED_AT_UNIX", 2_000, raising=False)
    DummyMiner.fetch_status_response = {
        "code": 0,
        "when": 10_000,
        "msg": {
            "summary": {
                "bootup-time": 9_000,
                "power-limit": 1644,
                "up-freq-finish": 0,
                "power-realtime": 1587,
            }
        },
    }
    DummyMiner.set_power_limit_calls = []
    DummyMiner.set_power_percent_calls = []
    state = main.FixedSupplyTempRuntimeState()
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1600,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 42.0,
                    "tolerance_c": 1.0,
                    "correction": 0.0,
                },
            },
        },
        {
            "ts": int(main.datetime.now(main.timezone.utc).timestamp() * 1000),
            "supply_temp": 40.6,
        },
        runtime_state=state,
    )

    assert result is None
    assert DummyMiner.set_power_percent_calls == []
    assert DummyMiner.set_power_limit_calls == []
    assert state.startup_recalibration_decided is True
    assert state.startup_recalibration_needed is True
    assert state.startup_full_power_requested is False
    assert state.calibration_complete is False
    assert state.baseline_power_w is None


def test_fixed_supply_temp_mode_captures_startup_baseline_after_forcing_full_power(monkeypatch):
    monkeypatch.setattr(main, "_APP_STARTED_AT_UNIX", 2_000, raising=False)
    DummyMiner.fetch_status_response = {
        "code": 0,
        "when": 10_030,
        "msg": {
            "summary": {
                "bootup-time": 9_030,
                "power-limit": 3600,
                "up-freq-finish": 1,
                "power-realtime": 3110,
            }
        },
    }
    DummyMiner.set_power_percent_calls = []
    state = main.FixedSupplyTempRuntimeState(
        signature=("miner01", "miner.local", 4433, 3800, 1600),
        startup_recalibration_decided=True,
        startup_recalibration_needed=True,
        startup_full_power_requested=True,
        calibration_requested=True,
        last_power_percent=100,
    )
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1600,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 40.0,
                    "tolerance_c": 1.0,
                    "correction": 0.0,
                },
            },
        },
        {
            "ts": int(main.datetime.now(main.timezone.utc).timestamp() * 1000),
            "supply_temp": 45.0,
        },
        runtime_state=state,
    )

    assert result == {"power_percent": 52}
    assert state.calibration_complete is True
    assert state.baseline_power_w == 3110
    assert state.last_power_percent == 52
    assert DummyMiner.set_power_percent_calls == [52]


def test_fixed_supply_temp_mode_captures_baseline_and_updates_power_percent():
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {
            "summary": {
                "power-limit": 3800,
                "up-freq-finish": 1,
                "power": 3000,
            }
        },
    }
    DummyMiner.start_calls = 0
    DummyMiner.set_power_percent_calls = []
    state = main.FixedSupplyTempRuntimeState(
        signature=("miner01", "miner.local", 4433, 3800, 1000),
    )
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1000,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 40.0,
                    "tolerance_c": 1.0,
                    "correction": -2.0,
                },
            },
        },
        {
            "ts": int(main.datetime.now(main.timezone.utc).timestamp() * 1000),
            "supply_temp": 45.0,
        },
        runtime_state=state,
    )

    assert result == {"power_percent": 70}
    assert state.calibration_complete is True
    assert state.baseline_power_w == 3000
    assert state.last_power_percent == 70
    assert DummyMiner.set_power_percent_calls == [70]


def test_fixed_supply_temp_mode_uses_power_realtime_for_baseline_when_power_is_missing():
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {
            "summary": {
                "power-limit": 3800,
                "up-freq-finish": 1,
                "power-realtime": 3120,
                "power-5min": 3116.1,
            }
        },
    }
    DummyMiner.start_calls = 0
    DummyMiner.set_power_percent_calls = []
    state = main.FixedSupplyTempRuntimeState(
        signature=("miner01", "miner.local", 4433, 3800, 1000),
    )
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1000,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 40.0,
                    "tolerance_c": 1.0,
                    "correction": -2.0,
                },
            },
        },
        {
            "ts": int(main.datetime.now(main.timezone.utc).timestamp() * 1000),
            "supply_temp": 45.0,
        },
        runtime_state=state,
    )

    assert result == {"power_percent": 70}
    assert state.calibration_complete is True
    assert state.baseline_power_w == 3120
    assert state.last_power_percent == 70
    assert DummyMiner.set_power_percent_calls == [70]


def test_fixed_supply_temp_mode_proceeds_after_calibration_request_even_if_reported_limit_stays_lower():
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {
            "summary": {
                "power-limit": 3600,
                "up-freq-finish": 1,
                "power": 3110,
            }
        },
    }
    DummyMiner.start_calls = 0
    DummyMiner.set_power_limit_calls = []
    DummyMiner.set_power_percent_calls = []
    state = main.FixedSupplyTempRuntimeState(
        signature=("miner01", "miner.local", 4433, 3800, 1600),
        calibration_requested=True,
    )
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1600,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 40.0,
                    "tolerance_c": 1.0,
                    "correction": 0.0,
                },
            },
        },
        {
            "ts": int(main.datetime.now(main.timezone.utc).timestamp() * 1000),
            "supply_temp": 45.0,
        },
        runtime_state=state,
    )

    assert result == {"power_percent": 52}
    assert DummyMiner.set_power_limit_calls == []
    assert DummyMiner.set_power_percent_calls == [52]
    assert state.calibration_complete is True
    assert state.baseline_power_w == 3110
    assert state.last_power_percent == 52


def test_fixed_supply_temp_mode_retries_when_reported_power_is_still_near_baseline():
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {
            "summary": {
                "power-limit": 3600,
                "up-freq-finish": 1,
                "power-realtime": 3110,
            }
        },
    }
    DummyMiner.start_calls = 0
    DummyMiner.set_power_percent_calls = []
    state = main.FixedSupplyTempRuntimeState(
        signature=("miner01", "miner.local", 4433, 3800, 1600),
        calibration_requested=True,
        calibration_complete=True,
        baseline_power_w=3115,
        last_power_percent=52,
    )
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1600,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 40.0,
                    "tolerance_c": 2.0,
                    "correction": 0.0,
                },
            },
        },
        {
            "ts": int(main.datetime.now(main.timezone.utc).timestamp() * 1000),
            "supply_temp": 45.7,
            "supply_temp_source": "zont:12000:supply",
        },
        runtime_state=state,
    )

    assert result == {"power_percent": 52}
    assert DummyMiner.set_power_percent_calls == [52]
    assert state.last_power_percent == 52


def test_fixed_supply_temp_mode_does_not_increase_power_while_temp_is_above_target():
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {
            "summary": {
                "power-limit": 1930,
                "up-freq-finish": 1,
                "power-realtime": 1870,
            }
        },
    }
    DummyMiner.start_calls = 0
    DummyMiner.set_power_percent_calls = []
    state = main.FixedSupplyTempRuntimeState(
        signature=("miner01", "miner.local", 4433, 3800, 1600),
        calibration_requested=True,
        calibration_complete=True,
        baseline_power_w=3111,
        last_power_percent=61,
    )
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1600,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 40.0,
                    "tolerance_c": 2.0,
                    "correction": 0.0,
                },
            },
        },
        {
            "ts": int(main.datetime.now(main.timezone.utc).timestamp() * 1000),
            "supply_temp": 44.5,
            "supply_temp_source": "zont:12000:supply",
        },
        runtime_state=state,
    )

    assert result == {"power_percent": 52}
    assert DummyMiner.set_power_percent_calls == [52]
    assert state.last_power_percent == 52


def test_fixed_supply_temp_mode_keeps_when_reported_power_matches_desired_percent():
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {
            "summary": {
                "power-limit": 3600,
                "up-freq-finish": 1,
                "power-realtime": 1620,
            }
        },
    }
    DummyMiner.start_calls = 0
    DummyMiner.set_power_percent_calls = []
    state = main.FixedSupplyTempRuntimeState(
        signature=("miner01", "miner.local", 4433, 3800, 1600),
        calibration_requested=True,
        calibration_complete=True,
        baseline_power_w=3115,
        last_power_percent=52,
    )
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1600,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 40.0,
                    "tolerance_c": 2.0,
                    "correction": 0.0,
                },
            },
        },
        {
            "ts": int(main.datetime.now(main.timezone.utc).timestamp() * 1000),
            "supply_temp": 45.7,
            "supply_temp_source": "zont:12000:supply",
        },
        runtime_state=state,
    )

    assert result is None
    assert DummyMiner.set_power_percent_calls == []
    assert state.last_power_percent == 52


def test_fixed_supply_temp_mode_skips_when_supply_temp_is_missing():
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {
            "summary": {
                "power-limit": 3800,
                "up-freq-finish": 1,
                "power": 2800,
            }
        },
    }
    DummyMiner.start_calls = 0
    DummyMiner.set_power_percent_calls = []
    state = main.FixedSupplyTempRuntimeState(
        signature=("miner01", "miner.local", 4433, 3800, 1000),
        calibration_complete=True,
        baseline_power_w=3000,
        last_power_percent=80,
    )
    miner = DummyMiner()

    result = main._apply_fixed_supply_temp_heating_mode(
        miner,
        {
            "devices": {
                "whatsminer": [
                    {
                        "device_id": "miner01",
                        "host": "miner.local",
                        "max_power": 3800,
                        "min_power": 1000,
                    }
                ]
            },
            "control_inputs": {"max_age_seconds": 180},
            "heating_mode": {
                "enabled": True,
                "type": "fixed_supply_temp",
                "params": {
                    "target_supply_temp_c": 40.0,
                    "tolerance_c": 1.0,
                    "correction": 0.0,
                },
            },
        },
        {
            "ts": int(main.datetime.now(main.timezone.utc).timestamp() * 1000),
            "supply_temp": None,
        },
        runtime_state=state,
    )

    assert result is None
    assert DummyMiner.set_power_percent_calls == []


def test_run_heating_mode_control_uses_first_whatsminer_device_config(monkeypatch):
    settings = {
        "devices": {
            "whatsminer": [
                {
                    "host": "miner.local",
                    "port": 4444,
                    "login": "user",
                    "password": "secret",
                    "timeout": 12,
                    "max_power": 3800,
                }
            ]
        },
        "heating_mode": {
            "enabled": True,
            "type": "fixed_power",
            "params": {"power_w": 3200},
        },
    }
    DummyMiner.fetch_status_response = {
        "code": 0,
        "msg": {"summary": {"power-limit": 3600, "up-freq-finish": 1}},
    }
    DummyMiner.set_power_limit_calls = []
    DummyMiner.init_kwargs = []

    monkeypatch.setattr(main, "Whatsminer", DummyMiner, raising=False)
    monkeypatch.setattr(main, "load_settings_yaml", lambda: "unused", raising=False)
    monkeypatch.setattr(main, "parse_settings_yaml", lambda raw_yaml: settings, raising=False)

    main._run_heating_mode_control()

    assert DummyMiner.init_kwargs[-1] == {
        "host": "miner.local",
        "port": 4444,
        "login": "user",
        "password": "secret",
        "timeout": 12,
        "max_power": 3800,
    }
    assert DummyMiner.set_power_limit_calls == [3200]


def test_heating_curve_api_reads_and_writes_section(tmp_path, monkeypatch):
    parsed_settings = {"devices": {}}
    routes = build_routes(tmp_path, monkeypatch, parsed_settings=parsed_settings)

    get_payload = routes["/api/heating-curve"]()
    assert get_payload["data"]["slope"] == 6.0
    assert get_payload["data"]["exponent"] == 0.4
    assert get_payload["data"]["force_max_power_below_target"] is True

    update_payload = routes["POST /api/heating-curve"](
        {
            "slope": 1.7,
            "exponent": 1.4,
            "force_max_power_below_target": False,
            "force_max_power_margin_c": 3.5,
            "min_supply_temp_c": 28.0,
            "max_supply_temp_c": 58.0,
        }
    )

    assert update_payload["data"] == {
        "slope": 1.7,
        "exponent": 1.4,
        "force_max_power_below_target": False,
        "force_max_power_margin_c": 3.5,
        "min_supply_temp_c": 28.0,
        "max_supply_temp_c": 58.0,
    }
