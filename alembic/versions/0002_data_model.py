"""Data model migration (BON-29).

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-21

Creates the ``users`` and ``learning_items`` tables per
``openspec/changes/mvp-core/design.md`` "Data model (sketch)" and the
user-memory / learning-items capability specs.

Notes:
- All datetime columns are UTC. SQLite ``DATETIME`` stores wall-clock text
  without a timezone, so callers must persist aware-UTC values (the
  repository layer enforces this); values are read back as naive and the
  repository layer re-attaches UTC.
- ``learning_items`` carries UNIQUE(user_id, normalized_front) — the exact
  deduplication rule of learning-items/spec.md "Front normalization".
- ``users.activity_hours_utc`` is a JSON array of 24 integers (UTC hour
  buckets); SQLite has no fixed-length array type.
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.Integer(), nullable=False),
        sa.Column("native_lang", sa.String(length=8), nullable=False, server_default="ru"),
        sa.Column("target_lang", sa.String(length=8), nullable=False, server_default="en"),
        sa.Column("ui_lang", sa.String(length=8), nullable=True),
        sa.Column(
            "level_estimate", sa.String(length=16), nullable=False, server_default="beginner"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activity_hours_utc", sa.JSON(), nullable=False),
        sa.Column("proactive_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proactive_count_date", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)

    op.create_table(
        "learning_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("front", sa.Text(), nullable=False),
        sa.Column("normalized_front", sa.Text(), nullable=False),
        sa.Column("back", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("ease", sa.Float(), nullable=False, server_default=sa.text("2.5")),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default=sa.text("20")),
        sa.Column("repetitions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="learning"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id", "normalized_front", name="uq_learning_items_user_normalized_front"
        ),
    )
    op.create_index(op.f("ix_learning_items_user_id"), "learning_items", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_learning_items_user_id"), table_name="learning_items")
    op.drop_table("learning_items")
    op.drop_index(op.f("ix_users_telegram_id"), table_name="users")
    op.drop_table("users")
