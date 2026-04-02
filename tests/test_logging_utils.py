import logging
from datetime import datetime, timezone
import time

from proof_of_heat.logging_utils import ColorFormatter, LOG_DATE_FORMAT, LOG_FORMAT


def test_color_formatter_includes_timestamp_and_right_aligned_level(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    formatter = ColorFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    formatter.converter = time.gmtime

    record = logging.LogRecord(
        name="proof_of_heat",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="temperature updated",
        args=(),
        exc_info=None,
    )
    record.created = datetime(2026, 12, 3, 13, 23, 55, tzinfo=timezone.utc).timestamp()
    record.msecs = 0.0

    assert formatter.format(record) == "2026-12-03 13:23:55     INFO: temperature updated"


def test_color_formatter_keeps_level_column_aligned(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    formatter = ColorFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    formatter.converter = time.gmtime
    timestamp = datetime(2026, 12, 3, 13, 23, 55, tzinfo=timezone.utc).timestamp()

    info_record = logging.LogRecord("proof_of_heat", logging.INFO, __file__, 10, "info", (), None)
    warning_record = logging.LogRecord("proof_of_heat", logging.WARNING, __file__, 11, "warning", (), None)
    info_record.created = warning_record.created = timestamp
    info_record.msecs = warning_record.msecs = 0.0

    info_message = formatter.format(info_record)
    warning_message = formatter.format(warning_record)

    assert info_message.rindex(":") == warning_message.rindex(":")
