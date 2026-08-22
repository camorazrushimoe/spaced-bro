"""Logging configuration.

All log records go to stderr: stdout is reserved for data output, and the
llm-router fail-fast contract requires the startup ``ERROR`` line (naming
the offending variable) to be visible on the process stderr before the
service port is bound — including before :func:`configure_logging` has run.

BON-34 (tasks.md §7 "Logs/metrics"): records are **structured** — every
line is a single-line JSON object (``ts`` ISO-8601 UTC, ``level``,
``logger``, ``message``, ``exc_info`` when an exception is attached). The
Dockerfile captures stderr as the container log, so each line must parse
on its own; the log level comes from the environment (``LOG_LEVEL``).
The one exception is the pre-configuration zone: fail-fast ``ERROR`` lines
logged before :func:`configure_logging` runs use the plain format, because
the JSON handler is installed by that function and the plain format keeps
those earliest lines human-readable on a crashed boot.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    """One line per record, one JSON object per line.

    Field contract (the smoke checklist greps these):

    - ``ts``: ISO-8601 UTC timestamp (``...Z``)
    - ``level``: uppercase level name (``INFO``, ``ERROR``, …)
    - ``logger``: the record's logger name (``spacedbro.scheduler`` …)
    - ``message``: the fully interpolated message (lazy args applied)
    - ``exc_info``: rendered traceback — present only when the record
      carries an exception (design §9: stack traces stay in the log,
      never in user-facing replies)

    Non-ASCII (bot copy is emoji-heavy) is kept readable — no ``\\u``
    escaping. JSON is built from scalar fields only, so ``json.dumps``
    cannot fail on a record; the single-record contract is that the line
    is exactly one physical line (``default=str`` guards exotic values).
    """

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, object] = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info is not None:
            obj["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False, default=str)


def _install_stderr_handler() -> None:
    """Attach a stderr handler to the root logger if it has none.

    Ensures early startup records (config resolution, fail-fast ERROR lines)
    reach stderr with the standard format regardless of how logging was
    previously configured. :func:`configure_logging` later upgrades it to
    the JSON formatter.
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
    """Set the process log level and the structured (JSON) format.

    The root level is set explicitly, on top of ``logging.basicConfig``:
    the import-time stderr handler (see :func:`_install_stderr_handler`)
    makes ``basicConfig`` a no-op once it has run — and that is exactly
    when the level must still apply, because the llm-router spec requires
    the INFO-level ``LLM resolved configuration`` line to appear in normal
    logs without a debug flag. Every root handler is switched to the JSON
    formatter here, so from configuration onward the whole process log is
    structured.
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
        handler.setFormatter(JsonFormatter())
    # Keep noisy third-party loggers quiet unless debugging.
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
