"""add research_report.previous_report_id

Revision ID: 0c0a80524432
Revises: 9336d284d20f
Create Date: 2026-09-28 15:14:31.262396

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0c0a80524432'
down_revision: Union[str, Sequence[str], None] = '9336d284d20f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('research_report', sa.Column('previous_report_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        'fk_research_report_previous_report_id',
        'research_report', 'research_report',
        ['previous_report_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_research_report_previous_report_id', 'research_report', type_='foreignkey')
    op.drop_column('research_report', 'previous_report_id')
