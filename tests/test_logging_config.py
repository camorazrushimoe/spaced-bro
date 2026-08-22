"""Tests for logging configuration (llm-router spec — Observability).

The spec's verify-after-change signal is an INFO line at startup:

    "log at INFO: resolved APP_ENV, provider, model, base URL"
    "... it must appear in normal logs, not only under a debug flag."

The module also installs a stderr handler at import time so the fail-fast
ERROR line is visible before ``configure_logging`` runs (fail-fast
contract). ``configure_logging`` must therefore still work when the root
logger already has handlers — it must raise the root level, not rely on
``logging.basicConfig`` (a no-op in that case).

BON-34 (tasks.md §7 "Logs/metrics"): every line the process emits is a
single-line JSON object (structured logging) — the Dockerfile captures
stderr as the container log, so log shippers must be able to parse each
line. The level comes from the environment (``LOG_LEVEL``).
"""

from __future__ import annotations

import json
import logging
import sys

from spacedbro.logging_config import JsonFormatter, configure_logging


def _record(
    name: str = "spacedbro.contract",
    level: int = logging.INFO,
    msg: str = "hello world",
    args: tuple = (),
    exc_info=None,
) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )


def test_configure_logging_sets_root_level(caplog) -> None:
    configure_logging("INFO")

    assert logging.getLogger().level == logging.INFO


def test_configure_logging_makes_info_records_visible(caplog) -> None:
    # Regression: with the import-time stderr handler present,
    # basicConfig no-ops and the root stays at WARNING — the spec's
    # INFO line ("LLM resolved configuration: ...") would be swallowed.
    configure_logging("INFO")

    with caplog.at_level(logging.INFO):
        logging.getLogger("spacedbro.probe").info("visible info line")

    assert "visible info line" in caplog.text


def test_configure_logging_unknown_level_falls_back_to_info() -> None:
    configure_logging("not-a-level")

    assert logging.getLogger().level == logging.INFO


def test_import_time_stderr_handler_receives_error_records_before_configure(caplog) -> None:
    # The fail-fast ERROR line must reach stderr even if it is logged
    # before configure_logging has run (root at default WARNING).
    import spacedbro.logging_config  # noqa: F401 — import installs the handler

    with caplog.at_level(logging.ERROR):
        logging.getLogger("spacedbro.early").error("fail-fast ERROR line")

    assert "fail-fast ERROR line" in caplog.text


# --- Structured (JSON) format — BON-34, tasks.md §7 "Logs/metrics" --------------


def test_json_format_is_one_line_one_object() -> None:
    # Field contract: ts (ISO-8601 UTC), level, logger, message. The smoke
    # checklist greps the "message" field; the level name is uppercase so
    # `grep '"level":"ERROR"'` works in the container log.
    obj = json.loads(JsonFormatter().format(_record(msg="hello world")))

    assert obj["message"] == "hello world"
    assert obj["level"] == "INFO"
    assert obj["logger"] == "spacedbro.contract"
    assert obj["ts"].endswith("Z")
    # No free-text framing: the whole line IS the JSON object.
    assert JsonFormatter().format(_record()) == json.dumps(obj, ensure_ascii=False)


def test_json_format_interpolates_lazy_args() -> None:
    obj = json.loads(
        JsonFormatter().format(_record(msg="nudge %s (%d due)", args=(11111, 3)))
    )

    assert obj["message"] == "nudge 11111 (3 due)"


def test_json_format_keeps_unicode_readable() -> None:
    # Bot copy is emoji-heavy; escaping it would defeat grep-ability.
    obj = json.loads(
        JsonFormatter().format(_record(msg="Boosted \u26A1 — back to the start"))
    )

    assert obj["message"] == "Boosted \u26A1 — back to the start"


def test_json_format_serializes_exception_info() -> None:
    # Stack traces stay in the log (server-side) — design §9 keeps them
    # away from USERS, not from the log.
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record(
            name="spacedbro.exc",
            level=logging.ERROR,
            msg="failed",
            exc_info=sys.exc_info(),
        )

    obj = json.loads(JsonFormatter().format(record))

    assert obj["message"] == "failed"
    assert obj["level"] == "ERROR"
    assert "ValueError" in obj["exc_info"]
    assert "boom" in obj["exc_info"]
    assert "Traceback" in obj["exc_info"]


def test_json_format_omits_exc_info_when_absent() -> None:
    obj = json.loads(JsonFormatter().format(_record(msg="no exception")))

    assert "exc_info" not in obj


def test_configure_logging_applies_the_json_formatter_to_stderr_handler() -> None:
    # The import-time handler keeps the standard format until configure runs
    # (fail-fast lines are pre-JSON); configure_logging must switch every
    # root handler to the JSON formatter so the whole process log is
    # structured from configuration onward.
    import sys

    configure_logging("INFO")

    root = logging.getLogger()
    json_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and h.stream is sys.stderr
        and isinstance(h.formatter, JsonFormatter)
    ]
    assert json_handlers, "the stderr handler must use the JSON formatter"
