"""Process-level startup fail-fast contract (llm-router spec).

Every startup configuration error aborts the process with exit code 78
(EX_CONFIG) WITHOUT binding the service port, and an ERROR log line names
the offending variable. The entrypoint resolves configuration before
migrations, scheduler, or the health server (the port binding point).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

EXIT_CONFIG = 78

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"


def _boot(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "BOT_TOKEN": "123456:ABC-DEF",
            "PYTHONPATH": str(REPO_ROOT / "src"),
        }
    )
    env.update(env_extra)
    # Any DATABASE_URL / HEALTH_PORT already in the ambient env must not
    # affect these tests; the process must die in the config zone first.
    return subprocess.run(
        [str(VENV_PYTHON), "-m", "spacedbro"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
    )


@pytest.mark.parametrize(
    ("env_extra", "variable"),
    [
        ({}, "APP_ENV"),  # missing
        ({"APP_ENV": ""}, "APP_ENV"),  # empty
        ({"APP_ENV": "prod"}, "APP_ENV"),  # unknown — must not resolve production-like
        ({"APP_ENV": "development", "LLM_PROVIDER": "anthropic"}, "LLM_PROVIDER"),
        ({"APP_ENV": "production"}, "OPENAI_API_KEY"),  # missing key for openai
        ({"APP_ENV": "production", "OPENAI_API_KEY": "   "}, "OPENAI_API_KEY"),
        ({"APP_ENV": "development", "LLM_TIMEOUT_SECONDS": "0"}, "LLM_TIMEOUT_SECONDS"),
        ({"APP_ENV": "development", "LLM_MAX_RETRIES": "-1"}, "LLM_MAX_RETRIES"),
    ],
)
def test_config_error_exits_78_and_names_variable(
    env_extra: dict[str, str], variable: str
) -> None:
    proc = _boot(env_extra)

    assert proc.returncode == EXIT_CONFIG
    assert variable in proc.stderr
    # The ERROR log line names the offending variable and the allowed values.
    assert "ERROR" in proc.stderr
    # Died before migrations/health: no "Scheduler" or health-server logs.
    assert "APScheduler started" not in proc.stderr
