"""add exchange_rate table

Revision ID: d1bbfc5e2f0f
Revises: 45a92b436bc1
Create Date: 2026-10-01 12:07:18.137895

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1bbfc5e2f0f'
down_revision: Union[str, Sequence[str], None] = '45a92b436bc1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'exchange_rate',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('pair_code', sa.String(length=10), nullable=False),
        sa.Column('price_date', sa.Date(), nullable=False),
        sa.Column('close_price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('change_pct', sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pair_code', 'price_date', name='uq_exchange_rate_pair_date'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('exchange_rate')
