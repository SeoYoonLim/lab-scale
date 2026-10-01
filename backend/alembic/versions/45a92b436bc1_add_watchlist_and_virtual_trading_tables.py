"""add watchlist and virtual trading tables

Revision ID: 45a92b436bc1
Revises: 0c0a80524432
Create Date: 2026-10-01 11:25:05.552376

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '45a92b436bc1'
down_revision: Union[str, Sequence[str], None] = '0c0a80524432'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'virtual_account',
        sa.Column('device_id', sa.String(length=100), nullable=False),
        sa.Column('cash_balance', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('device_id'),
    )
    op.create_table(
        'holding',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('device_id', sa.String(length=100), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.BigInteger(), nullable=False),
        sa.Column('avg_price', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['virtual_account.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', 'company_id', name='uq_holding_device_company'),
    )
    op.create_table(
        'trade',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('device_id', sa.String(length=100), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('side', sa.String(length=4), nullable=False),
        sa.Column('quantity', sa.BigInteger(), nullable=False),
        sa.Column('price', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('executed_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['virtual_account.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_trade_device', 'trade', ['device_id'], unique=False)
    op.create_table(
        'watchlist',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('device_id', sa.String(length=100), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', 'company_id', name='uq_watchlist_device_company'),
    )
    op.create_index('idx_watchlist_device', 'watchlist', ['device_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_watchlist_device', table_name='watchlist')
    op.drop_table('watchlist')
    op.drop_index('idx_trade_device', table_name='trade')
    op.drop_table('trade')
    op.drop_table('holding')
    op.drop_table('virtual_account')
