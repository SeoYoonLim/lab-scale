"""add quarterly_earnings table (DART 주요 계정 손익 세 항목)

Revision ID: b7e3d91a4c58
Revises: a9d3f6c12b47
Create Date: 2026-11-03 20:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e3d91a4c58'
down_revision: Union[str, Sequence[str], None] = 'a9d3f6c12b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'quarterly_earnings',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('bsns_year', sa.Integer(), nullable=False),
        sa.Column('reprt_code', sa.String(length=5), nullable=False),
        sa.Column('fs_div', sa.String(length=3), nullable=False),
        sa.Column('revenue', sa.Numeric(precision=20, scale=0), nullable=True),
        sa.Column('operating_income', sa.Numeric(precision=20, scale=0), nullable=True),
        sa.Column('net_income', sa.Numeric(precision=20, scale=0), nullable=True),
        sa.Column('revenue_prev', sa.Numeric(precision=20, scale=0), nullable=True),
        sa.Column('operating_income_prev', sa.Numeric(precision=20, scale=0), nullable=True),
        sa.Column('net_income_prev', sa.Numeric(precision=20, scale=0), nullable=True),
        sa.Column('rcept_no', sa.String(length=14), nullable=False),
        sa.Column('disclosed_date', sa.Date(), nullable=False),
        sa.Column('source', sa.String(length=30), server_default='dart_fnlttSinglAcnt', nullable=False),
        sa.Column('collected_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'bsns_year', 'reprt_code', name='uq_quarterly_earnings_report'),
    )
    op.create_index('ix_quarterly_earnings_company_id', 'quarterly_earnings', ['company_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_quarterly_earnings_company_id', table_name='quarterly_earnings')
    op.drop_table('quarterly_earnings')
