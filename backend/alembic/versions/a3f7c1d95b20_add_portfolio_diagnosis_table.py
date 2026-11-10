"""add portfolio_diagnosis table

Revision ID: a3f7c1d95b20
Revises: 80ab1e64c443
Create Date: 2026-10-14 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a3f7c1d95b20'
down_revision: Union[str, Sequence[str], None] = '80ab1e64c443'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    portfolio_diagnosis: 포트폴리오 AI 진단 결과 저장본(사용자당 최근 50건). 사용자가 지워지면 함께 지워진다.
    """
    op.create_table(
        'portfolio_diagnosis',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('total_asset', sa.Numeric(), nullable=False),
        sa.Column('holdings_count', sa.Integer(), nullable=False),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_portfolio_diagnosis_user_id_created_at',
        'portfolio_diagnosis',
        ['user_id', sa.text('created_at DESC')],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_portfolio_diagnosis_user_id_created_at', table_name='portfolio_diagnosis')
    op.drop_table('portfolio_diagnosis')
