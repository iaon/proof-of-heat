import logging

from proof_of_heat.logging_utils import TRACE_LEVEL, ensure_trace_level
from proof_of_heat.services.sqlite_logging import connect_logged_sqlite


ensure_trace_level()


def test_sqlite_debug_logging_includes_query_timing_and_row_counts(caplog):
    logger = logging.getLogger("tests.sqlite.debug")
    caplog.set_level(logging.DEBUG, logger=logger.name)

    with connect_logged_sqlite(":memory:", logger=logger) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO sample (value) VALUES (?)", ("alpha",))
        rows = conn.execute("SELECT id, value FROM sample ORDER BY id").fetchall()
        conn.execute("DELETE FROM sample WHERE id = ?", (1,))

    assert rows == [(1, "alpha")]
    assert "SQLite INSERT executed in " in caplog.text
    assert "inserted 1 row: INSERT INTO sample (value) VALUES (?)" in caplog.text
    assert "SQLite SELECT executed in " in caplog.text
    assert "returned 1 row: SELECT id, value FROM sample ORDER BY id" in caplog.text
    assert "SQLite DELETE executed in " in caplog.text
    assert "deleted 1 row: DELETE FROM sample WHERE id = ?" in caplog.text


def test_sqlite_trace_logging_includes_full_result(caplog):
    logger = logging.getLogger("tests.sqlite.trace")
    caplog.set_level(TRACE_LEVEL, logger=logger.name)

    with connect_logged_sqlite(":memory:", logger=logger) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO sample (value) VALUES (?)",
            [("alpha",), ("beta",)],
        )
        rows = conn.execute("SELECT id, value FROM sample ORDER BY id").fetchall()

    assert rows == [(1, "alpha"), (2, "beta")]
    assert "SQLite SELECT result: [(1, 'alpha'), (2, 'beta')]" in caplog.text
