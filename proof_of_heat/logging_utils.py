from __future__ import annotations

import logging

TRACE_LEVEL = 5


def ensure_trace_level() -> None:
    if logging.getLevelName(TRACE_LEVEL) != "TRACE":
        logging.addLevelName(TRACE_LEVEL, "TRACE")
        setattr(logging, "TRACE", TRACE_LEVEL)

    if not hasattr(logging.Logger, "trace"):
        def trace(self: logging.Logger, msg: str, *args: object, **kwargs: object) -> None:
            if self.isEnabledFor(TRACE_LEVEL):
                self._log(TRACE_LEVEL, msg, args, **kwargs)

        setattr(logging.Logger, "trace", trace)
