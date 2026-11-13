import sqlite3
from contextlib import closing

import pytest

from proof_of_heat.services import device_polling
from proof_of_heat.services.device_polling import DevicePoller
from proof_of_heat.services.metric_catalog import prune_catalog
from proof_of_heat.services.metrics import MetricSample


def legacy_db(tmp_path):
    path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE metrics (id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL, device_type TEXT NOT NULL, device_id TEXT NOT NULL,
            metric TEXT NOT NULL, value REAL NOT NULL)""")
        conn.executemany("INSERT INTO metrics(ts,device_type,device_id,metric,value) VALUES (1,'old','1',?,1)",
                         [(f"m{i}",) for i in range(5)])
    return path


def test_backfill_resumes_and_live_inserts_are_transactional(monkeypatch, tmp_path):
    path = legacy_db(tmp_path)
    poller = DevicePoller({}, data_dir=tmp_path)
    with sqlite3.connect(path) as conn:
        poller._ensure_schema(conn)
    assert poller.get_metric_catalog_status() == {"catalog": {}, "ready": False}
    monkeypatch.setattr(device_polling, "RETENTION_BATCH_SIZE", 2)

    def stop(timeout):
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT last_id FROM metric_catalog_progress WHERE source='metrics'").fetchone()[0] == 2
        assert poller._db_lock.acquire(blocking=False)
        poller._db_lock.release()
        poller._maintenance_stop.set()
        return True

    monkeypatch.setattr(poller._maintenance_stop, "wait", stop)
    poller._maintain_metric_catalog()
    assert poller.get_metric_catalog_status()["ready"] is False
    restarted = DevicePoller({}, data_dir=tmp_path)
    restarted._write_metrics(2, "new", "2", [MetricSample(name="live", value=1)])
    restarted._maintain_metric_catalog()
    assert restarted.get_metric_catalog_status() == {
        "catalog": {"old": {"1": [f"m{i}" for i in range(5)]}, "new": {"2": ["live"]}}, "ready": True,
    }
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO metrics(ts,device_type,device_id,metric,value) VALUES (3,'bad','1','rollback',1)")
        conn.rollback()
    assert "bad" not in restarted.get_metric_catalog()
    # A new process reads the persistent small catalog without a history scan.
    assert DevicePoller({}, data_dir=tmp_path).get_metric_catalog() == restarted.get_metric_catalog()


def test_catalog_prunes_only_after_last_point_across_all_resolutions(tmp_path):
    path = tmp_path / "telemetry.sqlite3"
    poller = DevicePoller({}, data_dir=tmp_path)
    with sqlite3.connect(path) as conn:
        poller._ensure_tables(conn)
        for resolution in (600, 3600):
            conn.execute("INSERT INTO metric_rollups(ts,resolution_seconds,device_type,device_id,metric,value) VALUES (0,?,'old','1','temp',1)", (resolution,))
        conn.execute("INSERT INTO metrics(ts,device_type,device_id,metric,value) VALUES (1,'old','1','temp',2)")
    key = ("old", "1", "temp")
    assert poller.get_metric_catalog() == {"old": {"1": ["temp"]}}
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM metrics")
        assert prune_catalog(conn, [key]) == 0
        conn.execute("DELETE FROM metric_rollups WHERE resolution_seconds=600")
        assert prune_catalog(conn, [key]) == 0
        conn.execute("DELETE FROM metric_rollups")
        assert prune_catalog(conn, [key]) == 1
        # Live insert in the same transaction restores membership atomically.
        conn.execute("INSERT INTO metrics(ts,device_type,device_id,metric,value) VALUES (2,'old','1','temp',3)")
    assert poller.get_metric_catalog() == {"old": {"1": ["temp"]}}
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM metrics")
    poller._maintain_metric_catalog()
    assert poller.get_metric_catalog() == {}


def test_ready_catalog_does_not_wait_for_database_lock(tmp_path):
    path = tmp_path / "telemetry.sqlite3"
    poller = DevicePoller({}, data_dir=tmp_path)
    poller._write_metrics(1, "test", "1", [MetricSample(name="temp", value=1)])
    expected = poller.get_metric_catalog_status()
    assert expected["ready"]
    with poller._db_lock, closing(sqlite3.connect(path)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        assert poller.get_metric_catalog_status() == expected
        writer.rollback()
    # Cache snapshots belong to callers and cannot corrupt the server cache.
    expected["catalog"].clear()
    assert poller.get_metric_catalog()


def test_discovery_never_reads_history_after_backfill(monkeypatch, tmp_path):
    path = legacy_db(tmp_path)
    poller = DevicePoller({}, data_dir=tmp_path)
    poller._maintain_metric_catalog()
    original = sqlite3.connect
    read_tables = set()

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)

        def authorize(action, table, column, db, trigger):
            if action == sqlite3.SQLITE_READ:
                read_tables.add(table)
                if table in {"metrics", "metric_rollups"}:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert len(poller.get_metric_catalog()["old"]["1"]) == 5
    poller.get_economics_metadata()
    assert not {"metrics", "metric_rollups"} & read_tables


def test_backfill_discovers_historical_rollup_resolution(tmp_path):
    path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE metric_rollups (id INTEGER PRIMARY KEY, ts INTEGER,
            resolution_seconds INTEGER, device_type TEXT, device_id TEXT, metric TEXT,
            value REAL, unit TEXT)""")
        conn.execute("INSERT INTO metric_rollups VALUES(1,0,7200,'old','1','temp',1,NULL)")
    poller = DevicePoller({}, data_dir=tmp_path)
    poller._maintain_metric_catalog()
    assert poller.get_metric_catalog_status() == {"catalog": {"old": {"1": ["temp"]}}, "ready": True}
    with sqlite3.connect(path) as conn:
        assert prune_catalog(conn, [("old", "1", "temp")]) == 0
        assert conn.execute("SELECT resolution_seconds FROM metric_catalog_resolutions").fetchall() == [(7200,)]


def test_atomic_prune_rollback_preserves_membership(tmp_path):
    poller = DevicePoller({}, data_dir=tmp_path)
    poller._write_metrics(1, "test", "1", [MetricSample(name="temp", value=1)])
    path = tmp_path / "telemetry.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DELETE FROM metrics")
        assert prune_catalog(conn, [("test", "1", "temp")]) == 1
        conn.rollback()
    assert poller.get_metric_catalog() == {"test": {"1": ["temp"]}}
