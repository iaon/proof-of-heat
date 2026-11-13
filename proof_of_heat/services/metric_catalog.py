"""Persistent metric discovery without scanning telemetry on page requests."""
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)
SOURCES = ("metrics", "metric_rollups")


def ensure_catalog_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS metric_catalog (
        device_type TEXT NOT NULL, device_id TEXT NOT NULL, metric TEXT NOT NULL,
        PRIMARY KEY (device_type, device_id, metric)) WITHOUT ROWID""")
    conn.execute("""CREATE TABLE IF NOT EXISTS metric_catalog_progress (
        source TEXT PRIMARY KEY, last_id INTEGER NOT NULL DEFAULT 0,
        complete INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("CREATE TABLE IF NOT EXISTS metric_catalog_resolutions (resolution_seconds INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE IF NOT EXISTS metric_catalog_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)")
    conn.execute("INSERT OR IGNORE INTO metric_catalog_version VALUES (1, 0)")
    for operation in ("INSERT", "DELETE"):
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS catalog_version_{operation.lower()}
            AFTER {operation} ON metric_catalog BEGIN
            UPDATE metric_catalog_version SET version = version + 1 WHERE id = 1;
            END""")
    for source in SOURCES:
        # Only a primary-key existence probe, including for a large legacy DB.
        conn.execute("INSERT OR IGNORE INTO metric_catalog_progress(source, complete) "
                     f"VALUES (?, NOT EXISTS (SELECT 1 FROM {source} LIMIT 1))", (source,))
        resolution = ("INSERT OR IGNORE INTO metric_catalog_resolutions VALUES (NEW.resolution_seconds);"
                      if source == "metric_rollups" else "")
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS catalog_insert_{source}
            AFTER INSERT ON {source} BEGIN
            INSERT OR IGNORE INTO metric_catalog VALUES (NEW.device_type, NEW.device_id, NEW.metric);
            {resolution}
            END""")


def catalog_ready(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT count(*) = 2 AND min(complete) = 1 FROM metric_catalog_progress").fetchone()[0] == 1


def prune_catalog(conn: sqlite3.Connection, keys: list[tuple[str, str, str]]) -> int:
    # All legacy resolutions must be discovered before absence can be established.
    if not keys or not catalog_ready(conn):
        return 0
    removed = 0
    for key in set(keys):
        removed += conn.execute("""DELETE FROM metric_catalog
            WHERE device_type=? AND device_id=? AND metric=?
            AND NOT EXISTS (SELECT 1 FROM metrics
                WHERE device_type=? AND device_id=? AND metric=? LIMIT 1)
            AND NOT EXISTS (
                SELECT 1 FROM metric_catalog_resolutions r
                WHERE EXISTS (SELECT 1 FROM metric_rollups m
                    WHERE m.resolution_seconds=r.resolution_seconds
                      AND m.device_type=? AND m.device_id=? AND m.metric=? LIMIT 1))""",
            (*key, *key, *key),
        ).rowcount
    return removed


class MetricCatalog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.lock = Lock()
        self.version = -1
        self.cached: dict = {"catalog": {}, "ready": False}

    def snapshot(self) -> dict:
        started = time.perf_counter()
        # Serialize only small catalog reads, never telemetry maintenance.
        with self.lock:
            if not self.path:
                return {"catalog": {}, "ready": True}
            try:
                with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True,
                                             timeout=0)) as conn:
                    conn.execute("BEGIN")
                    version = conn.execute("SELECT version FROM metric_catalog_version WHERE id=1").fetchone()[0]
                    ready = catalog_ready(conn)
                    if version != self.version:
                        catalog: dict = {}
                        for kind, device, metric in conn.execute(
                            "SELECT device_type, device_id, metric FROM metric_catalog ORDER BY device_type, device_id, metric"
                        ):
                            catalog.setdefault(kind, {}).setdefault(device, []).append(metric)
                        self.cached = {"catalog": catalog, "ready": ready}
                        self.version = version
                    else:
                        self.cached["ready"] = ready
            except sqlite3.OperationalError as exc:
                # A writer holding an exclusive lock must not stall page rendering.
                # Also handles requests before schema initialization has finished.
                logger.debug("Serving cached metric catalog: %s", exc)
            result = deepcopy(self.cached)
        logger.debug("Metric catalog snapshot returned in %.3f seconds", time.perf_counter() - started)
        return result
