import json
import sqlite3

from proof_of_heat.services import device_polling
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
    assert legacy_row == (1, "legacy", "device-1", "temp", 10.0)
    assert new_row == ("temperature", 1.5, "celsius")
