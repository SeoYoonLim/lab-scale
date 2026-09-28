"""add market_index table

Revision ID: 9336d284d20f
Revises: 71c96e49120c
Create Date: 2026-09-28 13:48:58.304095

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9336d284d20f'
down_revision: Union[str, Sequence[str], None] = '71c96e49120c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'market_index',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('index_code', sa.String(length=10), nullable=False),
        sa.Column('price_date', sa.Date(), nullable=False),
        sa.Column('close_price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('change_pct', sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('index_code', 'price_date', name='uq_market_index_code_date'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('market_index')
