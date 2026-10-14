"""add company.asset_type

Revision ID: b7c4e92a1d36
Revises: a3f7c1d95b20
Create Date: 2026-10-14 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c4e92a1d36'
down_revision: Union[str, Sequence[str], None] = 'a3f7c1d95b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    company.asset_type: 'stock' | 'etf'. 기존 행은 모두 'stock'(server_default가 채운다).
    ETF도 주식처럼 company 한 행으로 다루고 이 컬럼으로만 구분한다(market/sector는 NULL).
    """
    op.add_column('company', sa.Column('asset_type', sa.String(length=10), server_default='stock', nullable=False))
    op.create_check_constraint('ck_company_asset_type', 'company', "asset_type IN ('stock', 'etf')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_company_asset_type', 'company', type_='check')
    op.drop_column('company', 'asset_type')
