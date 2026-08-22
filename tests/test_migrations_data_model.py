"""Schema acceptance tests for the data model (BON-29).

Verifies the migration-built schema column-for-column against
``openspec/changes/mvp-core/design.md`` "Data model (sketch)" and the
user-memory / learning-items specs:

- ``users``: profile fields with spec defaults (``native_lang`` = 'ru',
  ``target_lang`` = 'en') and UTC activity/proactive fields.
- ``learning_items``: card + SRS fields with New-state defaults, and the
  UNIQUE(user_id, normalized_front) constraint from
  learning-items/spec.md "Front normalization".
- Migrations apply idempotently on a fresh DB and on upgrade (design §10)
  and downgrade/upgrade cycles cleanly.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from spacedbro.migrations import run_migrations

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Exact column sets from design.md "Data model (sketch)".
EXPECTED_USERS_COLUMNS = {
    "id",
    "telegram_id",
    "native_lang",
    "target_lang",
    "ui_lang",
    "level_estimate",
    "created_at",
    "last_active_at",
    "activity_hours_utc",
    "proactive_count",
    "proactive_count_date",
    "onboarding_asked",
}
EXPECTED_ITEMS_COLUMNS = {
    "id",
    "user_id",
    "front",
    "normalized_front",
    "back",
    "context",
    "ease",
    "interval_minutes",
    "repetitions",
    "next_review_at",
    "last_review_at",
    "status",
    "created_at",
}


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    """Fresh temp SQLite database migrated to head."""
    db_file = tmp_path / "spacedbro.db"
    original_cwd = Path.cwd()
    original_config = os.environ.get("ALEMBIC_CONFIG")
    original_url = os.environ.get("DATABASE_URL")
    try:
        os.environ["ALEMBIC_CONFIG"] = str(REPO_ROOT / "alembic.ini")
        os.environ.pop("DATABASE_URL", None)
        run_migrations(f"sqlite:///{db_file}")
    finally:
        os.chdir(original_cwd)
        if original_config is not None:
            os.environ["ALEMBIC_CONFIG"] = original_config
        else:
            os.environ.pop("ALEMBIC_CONFIG", None)
        if original_url is not None:
            os.environ["DATABASE_URL"] = original_url
        else:
            os.environ.pop("DATABASE_URL", None)
    return db_file


def _columns(db_file: Path, table: str) -> dict[str, str]:
    """column name -> lowercased SQLite declared type."""
    with sqlite3.connect(db_file) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1]: row[2].lower() for row in rows}


def _indexes(db_file: Path, table: str) -> list[tuple[str, list[str], bool]]:
    """[(index_name, [columns], unique)] for a table's indexes."""
    with sqlite3.connect(db_file) as conn:
        idx = conn.execute(f"PRAGMA index_list({table})").fetchall()
        out = []
        for _seq, name, unique, _origin, _partial in idx:
            cols = [r[2] for r in conn.execute(f"PRAGMA index_info({name})").fetchall()]
            out.append((name, cols, bool(unique)))
    return out


def test_migrations_stamp_0003_on_fresh_db(migrated_db: Path) -> None:
    with sqlite3.connect(migrated_db) as conn:
        versions = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    assert versions == [("0003",)]


def test_users_table_matches_design_sketch(migrated_db: Path) -> None:
    assert set(_columns(migrated_db, "users")) == EXPECTED_USERS_COLUMNS


def test_learning_items_table_matches_design_sketch(migrated_db: Path) -> None:
    assert set(_columns(migrated_db, "learning_items")) == EXPECTED_ITEMS_COLUMNS


def test_users_defaults(migrated_db: Path) -> None:
    cols = _columns(migrated_db, "users")
    # Spec defaults are stored as column DEFAULTs so a bare INSERT gets them.
    with sqlite3.connect(migrated_db) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
    ddl = row[0]
    assert "native_lang" in cols
    assert "target_lang" in cols
    assert "'ru'" in ddl or '"ru"' in ddl, "native_lang DEFAULT 'ru' missing"
    assert "'en'" in ddl or '"en"' in ddl, "target_lang DEFAULT 'en' missing"
    # onboarding_asked arrives via ALTER (migration 0003) — the stored CREATE
    # statement is not rewritten, so its default is checked via PRAGMA.
    with sqlite3.connect(migrated_db) as conn:
        pragma_rows = conn.execute("PRAGMA table_info(users)").fetchall()
    col = next(r for r in pragma_rows if r[1] == "onboarding_asked")
    assert col[3] == 1, "onboarding_asked must be NOT NULL"
    assert str(col[4]) == "0", "onboarding_asked DEFAULT 0 missing"


