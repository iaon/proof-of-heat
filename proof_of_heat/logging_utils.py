from __future__ import annotations

import logging
import os

TRACE_LEVEL = 5
RESET = "\033[0m"
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
        return f"{color}{message}{RESET}"


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
    handler.setFormatter(ColorFormatter("%(levelname)s:%(name)s:%(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)
