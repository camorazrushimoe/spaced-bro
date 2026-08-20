"""Baseline migration.

Revision ID: 0001
Revises:
Create Date: 2026-08-20

Empty baseline establishing Alembic versioning for the project. The initial
schema (users, learning_items) is added by the database ticket (BON-28).
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
