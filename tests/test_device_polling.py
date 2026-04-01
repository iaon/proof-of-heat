import json
import sqlite3
import time
from datetime import datetime, timezone

from proof_of_heat.services import device_polling
from proof_of_heat.services import economic_polling
from proof_of_heat.services.device_polling import DevicePoller


def test_open_meteo_virtual_device_metrics_are_persisted(monkeypatch, tmp_path):
    settings = {
        "location": {
            "name": "Moscow",
            "latitude": 55.7558,
            "longitude": 37.6173,
            "timezone": "Europe/Moscow",
        },
        "devices": {
            "open_meteo": [
                {
                    "device_id": 1001,
                    "type": "virtual",
                }
            ]
        },
    }

    monkeypatch.setattr(
        device_polling,
        "fetch_open_meteo_weather",
        lambda **kwargs: {
            "provider": "open_meteo",
            "timestamp": "2026-03-29T10:15:00+00:00",
            "current": {
                "temperature": 1.5,
                "windspeed": 3.0,
                "weathercode": 2,
                "time": "2026-03-29T13:15",
            },
            "units": {
                "temperature": "celsius",
                "windspeed": "km/h",
                "weathercode": "wmo code",
            },
            "source": kwargs,
        },
    )

    poller = DevicePoller(settings, data_dir=tmp_path)
    payload = poller.poll_open_meteo_device(settings["devices"]["open_meteo"][0])

    assert payload["device_id"] == "1001"
    assert payload["type"] == "virtual"
    assert payload["location"]["name"] == "Moscow"

    assert poller.list_metric_device_types() == ["open_meteo"]
    assert poller.list_metric_device_ids("open_meteo") == ["1001"]
    assert set(poller.list_metric_names("open_meteo", "1001")) >= {
        "temperature",
        "weathercode",
        "windspeed",
    }
    assert poller.get_metric_catalog()["open_meteo"]["1001"] == [
        "temperature",
        "weathercode",
        "windspeed",
    ]

    points = poller.get_metric_series("open_meteo", "1001", "temperature", None, None)
    assert len(points) == 1
    assert points[0]["value"] == 1.5

    db_path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(db_path) as conn:
        raw_event = conn.execute(
            "SELECT payload FROM raw_events WHERE device_type = ? AND device_id = ?",
            ("open_meteo", "1001"),
        ).fetchone()
        metric_unit = conn.execute(
            "SELECT unit FROM metrics WHERE device_type = ? AND device_id = ? AND metric = ?",
            ("open_meteo", "1001", "temperature"),
        ).fetchone()

    assert raw_event is not None
    assert json.loads(raw_event[0])["type"] == "virtual"
    assert metric_unit == ("celsius",)


