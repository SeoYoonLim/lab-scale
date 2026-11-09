"""quarterly_earnings에 누적 금액(thstrm_add_amount) 컬럼 추가

Revision ID: c4d2e8f61a93
Revises: b7e3d91a4c58
Create Date: 2026-11-03 21:45:00

분기·반기보고서의 손익은 그 분기(3개월) 금액과 사업연도 누적 금액이 따로 있다. 누적을 같이 저장해 "1~n분기 합 = 누적"을 검증하고
4분기(연간 - 1~3분기 누적)를 계산한다(app/earnings.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d2e8f61a93'
down_revision: Union[str, Sequence[str], None] = 'b7e3d91a4c58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = ("revenue_cum", "operating_income_cum", "net_income_cum")


def upgrade() -> None:
    """Upgrade schema."""
    for name in COLUMNS:
        op.add_column('quarterly_earnings', sa.Column(name, sa.Numeric(precision=20, scale=0), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    for name in COLUMNS:
        op.drop_column('quarterly_earnings', name)
