import sqlite3
from threading import Event

import pytest

from proof_of_heat.services import device_polling
from proof_of_heat.services.device_polling import CONTROL_HISTORY_COLUMNS, DevicePoller

NOW = 2_000_000_000_000
RAW_CUTOFF = NOW - 604_800_000
BUCKET = (RAW_CUTOFF // 600_000 - 2) * 600_000


def settings(table, sample="last"):
    return {"database": {"retention": {table: {
        "enabled": True, "interval_seconds": 3600, "raw_retention_seconds": 604800,
        "rollups": [{"resolution_seconds": 600, "retention_seconds": 15552000,
                     "sample": sample}],
    }}}}


def insert(conn, table, ts, value):
    # Different NULLs and text ensure a snapshot cannot be assembled from independent metrics.
    if table == "control_inputs":
        payload = (value, f"sensor-{value}", None, None, 45.0, "supply", 1200.0, '["miner"]')
    else:
        payload = (f"mode-{value}", None, 45.0, value, 1200.0, f"reason-{value}")
    columns = ", ".join(CONTROL_HISTORY_COLUMNS[table])
    placeholders = ", ".join("?" for _ in range(len(payload) + 1))
    conn.execute(f"INSERT INTO {table} (ts, {columns}) VALUES ({placeholders})", (ts, *payload))
    return payload


@pytest.mark.parametrize("table", CONTROL_HISTORY_COLUMNS)
@pytest.mark.parametrize("sample, chosen", [("last", 3), ("first", 1), ("any", 1)])
def test_full_snapshots_survive_batches_and_restart(monkeypatch, tmp_path, table, sample, chosen):
    monkeypatch.setattr(device_polling, "RETENTION_BATCH_SIZE", 2)
    poller = DevicePoller(settings(table, sample), data_dir=tmp_path)
    path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(path) as conn:
        poller._ensure_tables(conn)
        payloads = [insert(conn, table, BUCKET + ts, value)
                    for ts, value in [(1000, 1), (2000, 2), (2000, 3)]]
        insert(conn, table, RAW_CUTOFF, 4)

    def stop_after_commit(timeout):
        assert poller._db_lock.acquire(blocking=False)
        poller._db_lock.release()
        with sqlite3.connect(path) as conn:
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 2
        poller._maintenance_stop.set()
        return True

    monkeypatch.setattr(poller._maintenance_stop, "wait", stop_after_commit)
    assert poller._apply_control_history_retention(table, NOW)["deleted_raw_rows"] == 2
    restarted = DevicePoller(settings(table, sample), data_dir=tmp_path)
    assert restarted._apply_control_history_retention(table, NOW)["deleted_raw_rows"] == 1
    assert restarted._apply_control_history_retention(table, NOW)["deleted_raw_rows"] == 0
    with sqlite3.connect(path) as conn:
        columns = ", ".join(CONTROL_HISTORY_COLUMNS[table])
        row = conn.execute(f"SELECT ts, sample_ts, sample_id, {columns} FROM {table}_rollups").fetchone()
        assert row == (BUCKET, BUCKET + (1000 if chosen == 1 else 2000), chosen, *payloads[chosen - 1])
        assert conn.execute(f"SELECT ts FROM {table}").fetchall() == [(RAW_CUTOFF,)]


@pytest.mark.parametrize("table", CONTROL_HISTORY_COLUMNS)
def test_rollup_expiration_and_atomic_rollback(tmp_path, table):
    poller = DevicePoller(settings(table), data_dir=tmp_path)
    path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(path) as conn:
        poller._ensure_tables(conn)
        insert(conn, table, BUCKET + 1000, 1)
        conn.execute(f"CREATE TRIGGER fail_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        poller._apply_control_history_retention(table, NOW)
    with sqlite3.connect(path) as conn:
        assert conn.execute(f"SELECT count(*) FROM {table}_rollups").fetchone()[0] == 0
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
        conn.execute("DROP TRIGGER fail_delete")
    poller._apply_control_history_retention(table, NOW)
    # Expiration uses bucket timestamps, with a strict lower bound.
    cutoff_now = BUCKET + 15_552_000_000
    assert poller._apply_control_history_retention(table, cutoff_now)["deleted_rollup_rows"] == 0
    assert poller._apply_control_history_retention(table, cutoff_now + 1)["deleted_rollup_rows"] == 1


@pytest.mark.parametrize("table", CONTROL_HISTORY_COLUMNS)
def test_disabled_or_missing_policy_preserves_history(tmp_path, table):
    path = tmp_path / "telemetry.sqlite3"
    poller = DevicePoller({}, data_dir=tmp_path)
    with sqlite3.connect(path) as conn:
        poller._ensure_tables(conn)
        insert(conn, table, 1000, 1)
    for cfg in ({}, {"database": {"retention": {table: {"enabled": False}}}}):
        poller = DevicePoller(cfg, data_dir=tmp_path)
        assert poller._apply_control_history_retention(table, NOW)["deleted_raw_rows"] == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def test_control_retention_is_scheduled_in_background(monkeypatch, tmp_path):
    cfg = settings("control_inputs")
    cfg["database"]["retention"].update(settings("control_decisions")["database"]["retention"])
    poller = DevicePoller(cfg, data_dir=tmp_path)
    finished = {name: Event() for name in CONTROL_HISTORY_COLUMNS}
    monkeypatch.setattr(poller, "_apply_control_history_retention", lambda name: finished[name].set())
    try:
        poller.start(paused=True)
        jobs = [job for job in poller._scheduler.get_jobs()
                if job.id not in {"maintenance-indexes", "maintenance-catalog"}]
        assert {job.id for job in jobs} == {"retention-control-inputs", "retention-control-decisions"}
        assert all(job.executor == "maintenance" for job in jobs)
        assert not any(event.is_set() for event in finished.values())
        poller.resume()
        assert all(event.wait(5) for event in finished.values())
    finally:
        poller.shutdown()


@pytest.mark.parametrize("table", CONTROL_HISTORY_COLUMNS)
def test_existing_control_table_migrates_without_losing_rows(tmp_path, table):
    path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(path) as conn:
        fields = ", ".join(f"{name} {kind}" for name, kind in CONTROL_HISTORY_COLUMNS[table].items())
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, {fields})")
        payload = insert(conn, table, BUCKET + 1000, 1)
    poller = DevicePoller(settings(table), data_dir=tmp_path)
    poller._apply_control_history_retention(table, NOW)
    with sqlite3.connect(path) as conn:
        fields = ", ".join(CONTROL_HISTORY_COLUMNS[table])
        assert conn.execute(f"SELECT {fields} FROM {table}_rollups").fetchone() == payload


@pytest.mark.parametrize("table", CONTROL_HISTORY_COLUMNS)
def test_expired_source_rows_are_removed_without_archiving(tmp_path, table):
    poller = DevicePoller(settings(table), data_dir=tmp_path)
    path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(path) as conn:
        poller._ensure_tables(conn)
        insert(conn, table, 1000, 1)
        # Other histories and numeric graph data have independent retention.
        other = next(name for name in CONTROL_HISTORY_COLUMNS if name != table)
        insert(conn, other, 1000, 1)
        conn.execute("INSERT INTO metrics (ts,device_type,device_id,metric,value) VALUES (1000,'test','1','power',1)")
    assert poller._apply_control_history_retention(table, NOW) == {
        "rolled_up_rows": 0, "deleted_raw_rows": 1, "deleted_rollup_rows": 0,
    }
    with sqlite3.connect(path) as conn:
        assert conn.execute(f"SELECT count(*) FROM {table}_rollups").fetchone()[0] == 0
        assert conn.execute(f"SELECT count(*) FROM {other}").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM metrics").fetchone()[0] == 1