def test_metrics_table_is_migrated_in_place_for_older_sqlite_files(monkeypatch, tmp_path):
    settings = {
        "location": {
            "name": "Moscow",
            "latitude": 55.7558,
            "longitude": 37.6173,
            "timezone": "Europe/Moscow",
        },
        "devices": {
            "open_meteo": [
                {
                    "device_id": 1001,
                    "type": "virtual",
                }
            ]
        },
    }
    db_path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                device_type TEXT NOT NULL,
                device_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO metrics (ts, device_type, device_id, metric, value)
            VALUES (1, 'legacy', 'device-1', 'temp', 10.0)
            """
        )

    monkeypatch.setattr(
        device_polling,
        "fetch_open_meteo_weather",
        lambda **kwargs: {
            "provider": "open_meteo",
            "timestamp": "2026-03-29T10:15:00+00:00",
            "current": {"temperature": 1.5},
            "units": {"temperature": "celsius"},
            "source": kwargs,
        },
    )

    poller = DevicePoller(settings, data_dir=tmp_path)
    poller.poll_open_meteo_device(settings["devices"]["open_meteo"][0])

    with sqlite3.connect(db_path) as conn:
        metric_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(metrics)").fetchall()
        }
        metric_indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(metrics)").fetchall()
        }
        legacy_row = conn.execute(
            """
            SELECT ts, device_type, device_id, metric, value
            FROM metrics
            WHERE device_type = 'legacy'
            """
        ).fetchone()
        new_row = conn.execute(
            """
            SELECT metric, value, unit
            FROM metrics
            WHERE device_type = 'open_meteo' AND device_id = '1001'
            """
        ).fetchone()

    assert {"unit", "labels", "component"} <= metric_columns
    assert "idx_metrics_type_device_id" in metric_indexes
    assert "idx_metrics_type_device_metric_ts" in metric_indexes
    assert legacy_row == (1, "legacy", "device-1", "temp", 10.0)
    assert new_row == ("temperature", 1.5, "celsius")


def test_open_meteo_new_current_format_is_persisted(monkeypatch, tmp_path):
    settings = {
        "location": {
            "name": "Moscow",
            "latitude": 55.7558,
            "longitude": 37.6173,
            "timezone": "Europe/Moscow",
        },
        "devices": {
            "open_meteo": [
                {
                    "device_id": 1001,
                    "type": "virtual",
                }
            ]
        },
    }

    monkeypatch.setattr(
        device_polling,
        "fetch_open_meteo_weather",
        lambda **kwargs: {
            "provider": "open_meteo",
            "timestamp": "2026-03-29T10:15:00+00:00",
            "current": {
                "time": "2026-03-29T10:15",
                "temperature_2m": 6.4,
                "relative_humidity_2m": 72,
                "is_day": 1,
            },
            "units": {
                "temperature_2m": "celsius",
                "relative_humidity_2m": "%",
                "is_day": "",
            },
            "source": kwargs,
        },
    )

    poller = DevicePoller(settings, data_dir=tmp_path)
    poller.poll_open_meteo_device(settings["devices"]["open_meteo"][0])

    metric_names = set(poller.list_metric_names("open_meteo", "1001"))
    assert {"temperature_2m", "relative_humidity_2m", "is_day"} <= metric_names

    points = poller.get_metric_series("open_meteo", "1001", "temperature_2m", None, None)
    assert len(points) == 1
    assert points[0]["value"] == 6.4


def test_met_no_metrics_use_poll_time_instead_of_provider_hour_bucket(monkeypatch, tmp_path):
    settings = {
        "location": {
            "name": "Moscow",
            "latitude": 55.7558,
            "longitude": 37.6173,
            "timezone": "Europe/Moscow",
        },
        "devices": {
            "met_no": [
                {
                    "device_id": 1002,
                    "type": "virtual",
                }
            ]
        },
    }

    monkeypatch.setattr(
        device_polling,
        "fetch_met_no_weather",
        lambda **kwargs: {
            "provider": "met_no",
            # met.no can return coarse provider timestamps (for example on-the-hour)
            "timestamp": "2026-03-29T10:00:00+00:00",
            "current": {"air_temperature": 2.3},
            "units": {"air_temperature": "celsius"},
            "source": kwargs,
        },
    )

    poller = DevicePoller(settings, data_dir=tmp_path)
    device_cfg = settings["devices"]["met_no"][0]
    poller.poll_met_no_device(device_cfg)
    time.sleep(0.02)
    poller.poll_met_no_device(device_cfg)

    db_path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(db_path) as conn:
        distinct_ts = conn.execute(
            """
            SELECT COUNT(DISTINCT ts)
            FROM metrics
            WHERE device_type = 'met_no'
              AND device_id = '1002'
              AND metric = 'air_temperature'
            """
        ).fetchone()
        provider_ts = conn.execute(
            """
            SELECT json_extract(payload, '$.provider_ts_ms')
            FROM raw_events
            WHERE device_type = 'met_no' AND device_id = '1002'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert distinct_ts == (2,)
    assert provider_ts is not None
    assert int(provider_ts[0]) == poller._to_epoch_ms("2026-03-29T10:00:00+00:00")


