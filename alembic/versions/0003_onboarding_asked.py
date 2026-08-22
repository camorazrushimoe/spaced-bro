"""Onboarding marker (BON-31).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-22

Adds ``users.onboarding_asked`` — the flag behind the telegram-bot spec
"Start command" rule: ``/start`` SHALL ask the target language **once**
(user-memory spec "Onboarding question"). Existing rows default to 0
(FALSE), i.e. a user who has never seen the question still gets it on their
next ``/start``.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "onboarding_asked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "onboarding_asked")
