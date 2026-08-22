"""Tests for logging configuration (llm-router spec — Observability).

The spec's verify-after-change signal is an INFO line at startup:

    "log at INFO: resolved APP_ENV, provider, model, base URL"
    "... it must appear in normal logs, not only under a debug flag."

The module also installs a stderr handler at import time so the fail-fast
ERROR line is visible before ``configure_logging`` runs (fail-fast
contract). ``configure_logging`` must therefore still work when the root
logger already has handlers — it must raise the root level, not rely on
``logging.basicConfig`` (a no-op in that case).
"""

from __future__ import annotations

import logging

from spacedbro.logging_config import configure_logging


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