def test_learning_items_new_state_defaults(migrated_db: Path) -> None:
    with sqlite3.connect(migrated_db) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='learning_items'"
        ).fetchone()
    ddl = row[0]
    # Design §6 New / Boost state as column defaults.
    assert "2.5" in ddl, "ease DEFAULT 2.5 missing"
    assert "20" in ddl, "interval_minutes DEFAULT 20 missing"
    assert "0" in ddl, "repetitions DEFAULT 0 missing"
    assert "'learning'" in ddl or '"learning"' in ddl, "status DEFAULT 'learning' missing"


def test_items_unique_user_id_normalized_front(migrated_db: Path) -> None:
    unique_pairs = [
        (cols, unique)
        for _name, cols, unique in _indexes(migrated_db, "learning_items")
        if unique
    ]
    assert (["user_id", "normalized_front"], True) in [
        (cols, unique) for cols, unique in unique_pairs
    ]


def test_users_unique_telegram_id(migrated_db: Path) -> None:
    unique_cols = [
        cols
        for _name, cols, unique in _indexes(migrated_db, "users")
        if unique
    ]
    assert ["telegram_id"] in unique_cols


def test_items_foreign_key_to_users(migrated_db: Path) -> None:
    with sqlite3.connect(migrated_db) as conn:
        fks = conn.execute("PRAGMA foreign_key_list(learning_items)").fetchall()
    assert any(fk[2] == "users" and fk[3] == "user_id" and fk[4] == "id" for fk in fks)


def test_migration_idempotent_on_upgrade(migrated_db: Path) -> None:
    """Upgrading an already-migrated DB again must be a clean no-op."""
    original_cwd = Path.cwd()
    original_config = os.environ.get("ALEMBIC_CONFIG")
    original_url = os.environ.get("DATABASE_URL")
    try:
        os.environ["ALEMBIC_CONFIG"] = str(REPO_ROOT / "alembic.ini")
        os.environ["DATABASE_URL"] = f"sqlite:///{migrated_db}"
        ini = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(ini, "head")
    finally:
        os.chdir(original_cwd)
        os.environ.pop("ALEMBIC_CONFIG", None)
        if original_url is not None:
            os.environ["DATABASE_URL"] = original_url
        else:
            os.environ.pop("DATABASE_URL", None)

    with sqlite3.connect(migrated_db) as conn:
        versions = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    assert versions == [("0003",)]
    assert set(_columns(migrated_db, "users")) == EXPECTED_USERS_COLUMNS


def test_downgrade_and_upgrade_roundtrip(tmp_path: Path) -> None:
    """0002 downgrades cleanly back to 0001 and upgrades again."""
    original_url = os.environ.get("DATABASE_URL")
    db_file = tmp_path / "rt.db"
    ini = Config(str(REPO_ROOT / "alembic.ini"))
    # alembic/env.py resolves the URL from DATABASE_URL — pin it explicitly.
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"
    try:
        command.upgrade(ini, "head")
        with sqlite3.connect(db_file) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "users" in tables and "learning_items" in tables

        command.downgrade(ini, "0001")
        with sqlite3.connect(db_file) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            versions = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        assert "users" not in tables and "learning_items" not in tables
        assert versions == [("0001",)]

        command.upgrade(ini, "head")
        with sqlite3.connect(db_file) as conn:
            versions = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        assert versions == [("0003",)]
        assert set(_columns(db_file, "learning_items")) == EXPECTED_ITEMS_COLUMNS
    finally:
        if original_url is not None:
            os.environ["DATABASE_URL"] = original_url
        else:
            os.environ.pop("DATABASE_URL", None)


def test_head_is_0003() -> None:
    ini = Config(str(REPO_ROOT / "alembic.ini"))
    ini.set_main_option("sqlalchemy.url", "sqlite://")
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(ini)
    assert script.get_current_head() == "0003"


def test_orm_metadata_matches_migration(tmp_path: Path) -> None:
    """Autogenerate against the migrated DB must be a no-op.

    ``alembic/env.py`` points autogenerate at ``Base.metadata``, so the ORM
    models are the second source of schema truth. This test fails if a model
    edit diverges from the migration (or vice versa), keeping future
    autogenerate runs drift-free.
    """
    import re
    import shutil

    db_file = tmp_path / "drift.db"
    alembic_copy = tmp_path / "alembic"
    shutil.copytree(REPO_ROOT / "alembic", alembic_copy)

    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"
    try:
        ini = Config(str(REPO_ROOT / "alembic.ini"))
        ini.set_main_option("script_location", str(alembic_copy))
        command.upgrade(ini, "head")
        command.revision(ini, "drift-check", autogenerate=True)

        gen_file = max(
            (alembic_copy / "versions").glob("*.py"), key=lambda p: p.stat().st_mtime
        )
        ops = [
            line
            for line in gen_file.read_text().splitlines()
            if re.search(r"^\s*op\.", line)
        ]
        assert not ops, f"ORM metadata drifted from migration: {ops}"
    finally:
        if original_url is not None:
            os.environ["DATABASE_URL"] = original_url
        else:
            os.environ.pop("DATABASE_URL", None)
