"""Tests for the Alembic migration runner (BON-27, acceptance criterion
"Alembic before traffic").

The runner expects ``alembic.ini`` (with its ``script_location``) to be
reachable, so each test runs from a temp copy of the repo's alembic config
against a temp SQLite file — the real checkout is never touched.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from spacedbro.migrations import run_migrations

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_root(tmp_path: Path) -> Path:
    """Temp root with a copy of alembic.ini + alembic/ (versioned scripts)."""
    shutil.copyfile(REPO_ROOT / "alembic.ini", tmp_path / "alembic.ini")
    shutil.copytree(REPO_ROOT / "alembic", tmp_path / "alembic")
    return tmp_path


@pytest.fixture
def run_migrations_isolated(alembic_root: Path):
    """Call ``run_migrations`` pointed at a temp config + temp SQLite file.

    chdirs into the temp root, pins ``ALEMBIC_CONFIG``, and clears any
    inherited ``DATABASE_URL`` so the argument (not the environment) is what
    gets tested. Environment and cwd are restored afterwards.
    """
    def _run(database_url: str) -> None:
        original_cwd = Path.cwd()
        original_config = os.environ.get("ALEMBIC_CONFIG")
        original_url = os.environ.get("DATABASE_URL")
        try:
            os.chdir(alembic_root)
            os.environ["ALEMBIC_CONFIG"] = str(alembic_root / "alembic.ini")
            os.environ.pop("DATABASE_URL", None)
            run_migrations(database_url)
        finally:
            os.chdir(original_cwd)
            os.environ.pop("ALEMBIC_CONFIG", None)
            if original_url is not None:
                os.environ["DATABASE_URL"] = original_url
            else:
                os.environ.pop("DATABASE_URL", None)

    return _run


def _stamped_versions(db_file: Path) -> list[tuple[str]]:
    with sqlite3.connect(db_file) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchall()


def test_run_migrations_stamps_head_0003(tmp_path: Path, run_migrations_isolated) -> None:
    """BON-29: a fresh DB migrates from the 0001 baseline to head (0003),
    creating users + learning_items (plus the onboarding marker, BON-31)."""
    db_file = tmp_path / "spacedbro.db"
    database_url = f"sqlite:///{db_file}"

    run_migrations_isolated(database_url)

    assert db_file.exists()
    assert _stamped_versions(db_file) == [("0003",)]
    with sqlite3.connect(db_file) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "users" in tables
    assert "learning_items" in tables


def test_run_migrations_is_idempotent(tmp_path: Path, run_migrations_isolated) -> None:
    """Upgrading to head twice must not fail (second run is a no-op)."""
    db_file = tmp_path / "spacedbro.db"
    database_url = f"sqlite:///{db_file}"

    run_migrations_isolated(database_url)
    run_migrations_isolated(database_url)

    assert _stamped_versions(db_file) == [("0003",)]
