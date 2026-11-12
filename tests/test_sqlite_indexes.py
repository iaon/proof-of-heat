import sqlite3
from threading import Event

from proof_of_heat.services.device_polling import DevicePoller, OBSOLETE_TELEMETRY_INDEXES
from proof_of_heat.services.economic_polling import _discover_hashcost_sources, _resolve_power_rate_metrics

LEGACY_INDEXES = {
    "idx_metric_rollups_type_device_metric_ts": "metric_rollups(device_type, device_id, metric, ts)",
    "idx_metric_rollups_device_metric_ts": "metric_rollups(device_id, metric, ts)",
    "idx_metrics_device_metric_ts": "metrics(device_id, metric, ts)",
    "idx_metrics_type_device_id": "metrics(device_type, device_id)",
}


def indexes(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='index'")}


def legacy_database(tmp_path):
    poller = DevicePoller({}, data_dir=tmp_path)
    path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(path) as conn:
        poller._ensure_tables(conn)
        for name, definition in LEGACY_INDEXES.items():
            conn.execute(f"CREATE INDEX {name} ON {definition}")
        conn.executemany(
            "INSERT INTO metrics(ts,device_type,device_id,metric,value) VALUES (?,'whatsminer','1','power_rate',?)",
            [(1000, 20), (2000, 25)],
        )
        conn.execute("INSERT INTO metric_rollups(ts,resolution_seconds,device_type,device_id,metric,value) VALUES (0,600,'whatsminer','1','power_rate',18)")
    return poller, path


def test_index_migration_preserves_data_and_query_results(tmp_path):
    poller, path = legacy_database(tmp_path)
    poller._settings = {"database": {"retention": {"metrics": {
        "interval_seconds": 3600, "raw_retention_seconds": 604800,
        "rollups": [{"resolution_seconds": 600, "retention_seconds": 15552000, "sample": "last"}],
    }}}}
    before_series = poller.get_metric_series("whatsminer", "1", "power_rate", None, None)
    before_catalog = poller.get_metric_catalog()
    with sqlite3.connect(path) as conn:
        before = {table: conn.execute(f"SELECT * FROM {table}").fetchall()
                  for table in ("metrics", "metric_rollups")}
        sources = _discover_hashcost_sources(conn, {})
        rates = _resolve_power_rate_metrics(conn, {}, 5000, 2000)
    assert poller._remove_obsolete_indexes() == 4
    assert poller._remove_obsolete_indexes() == 0
    # Schema initialization after a restart must not recreate removed indexes.
    with sqlite3.connect(path) as conn:
        DevicePoller({})._ensure_tables(conn)
        assert not set(LEGACY_INDEXES) & indexes(conn)
        assert {"idx_metric_rollups_unique_bucket", "idx_metric_rollups_resolution_ts",
                "idx_metrics_type_device_metric_ts"} <= indexes(conn)
        assert before == {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in before}
        assert _discover_hashcost_sources(conn, {}) == sources
        assert _resolve_power_rate_metrics(conn, {}, 5000, 2000) == rates
    assert poller.get_metric_series("whatsminer", "1", "power_rate", None, None) == before_series
    poller._metric_catalog_cache = None
    assert poller.get_metric_catalog() == before_catalog


def test_remaining_indexes_support_chart_and_retention_queries(tmp_path):
    poller, path = legacy_database(tmp_path)
    poller._remove_obsolete_indexes()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE INDEX idx_metrics_ts ON metrics(ts)")
        queries = {
            "idx_metric_rollups_unique_bucket": "SELECT ts,value FROM metric_rollups WHERE resolution_seconds=600 AND device_type='whatsminer' AND device_id='1' AND metric='power_rate' AND ts BETWEEN 0 AND 2000 ORDER BY ts",
            "idx_metrics_type_device_metric_ts": "SELECT ts,value FROM metrics WHERE device_type='whatsminer' AND device_id='1' AND metric='power_rate' ORDER BY ts DESC,id DESC LIMIT 1",
            "idx_metric_rollups_resolution_ts": "SELECT id FROM metric_rollups WHERE resolution_seconds=600 AND ts<2000 ORDER BY ts,id LIMIT 2000",
            "idx_metrics_ts": "SELECT id FROM metrics WHERE ts<2000 ORDER BY ts,id LIMIT 2000",
        }
        for index, query in queries.items():
            plan = conn.execute("EXPLAIN QUERY PLAN " + query).fetchall()
            assert any("SEARCH" in row[3] and index in row[3] for row in plan), plan
            assert not any("TEMP B-TREE" in row[3] for row in plan), plan


def test_index_migration_resumes_after_shutdown(monkeypatch, tmp_path):
    poller, path = legacy_database(tmp_path)

    def stop_after_first(timeout):
        assert poller._db_lock.acquire(blocking=False)
        poller._db_lock.release()
        with sqlite3.connect(path) as conn:
            assert len(set(LEGACY_INDEXES) & indexes(conn)) == 3
        poller._maintenance_stop.set()
        return True

    monkeypatch.setattr(poller._maintenance_stop, "wait", stop_after_first)
    assert poller._remove_obsolete_indexes() == 1
    restarted = DevicePoller({}, data_dir=tmp_path)
    assert restarted._remove_obsolete_indexes() == 3


def test_index_migration_runs_after_resume_even_without_retention(monkeypatch, tmp_path):
    poller, path = legacy_database(tmp_path)
    completed = Event()
    original = poller._remove_obsolete_indexes

    def migrate():
        try:
            return original()
        finally:
            completed.set()

    monkeypatch.setattr(poller, "_remove_obsolete_indexes", migrate)
    try:
        poller.start(paused=True)
        job = poller._scheduler.get_job("maintenance-indexes")
        assert job.executor == "maintenance"
        assert job.misfire_grace_time is None
        assert job.max_instances == 1
        assert not completed.is_set()
        with sqlite3.connect(path) as conn:
            assert set(OBSOLETE_TELEMETRY_INDEXES) <= indexes(conn)
        poller.resume()
        assert completed.wait(5)
        with sqlite3.connect(path) as conn:
            assert not set(OBSOLETE_TELEMETRY_INDEXES) & indexes(conn)
    finally:
        poller.shutdown()
