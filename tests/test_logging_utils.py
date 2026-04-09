import logging
from datetime import datetime, timezone
import time

from uvicorn.logging import AccessFormatter, DefaultFormatter

from proof_of_heat.logging_utils import (
    ColorFormatter,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    UVICORN_ACCESS_LOG_FORMAT,
    UVICORN_DEFAULT_LOG_FORMAT,
    build_uvicorn_log_config,
)


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


def test_build_uvicorn_log_config_adds_timestamps_to_default_and_access_logs():
    log_config = build_uvicorn_log_config()
    default_config = log_config["formatters"]["default"]
    access_config = log_config["formatters"]["access"]

    assert default_config["fmt"] == UVICORN_DEFAULT_LOG_FORMAT
    assert default_config["datefmt"] == LOG_DATE_FORMAT
    assert access_config["fmt"] == UVICORN_ACCESS_LOG_FORMAT
    assert access_config["datefmt"] == LOG_DATE_FORMAT

    default_formatter = DefaultFormatter(
        default_config["fmt"],
        datefmt=default_config["datefmt"],
        use_colors=False,
    )
    default_formatter.converter = time.gmtime
    access_formatter = AccessFormatter(
        access_config["fmt"],
        datefmt=access_config["datefmt"],
        use_colors=False,
    )
    access_formatter.converter = time.gmtime

    timestamp = datetime(2026, 12, 3, 13, 23, 55, tzinfo=timezone.utc).timestamp()

    default_record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=20,
        msg="Application startup complete.",
        args=(),
        exc_info=None,
    )
    default_record.created = timestamp
    default_record.msecs = 0.0

    access_record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=21,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("172.20.0.1:54008", "GET", "/poh/metrics", "1.0", 200),
        exc_info=None,
    )
    access_record.created = timestamp
    access_record.msecs = 0.0

    assert default_formatter.format(default_record) == "2026-12-03 13:23:55 INFO:     Application startup complete."
    assert access_formatter.format(access_record) == '2026-12-03 13:23:55 INFO:     172.20.0.1:54008 - "GET /poh/metrics HTTP/1.0" 200 OK'
