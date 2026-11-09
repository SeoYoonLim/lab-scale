"""사용자별 일일 투자 브리핑 저장본(daily_briefing)

Revision ID: 5e7b9d2c4a10
Revises: 14a53dc73b9c
Create Date: 2026-11-06 14:00:00

- 같은 사용자·같은 날짜(사용자 시간대의 local_date)에는 version 1부터, 명시적으로 다시 만들면 version을 올린 새 행(덮어쓰지 않는다).
- content(사용자 언어)·original_content(한국어)는 생성 당시의 스냅샷이고, 열 때 다시 계산하지 않는다.
- 사용자를 지우면 브리핑도 지운다(ON DELETE CASCADE). 기존 테이블은 건드리지 않는다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '5e7b9d2c4a10'
down_revision: Union[str, Sequence[str], None] = '14a53dc73b9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_briefing",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("locale", sa.String(10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("data_as_of", sa.Date()),
        sa.Column("market_data_status", sa.String(20), nullable=False),
        sa.Column("summary_source", sa.String(10), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("original_content", postgresql.JSONB(), nullable=False),
        sa.Column("tickers", postgresql.JSONB(), nullable=False),
        sa.Column("sources", postgresql.JSONB(), nullable=False),
        sa.Column("section_status", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "local_date", "version", name="uq_daily_briefing_user_date_version"),
        sa.CheckConstraint("version >= 1", name="ck_daily_briefing_version"),
        sa.CheckConstraint("locale IN ('ko-KR', 'en-US', 'ja-JP', 'zh-CN')", name="ck_daily_briefing_locale"),
    )
    op.create_index("ix_daily_briefing_user_date", "daily_briefing", ["user_id", "local_date"])


def downgrade() -> None:
    op.drop_index("ix_daily_briefing_user_date", table_name="daily_briefing")
    op.drop_table("daily_briefing")
