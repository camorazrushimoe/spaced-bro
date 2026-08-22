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
    """Set the process log level and format.

    The root level is set explicitly, on top of ``logging.basicConfig``:
    the import-time stderr handler (see :func:`_install_stderr_handler`)
    makes ``basicConfig`` a no-op once it has run — and that is exactly
    when the level must still apply, because the llm-router spec requires
    the INFO-level ``LLM resolved configuration`` line to appear in normal
    logs without a debug flag.
    """
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=resolved_level,
        format=_DEFAULT_FORMAT,
        stream=sys.stderr,
    )
    root = logging.getLogger()
    root.setLevel(resolved_level)
    for handler in root.handlers:
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    # Keep noisy third-party loggers quiet unless debugging.
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
