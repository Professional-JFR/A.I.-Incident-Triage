"""Add indices for recent-incident and service queries.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_incidents_created_at ON incidents (created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_incidents_service ON incidents (service)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_incidents_created_at_service ON incidents (created_at DESC, service)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_incidents_created_at_service")
    op.execute("DROP INDEX IF EXISTS ix_incidents_service")
    op.execute("DROP INDEX IF EXISTS ix_incidents_created_at")