def test_whatsminer_collects_summary_pools_and_device_info(monkeypatch, tmp_path):
    settings = {
        "devices": {
            "whatsminer": [
                {
                    "device_id": "miner01",
                    "login": "login",
                    "password": "pass",
                    "host": "example.com",
                    "port": 4028,
                }
            ]
        }
    }

    calls = []

    def fake_call_whatsminer(**kwargs):
        calls.append((kwargs["cmd"], kwargs.get("param")))
        if kwargs["cmd"] == "get.miner.status" and kwargs.get("param") == "summary":
            return {
                "when": "2026-03-29T10:15:00+00:00",
                "msg": {
                    "summary": {
                        "power": 1000,
                        "board-temperature": [55.0],
                    }
                },
            }
        if kwargs["cmd"] == "get.miner.status" and kwargs.get("param") == "pools":
            return {
                "when": "2026-03-29T10:15:00+00:00",
                "msg": {
                    "pools": [
                        {
                            "id": 1,
                            "url": "stratum+tcp://pool.example.com:3333",
                            "user": "worker1",
                            "reject-rate": 0.7,
                            "last-share-time": 214748364.7,
                        }
                    ]
                },
            }
        if kwargs["cmd"] == "get.device.info":
            return {
                "when": "2026-03-29T10:15:00+00:00",
                "msg": {
                    "system": {
                        "fwversion": "test-fw",
                    },
                    "power": {
                        "model": "P221B",
                        "iin": 7.96,
                        "vin": 234,
                        "vout": 1135,
                        "pin": 1869,
                        "fanspeed": 4992,
                        "temp0": 50,
                    },
                },
            }
        raise AssertionError(f"Unexpected Whatsminer call: {kwargs}")

    monkeypatch.setattr(device_polling.DevicePoller, "_ping_host", lambda *args, **kwargs: True)
    monkeypatch.setattr(device_polling, "call_whatsminer", fake_call_whatsminer)

    poller = DevicePoller(settings, data_dir=tmp_path)
    payload = poller.poll_whatsminer_device(settings["devices"]["whatsminer"][0])

    assert calls == [
        ("get.miner.status", "summary"),
        ("get.miner.status", "pools"),
        ("get.device.info", None),
    ]
    assert payload["summary"]["msg"]["summary"]["power"] == 1000
    assert payload["pools"]["msg"]["pools"][0]["user"] == "worker1"
    assert payload["device_info"]["msg"]["power"]["model"] == "P221B"

    db_path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(db_path) as conn:
        raw_event = conn.execute(
            """
            SELECT payload
            FROM raw_events
            WHERE device_type = 'whatsminer' AND device_id = 'miner01'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert raw_event is not None
    raw_payload = json.loads(raw_event[0])
    assert "summary" in raw_payload
    assert "pools" in raw_payload
    assert "device_info" in raw_payload
    assert raw_payload["device_info"]["msg"]["system"]["fwversion"] == "test-fw"

    metric_names = set(poller.list_metric_names("whatsminer", "miner01"))
    assert {
        "power",
        "board_temperature_0",
        "pool_1_reject_rate",
        "pool_1_last_share_time",
        "psu_iin",
        "psu_vin",
        "psu_vout",
        "psu_pin",
        "psu_fanspeed",
        "psu_temp0",
    } <= metric_names


def test_zont_device_selected_by_serial_and_metrics_persisted(monkeypatch, tmp_path):
    settings = {
        "integrations": {
            "zont_api": [
                {
                    "id": 1,
                    "headers": {"X-ZONT-Client": "test@example.com"},
                    "login": "login",
                    "password": "password",
                }
            ]
        },
        "devices": {
            "zont": [
                {
                    "integration_id": 1,
                    "device_id": 12000,
                    "serial": "SN-NEEDED",
                }
            ]
        },
    }

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "devices": [
                    {
                        "serial": "SN-OTHER",
                        "temp_out": 1.0,
                        "io": [{"portname": "t_room", "value": 18.5}],
                    },
                    {
                        "serial": "SN-NEEDED",
                        "temp_out": 3.2,
                        "io": {
                            "thermometers-state": {
                                "600ff17fdcc0c856f06a7c3d": {
                                    "last_state": "ok",
                                    "last_value": 22.9,
                                    "last_value_time": 1774739307,
                                },
                                "64adb1ba3939e3473a8ab9a3": {
                                    "last_state": "ok",
                                    "last_value": -1.2,
                                    "last_value_time": 1774739307,
                                },
                            },
                            "last-boiler-state": {
                                "target_temp": 5,
                                "power": True,
                            },
                        },
                    },
                ],
            }

    class _FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers, json, auth):
            assert url == "https://my.zont.online/api/devices"
            assert headers["X-ZONT-Client"] == "test@example.com"
            assert json["load_io"] is True
            assert auth == ("login", "password")
            return _FakeResponse()

    monkeypatch.setattr(device_polling.httpx, "Client", _FakeClient)

    poller = DevicePoller(settings, data_dir=tmp_path)
    payload = poller.poll_zont_device(settings["devices"]["zont"][0])

    assert payload["provider"] == "zont"
    assert payload["serial"] == "SN-NEEDED"
    assert payload["device_id"] == "12000"

    metric_names = set(poller.list_metric_names("zont", "12000"))
    assert {
        "temp_out",
        "io_thermometers_state_600ff17fdcc0c856f06a7c3d_last_value",
        "io_thermometers_state_64adb1ba3939e3473a8ab9a3_last_value",
        "io_last_boiler_state_target_temp",
        "io_last_boiler_state_power",
    } <= metric_names

    points = poller.get_metric_series(
        "zont",
        "12000",
        "io_thermometers_state_600ff17fdcc0c856f06a7c3d_last_value",
        None,
        None,
    )
    assert len(points) == 1
    assert points[0]["value"] == 22.9


def test_control_inputs_are_resolved_and_persisted(monkeypatch, tmp_path):
    settings = {
        "location": {
            "name": "Moscow",
            "latitude": 55.7558,
            "longitude": 37.6173,
            "timezone": "Europe/Moscow",
        },
        "devices": {
            "open_meteo": [{"device_id": 1001, "type": "virtual"}],
            "met_no": [{"device_id": 1002, "type": "virtual"}],
            "whatsminer": [
                {
                    "device_id": "miner01",
                    "login": "login",
                    "password": "pass",
                    "host": "example.com",
                    "port": 4028,
                }
            ],
        },
        "control_inputs": {
            "max_age_seconds": 180,
            "indoor_temp": {
                "select": "highest_priority_available",
                "sources": [
                    {
                        "device_type": "open_meteo",
                        "device_id": "1001",
                        "metric": "temperature_2m",
                        "correction": -0.5,
                    },
                    {
                        "device_type": "met_no",
                        "device_id": "1002",
                        "metric": "air_temperature",
                    },
                ],
            },
            "outdoor_temp": {
                "select": "highest_priority_available",
                "sources": [
                    {
                        "device_type": "met_no",
                        "device_id": "1002",
                        "metric": "air_temperature",
                        "correction": 0.2,
                    }
                ],
            },
            "supply_temp": {
                "select": "highest_priority_available",
                "sources": [
                    {
                        "device_type": "whatsminer",
                        "device_id": "miner01",
                        "metric": "board_temperature_0",
                    }
                ],
            },
            "power": {
                "select": "sum_all_available",
                "default": 0,
                "sources": [
                    {
                        "device_type": "whatsminer",
                        "device_id": "miner01",
                        "metric": "power",
                    },
                    {
                        "device_type": "met_no",
                        "device_id": "1002",
                        "metric": "air_temperature",
                        "correction": 1.0,
                    },
                ],
            },
        },
    }

    monkeypatch.setattr(device_polling.DevicePoller, "_ping_host", lambda *args, **kwargs: True)
    def fake_call_whatsminer(**kwargs):
        if kwargs["cmd"] == "get.miner.status" and kwargs.get("param") == "summary":
            return {
                "when": "2026-03-29T10:15:00+00:00",
                "msg": {
                    "summary": {
                        "power": 1000,
                        "board-temperature": [55.0],
                    }
                },
            }
        if kwargs["cmd"] == "get.miner.status" and kwargs.get("param") == "pools":
            return {
                "when": "2026-03-29T10:15:00+00:00",
                "msg": {"pools": []},
            }
        if kwargs["cmd"] == "get.device.info":
            return {
                "when": "2026-03-29T10:15:00+00:00",
                "msg": {"model": "M50"},
            }
        raise AssertionError(f"Unexpected Whatsminer call: {kwargs}")

    monkeypatch.setattr(device_polling, "call_whatsminer", fake_call_whatsminer)
    monkeypatch.setattr(
        device_polling,
        "fetch_met_no_weather",
        lambda **kwargs: {
            "provider": "met_no",
            "timestamp": "2026-03-29T10:15:00+00:00",
            "current": {"air_temperature": 2.3},
            "units": {"air_temperature": "celsius"},
            "source": kwargs,
        },
    )
    monkeypatch.setattr(
        device_polling,
        "fetch_open_meteo_weather",
        lambda **kwargs: {
            "provider": "open_meteo",
            "timestamp": "2026-03-29T10:15:00+00:00",
            "current": {"temperature_2m": 6.4},
            "units": {"temperature_2m": "celsius"},
            "source": kwargs,
        },
    )

    poller = DevicePoller(settings, data_dir=tmp_path)
    poller.poll_met_no_device(settings["devices"]["met_no"][0])
    poller.poll_open_meteo_device(settings["devices"]["open_meteo"][0])
    poller.poll_whatsminer_device(settings["devices"]["whatsminer"][0])

    db_path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT indoor_temp, indoor_temp_source, outdoor_temp, outdoor_temp_source,
                   supply_temp, supply_temp_source, power, power_sources
            FROM control_inputs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row[0] == 5.9
    assert row[1] == "open_meteo:1001:temperature_2m"
    assert row[2] == 2.5
    assert row[3] == "met_no:1002:air_temperature"
    assert row[4] == 55.0
    assert row[5] == "whatsminer:miner01:board_temperature_0"
    assert row[6] == 1003.3
    assert json.loads(row[7]) == [
        "whatsminer:miner01:power",
        "met_no:1002:air_temperature",
    ]


def test_control_inputs_ignore_stale_metrics_and_default_power_to_zero(tmp_path):
    settings = {
        "control_inputs": {
            "max_age_seconds": 10,
            "indoor_temp": {
                "select": "highest_priority_available",
                "sources": [
                    {
                        "device_type": "open_meteo",
                        "device_id": "1001",
                        "metric": "temperature_2m",
                    }
                ],
            },
            "power": {
                "select": "sum_all_available",
                "default": 0,
                "sources": [
                    {
                        "device_type": "whatsminer",
                        "device_id": "miner01",
                        "metric": "power",
                    }
                ],
            },
        }
    }

    poller = DevicePoller(settings, data_dir=tmp_path)
    stale_ts = 1_000
    with sqlite3.connect(tmp_path / "telemetry.sqlite3") as conn:
        poller._ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO metrics (ts, device_type, device_id, metric, value, unit)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (stale_ts, "open_meteo", "1001", "temperature_2m", 9.0, "celsius"),
        )
        poller._refresh_control_inputs(conn=conn, ts_ms=stale_ts + 11_000)
        row = conn.execute(
            """
            SELECT indoor_temp, power, power_sources
            FROM control_inputs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row == (None, 0.0, "[]")


def test_zont_refresh_interval_180_is_valid(monkeypatch, tmp_path):
    settings = {
        "devices": {
            "refresh_interval": 30,
            "zont": [
                {
                    "integration_id": 1,
                    "device_id": 12000,
                    "serial": "SN-NEEDED",
                    "refresh_interval": 180,
                }
            ],
        }
    }

    poller = DevicePoller(settings, data_dir=tmp_path)
    poller.start()
    try:
        assert poller._scheduler is not None
        jobs = poller._scheduler.get_jobs()
        assert len(jobs) == 1
        assert "interval[0:03:00]" in str(jobs[0].trigger)
    finally:
        poller.shutdown()


def test_economics_metrics_are_computed_and_persisted(monkeypatch, tmp_path):
    current_iso = datetime.now(timezone.utc).isoformat()
    settings = {
        "devices": {
            "whatsminer": [
                {
                    "device_id": "miner01",
                    "login": "login",
                    "password": "pass",
                    "host": "example.com",
                    "port": 4028,
                }
            ]
        },
        "economics": {
            "enabled": True,
            "currencies": {
                "crypto": "BTC",
                "fiat": "EUR",
            },
            "exchange_rate": {
                "integrations": {
                    "crypto_usd": "mempool_space",
                    "usd_fiat": "cbr",
                },
                "refresh_interval": 3600,
                "stale_after": 7200,
            },
            "hashprice": {
                "integration": "mempool_space",
                "reward_stats_blocks": 144,
                "hashrate_window": "1m",
                "refresh_interval": 3600,
                "stale_after": 7200,
            },
            "electricity": {
                "price_per_kwh": 0.06,
            },
        },
    }

    monkeypatch.setattr(device_polling.DevicePoller, "_ping_host", lambda *args, **kwargs: True)

    def fake_call_whatsminer(**kwargs):
        if kwargs["cmd"] == "get.miner.status" and kwargs.get("param") == "summary":
            return {
                "when": current_iso,
                "msg": {
                    "summary": {
                        "power": 1000,
                        "power-rate": 20.0,
                        "board-temperature": [55.0],
                    }
                },
            }
        if kwargs["cmd"] == "get.miner.status" and kwargs.get("param") == "pools":
            return {
                "when": current_iso,
                "msg": {"pools": []},
            }
        if kwargs["cmd"] == "get.device.info":
            return {
                "when": current_iso,
                "msg": {"model": "M50"},
            }
        raise AssertionError(f"Unexpected Whatsminer call: {kwargs}")

    monkeypatch.setattr(device_polling, "call_whatsminer", fake_call_whatsminer)
    monkeypatch.setattr(
        economic_polling,
        "fetch_mempool_prices",
        lambda **kwargs: {
            "provider": "mempool_space",
            "timestamp": 1774739307,
            "prices": {"USD": 100000},
        },
    )
    monkeypatch.setattr(
        economic_polling,
        "fetch_cbr_daily_rates",
        lambda **kwargs: {
            "provider": "cbr",
            "timestamp": "01.04.2026",
            "base_currency": "RUB",
            "rates": {"RUB": 1.0, "USD": 90.5, "EUR": 100.0},
        },
    )
    monkeypatch.setattr(
        economic_polling,
        "fetch_mempool_reward_stats",
        lambda **kwargs: {
            "provider": "mempool_space",
            "block_count": 144,
            "payload": {
                "totalReward": "90000000000",
                "totalFee": "100000000",
                "totalTx": "1000",
            },
        },
    )
    monkeypatch.setattr(
        economic_polling,
        "fetch_mempool_hashrate",
        lambda **kwargs: {
            "provider": "mempool_space",
            "time_period": "1m",
            "payload": {"currentHashrate": 500_000_000_000_000_000_000},
        },
    )

    poller = DevicePoller(settings, data_dir=tmp_path)
    poller.poll_whatsminer_device(settings["devices"]["whatsminer"][0])
    payload = poller.poll_economics(settings["economics"])

    assert payload["derived"]["exchange_rate_btc_usd"] == 100000
    assert payload["derived"]["exchange_rate_usd_eur"] == 0.905
    assert payload["derived"]["exchange_rate_btc_eur"] == 90_500
    assert payload["derived"]["network_hashrate_th_s"] == 500_000_000
    assert payload["derived"]["avg_block_reward_btc"] == 6.25
    assert abs(payload["derived"]["hashprice_btc_th_day"] - 0.0000018) < 1e-12
    assert abs(payload["derived"]["hashprice_eur_th_day"] - 0.1629) < 1e-9
    assert payload["derived"]["electricity_price_eur_kwh"] == 0.06
    assert abs(payload["derived"]["hashcost_eur_th_day"] - 0.0288) < 1e-9
    assert abs(payload["derived"]["hashcost_btc_th_day"] - (0.0288 / 90_500)) < 1e-15
    assert payload["derived"]["power_rate_source"] == "whatsminer:miner01:power_rate"

    metric_names = set(poller.list_metric_names("economics", "market"))
    assert {
        "exchange_rate_btc_usd",
        "exchange_rate_usd_eur",
        "exchange_rate_btc_eur",
        "network_hashrate_th_s",
        "avg_block_reward_btc",
        "hashprice_btc_th_day",
        "hashprice_eur_th_day",
        "electricity_price_eur_kwh",
        "hashcost_eur_th_day",
        "hashcost_btc_th_day",
    } <= metric_names

    with sqlite3.connect(tmp_path / "telemetry.sqlite3") as conn:
        raw_event = conn.execute(
            """
            SELECT payload
            FROM raw_events
            WHERE device_type = 'economics' AND device_id = 'market'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert raw_event is not None
    raw_payload = json.loads(raw_event[0])
    assert raw_payload["exchange_rate"]["crypto_usd"]["prices"]["USD"] == 100000
    assert raw_payload["exchange_rate"]["usd_fiat"]["rates"]["EUR"] == 100.0
    assert raw_payload["hashprice"]["reward_stats"]["payload"]["totalReward"] == "90000000000"


def test_economics_job_is_scheduled_without_devices(monkeypatch, tmp_path):
    settings = {
        "economics": {
            "enabled": True,
            "exchange_rate": {
                "integrations": {
                    "crypto_usd": "mempool_space",
                    "usd_fiat": "cbr",
                },
                "refresh_interval": 3600,
            },
            "hashprice": {
                "integration": "mempool_space",
                "refresh_interval": 7200,
            },
            "electricity": {
                "price_per_kwh": 5.5,
            },
        }
    }

    monkeypatch.setattr(DevicePoller, "poll_economics", lambda self, device, request=None: {"ok": True})

    poller = DevicePoller(settings, data_dir=tmp_path)
    poller.start()
    try:
        assert poller._scheduler is not None
        jobs = poller._scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "economics-market"
        assert "interval[1:00:00]" in str(jobs[0].trigger)
    finally:
        poller.shutdown()


def test_economics_is_polled_immediately_on_start(monkeypatch, tmp_path):
    settings = {
        "economics": {
            "enabled": True,
            "exchange_rate": {
                "integrations": {
                    "crypto_usd": "mempool_space",
                    "usd_fiat": "cbr",
                },
                "refresh_interval": 3600,
            },
            "hashprice": {
                "integration": "mempool_space",
                "refresh_interval": 3600,
            },
            "electricity": {
                "price_per_kwh": 5.5,
            },
        }
    }

    calls = []

    def fake_poll_economics(self, device, request=None):
        calls.append(device.copy())
        return {"provider": "economics", "device_id": "market", "ok": True}

    monkeypatch.setattr(DevicePoller, "poll_economics", fake_poll_economics)

    poller = DevicePoller(settings, data_dir=tmp_path)
    poller.start()
    try:
        assert len(calls) == 1
        latest_payloads = poller.get_latest_payloads()
        assert "economics:market" in latest_payloads
        assert latest_payloads["economics:market"]["payload"]["ok"] is True
    finally:
        poller.shutdown()
