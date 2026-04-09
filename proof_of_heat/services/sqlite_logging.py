from __future__ import annotations

import logging
import sqlite3
import time
from functools import partial
from pathlib import Path
from pprint import pformat
from typing import Any

from proof_of_heat.logging_utils import TRACE_LEVEL


def _normalize_sql(sql: str) -> str:
    return " ".join(str(sql).split())


def _statement_kind(sql: str) -> str:
    normalized = _normalize_sql(sql)
    if not normalized:
        return "SQL"
    return normalized.split(" ", 1)[0].upper()


def _statement_result_verb(statement_kind: str, *, has_rows: bool) -> str:
    if has_rows:
        return "returned"
    if statement_kind in {"INSERT", "REPLACE"}:
        return "inserted"
    if statement_kind == "UPDATE":
        return "updated"
    if statement_kind == "DELETE":
        return "deleted"
    return "affected"


class LoggedSQLiteCursor(sqlite3.Cursor):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)
        sql_logger = getattr(connection, "_sql_logger", None)
        self._sql_logger: logging.Logger = (
            sql_logger if isinstance(sql_logger, logging.Logger) else logging.getLogger("proof_of_heat.sqlite")
        )
        self._statement = ""
        self._statement_kind = "SQL"
        self._started_at: float | None = None
        self._pending_result_log = False
        self._debug_enabled = False
        self._trace_enabled = False

    def execute(self, sql: str, parameters: Any = ()) -> "LoggedSQLiteCursor":
        self._prepare_logging(sql)
        try:
            cursor = super().execute(sql, parameters)
        except Exception:
            self._log_failure()
            raise
        self._after_execute(has_result_rows=self.description is not None)
        return cursor

    def executemany(self, sql: str, seq_of_parameters: Any) -> "LoggedSQLiteCursor":
        self._prepare_logging(sql)
        try:
            cursor = super().executemany(sql, seq_of_parameters)
        except Exception:
            self._log_failure()
            raise
        self._after_execute(has_result_rows=False)
        return cursor

    def fetchone(self) -> Any:
        row = super().fetchone()
        if self._pending_result_log:
            row_count = 0 if row is None else 1
            self._log_completion(row_count=row_count, result=row, has_rows=True)
        return row

    def fetchall(self) -> list[Any]:
        rows = super().fetchall()
        if self._pending_result_log:
            self._log_completion(row_count=len(rows), result=rows, has_rows=True)
        return rows

    def fetchmany(self, size: int | None = None) -> list[Any]:
        rows = super().fetchmany() if size is None else super().fetchmany(size)
        if self._pending_result_log:
            self._log_completion(row_count=len(rows), result=rows, has_rows=True)
        return rows

    def _prepare_logging(self, sql: str) -> None:
        self._statement = _normalize_sql(sql)
        self._statement_kind = _statement_kind(sql)
        self._started_at = time.perf_counter()
        self._pending_result_log = False
        self._debug_enabled = self._sql_logger.isEnabledFor(logging.DEBUG)
        self._trace_enabled = self._sql_logger.isEnabledFor(TRACE_LEVEL)

    def _after_execute(self, *, has_result_rows: bool) -> None:
        if not self._debug_enabled:
            return
        if has_result_rows:
            self._pending_result_log = True
            return
        self._log_completion(
            row_count=self.rowcount if self.rowcount is not None and self.rowcount >= 0 else None,
            result={
                "rowcount": self.rowcount,
                "lastrowid": self.lastrowid,
            },
            has_rows=False,
        )

    def _elapsed_ms(self) -> float:
        if self._started_at is None:
            return 0.0
        return (time.perf_counter() - self._started_at) * 1000.0

    def _log_completion(self, *, row_count: int | None, result: Any, has_rows: bool) -> None:
        if not self._debug_enabled:
            return
        verb = _statement_result_verb(self._statement_kind, has_rows=has_rows)
        if row_count is None:
            count_text = "rowcount unavailable"
        else:
            noun = "row" if row_count == 1 else "rows"
            count_text = f"{verb} {row_count} {noun}"
        self._sql_logger.debug(
            "SQLite %s executed in %.2f ms, %s: %s",
            self._statement_kind,
            self._elapsed_ms(),
            count_text,
            self._statement,
        )
        if self._trace_enabled:
            self._sql_logger.log(
                TRACE_LEVEL,
                "SQLite %s result: %s",
                self._statement_kind,
                pformat(result, width=120, compact=False),
            )
        self._pending_result_log = False

    def _log_failure(self) -> None:
        if not self._debug_enabled:
            return
        self._sql_logger.exception(
            "SQLite %s failed after %.2f ms: %s",
            self._statement_kind,
            self._elapsed_ms(),
            self._statement,
        )
        self._pending_result_log = False


class LoggedSQLiteConnection(sqlite3.Connection):
    def __init__(self, *args: Any, logger: logging.Logger, **kwargs: Any) -> None:
        self._sql_logger = logger
        super().__init__(*args, **kwargs)

    def cursor(self, factory: type[sqlite3.Cursor] | None = None) -> sqlite3.Cursor:
        return super().cursor(factory or LoggedSQLiteCursor)

    def execute(self, sql: str, parameters: Any = (), /) -> LoggedSQLiteCursor:
        cursor = self.cursor()
        return cursor.execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any, /) -> LoggedSQLiteCursor:
        cursor = self.cursor()
        return cursor.executemany(sql, seq_of_parameters)


def connect_logged_sqlite(
    database: str | Path,
    *,
    logger: logging.Logger,
    isolation_level: str | None = "",
) -> sqlite3.Connection:
    factory = partial(LoggedSQLiteConnection, logger=logger)
    return sqlite3.connect(database, isolation_level=isolation_level, factory=factory)
