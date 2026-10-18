"""Add database-enforced idempotency for incident triage.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS idempotency_key TEXT")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_incidents_idempotency_key "
        "ON incidents (idempotency_key)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_incidents_idempotency_key")
    op.execute("ALTER TABLE incidents DROP COLUMN IF EXISTS idempotency_key")
