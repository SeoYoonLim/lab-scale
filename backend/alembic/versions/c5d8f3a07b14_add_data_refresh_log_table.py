"""add data_refresh_log table

Revision ID: c5d8f3a07b14
Revises: b7c4e92a1d36
Create Date: 2026-10-14 17:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d8f3a07b14'
down_revision: Union[str, Sequence[str], None] = 'b7c4e92a1d36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    scripts/refresh_data.py가 단계마다 한 행씩 남기는 갱신 기록. error_type은 예외 클래스 이름만(메시지 저장 금지).
    """
    op.create_table(
        'data_refresh_log',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('step', sa.String(length=30), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('rows_added', sa.Integer(), nullable=True),
        sa.Column('latest_date', sa.Date(), nullable=True),
        sa.Column('error_type', sa.String(length=100), nullable=True),
        sa.CheckConstraint("status IN ('success', 'failed', 'skipped')", name='ck_data_refresh_log_status'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('data_refresh_log')
