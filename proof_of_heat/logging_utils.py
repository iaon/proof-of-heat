from __future__ import annotations

from copy import deepcopy
import logging
import os
from typing import Any

TRACE_LEVEL = 5
RESET = "\033[0m"
LOG_FORMAT = "%(asctime)s %(levelname)8s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
UVICORN_DEFAULT_LOG_FORMAT = "%(asctime)s %(levelprefix)s %(message)s"
UVICORN_ACCESS_LOG_FORMAT = '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
LEVEL_COLORS = {
    TRACE_LEVEL: "\033[36m",
    logging.DEBUG: "\033[92m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if os.getenv("NO_COLOR"):
            return message
        color = LEVEL_COLORS.get(record.levelno)
        if not color:
            return message
        level_marker = f"{record.levelname}:"
        colored_marker = f"{color}{level_marker}{RESET}"
        return message.replace(level_marker, colored_marker, 1)


def ensure_trace_level() -> None:
    if logging.getLevelName(TRACE_LEVEL) != "TRACE":
        logging.addLevelName(TRACE_LEVEL, "TRACE")
        setattr(logging, "TRACE", TRACE_LEVEL)

    if not hasattr(logging.Logger, "trace"):
        def trace(self: logging.Logger, msg: str, *args: object, **kwargs: object) -> None:
            if self.isEnabledFor(TRACE_LEVEL):
                self._log(TRACE_LEVEL, msg, args, **kwargs)

        setattr(logging.Logger, "trace", trace)


def configure_logging(level: int) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)


def build_uvicorn_log_config() -> dict[str, Any]:
    from uvicorn.config import LOGGING_CONFIG

    log_config = deepcopy(LOGGING_CONFIG)
    log_config["formatters"]["default"]["fmt"] = UVICORN_DEFAULT_LOG_FORMAT
    log_config["formatters"]["default"]["datefmt"] = LOG_DATE_FORMAT
    log_config["formatters"]["access"]["fmt"] = UVICORN_ACCESS_LOG_FORMAT
    log_config["formatters"]["access"]["datefmt"] = LOG_DATE_FORMAT
    return log_config
