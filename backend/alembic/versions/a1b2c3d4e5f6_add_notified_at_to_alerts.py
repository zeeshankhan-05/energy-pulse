"""add notified_at to alerts

Revision ID: a1b2c3d4e5f6
Revises: 23213b7c087e
Create Date: 2026-03-08 00:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '23213b7c087e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add notified_at timestamp column to alerts table."""
    op.add_column(
        'alerts',
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove notified_at column from alerts table."""
    op.drop_column('alerts', 'notified_at')
