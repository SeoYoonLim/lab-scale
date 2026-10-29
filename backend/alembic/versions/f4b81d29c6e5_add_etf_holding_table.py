"""add etf_holding table

Revision ID: f4b81d29c6e5
Revises: e2a9c4f71b03
Create Date: 2026-10-29 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b81d29c6e5'
down_revision: Union[str, Sequence[str], None] = 'e2a9c4f71b03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    ETF 구성종목·비중(운용사 공시 PDF)의 기준일별 스냅샷. (ETF, 기준일, 종목) 유일 키는 코드가 NULL인 행도 같은 이름이면 중복으로 본다
    (NULLS NOT DISTINCT, PostgreSQL 15+). company가 지워지면 함께 지운다.
    """
    op.create_table(
        'etf_holding',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('holding_name', sa.String(length=200), nullable=False),
        sa.Column('holding_code', sa.String(length=30), nullable=True),
        sa.Column('weight_pct', sa.Numeric(precision=9, scale=4), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('collected_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'company_id', 'as_of_date', 'holding_name', 'holding_code',
            name='uq_etf_holding_etf_date_holding', postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index('idx_etf_holding_company_date', 'etf_holding', ['company_id', 'as_of_date'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_etf_holding_company_date', table_name='etf_holding')
    op.drop_table('etf_holding')
