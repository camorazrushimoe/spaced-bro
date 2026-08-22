"""Logging configuration.

All log records go to stderr: stdout is reserved for data output, and the
llm-router fail-fast contract requires the startup ``ERROR`` line (naming
the offending variable) to be visible on the process stderr before the
service port is bound — including before :func:`configure_logging` has run.
"""

from __future__ import annotations

import logging
import sys

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _install_stderr_handler() -> None:
    """Attach a stderr handler to the root logger if it has none.

    Ensures early startup records (config resolution, fail-fast ERROR lines)
    reach stderr with the standard format regardless of how logging was
    previously configured.
    """
    if any(
        isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
        for h in logging.getLogger().handlers
    ):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    logging.getLogger().addHandler(handler)


_install_stderr_handler()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=_DEFAULT_FORMAT,
        stream=sys.stderr,
    )
    # Keep noisy third-party loggers quiet unless debugging.
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
