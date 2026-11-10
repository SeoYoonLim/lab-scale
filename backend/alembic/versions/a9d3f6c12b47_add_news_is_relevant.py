"""add news.is_relevant (관련 없는 뉴스 표시)

Revision ID: a9d3f6c12b47
Revises: d4f8a1c6b2e9
Create Date: 2026-11-01 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9d3f6c12b47'
down_revision: Union[str, Sequence[str], None] = 'd4f8a1c6b2e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """NULL = 아직 판정 안 함(기존처럼 사용), False = 그 종목 기사가 아님(근거에서 뺀다). 행은 지우지 않는다."""
    op.add_column('news', sa.Column('is_relevant', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('news', 'is_relevant')
