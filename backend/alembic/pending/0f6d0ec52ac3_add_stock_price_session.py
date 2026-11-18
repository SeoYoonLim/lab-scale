"""세션별 시세 표(stock_price_session) — 승인 전 대기 마이그레이션(53차)

Revision ID: 0f6d0ec52ac3
Revises: c7e2a9f4d158
Create Date: 2026-11-14 15:00:00

**alembic/pending/에 있는 동안은 일반 `alembic upgrade head`의 대상이 아니다.** 시험 DB에서만
`python -m scripts.alembic_pending ...`(versions + pending)으로 적용한다. 개발 DB 적용 승인 뒤 이 파일을
alembic/versions/로 옮긴다(docs/SESSION_MARKET_DATA_IMPLEMENTATION_REHEARSAL_53.md 17절).

- 새 표 하나와 수정 금지 트리거만 만든다. stock_price 등 기존 표·행은 바꾸지 않는다.
- 행 = 종목·거래일·거래소·세션 하나의 분봉 집계(app/session_bars.py 규칙). 장중 행은 CHECK로 막는다(수집 시각 >= 그날 20:10 KST).
- 가격은 NUMERIC(자릿수 제한 없음): 수정주가 ETF 분봉의 소수 12자리를 원본 그대로 보존한다.
- 행은 수정하지 않는다(UPDATE 트리거가 거부). 같은 키 재적재는 적재 도구가 값 비교로 처리한다(ON CONFLICT DO UPDATE 없음).
- 다운그레이드는 표를 지운다. **적재한 세션 데이터가 모두 사라진다**(원본 분봉 파일에서 다시 만들 수 있다).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0f6d0ec52ac3'
down_revision: Union[str, Sequence[str], None] = 'c7e2a9f4d158'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SESSIONS = ("regular", "closing_auction", "post_close", "after_market")
VENUES = ("KRX",)


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "stock_price_session",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("venue", sa.String(8), nullable=False, server_default="KRX"),
        sa.Column("session", sa.String(20), nullable=False),
        sa.Column("open_price", sa.Numeric(), nullable=False),
        sa.Column("high_price", sa.Numeric(), nullable=False),
        sa.Column("low_price", sa.Numeric(), nullable=False),
        sa.Column("close_price", sa.Numeric(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.Column("first_bar_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_bar_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("source_file_sha256", sa.String(64), nullable=False),
        sa.Column("rules_version", sa.SmallInteger(), nullable=False),
        sa.Column("price_basis_date", sa.Date(), nullable=False),
        sa.Column("collected_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "price_date", "venue", "session", name="uq_stock_price_session_key"),
        sa.CheckConstraint(f"venue IN ({_in(VENUES)})", name="ck_sps_venue"),
        sa.CheckConstraint(f"session IN ({_in(SESSIONS)})", name="ck_sps_session"),
        sa.CheckConstraint("low_price > 0 AND low_price <= open_price AND low_price <= close_price "
                           "AND high_price >= open_price AND high_price >= close_price", name="ck_sps_ohlc"),
        sa.CheckConstraint("volume >= 0", name="ck_sps_volume"),
        sa.CheckConstraint("bar_count > 0 AND first_bar_at <= last_bar_at", name="ck_sps_bars"),
        sa.CheckConstraint("session NOT IN ('closing_auction', 'post_close') OR "
                           "(open_price = high_price AND high_price = low_price AND low_price = close_price)",
                           name="ck_sps_single_price"),
        sa.CheckConstraint("session <> 'closing_auction' OR bar_count = 1", name="ck_sps_auction_one_bar"),
        sa.CheckConstraint("(first_bar_at AT TIME ZONE 'Asia/Seoul')::date = price_date "
                           "AND (last_bar_at AT TIME ZONE 'Asia/Seoul')::date = price_date", name="ck_sps_bar_date"),
        sa.CheckConstraint("collected_at >= ((price_date + time '20:10') AT TIME ZONE 'Asia/Seoul')", name="ck_sps_complete_day"),
        sa.CheckConstraint("price_basis_date >= price_date", name="ck_sps_basis_date"),
        sa.CheckConstraint("source_file_sha256 ~ '^[0-9a-f]{64}$'", name="ck_sps_sha256"),
    )
    op.create_index("idx_stock_price_session_date", "stock_price_session", ["price_date"])
    op.execute("""
        CREATE FUNCTION stock_price_session_no_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'stock_price_session rows are immutable (id=%)', OLD.id USING ERRCODE = 'check_violation';
        END $$;
    """)
    op.execute("CREATE TRIGGER trg_stock_price_session_no_update BEFORE UPDATE ON stock_price_session "
               "FOR EACH ROW EXECUTE FUNCTION stock_price_session_no_update()")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_stock_price_session_no_update ON stock_price_session")
    op.execute("DROP FUNCTION IF EXISTS stock_price_session_no_update()")
    op.drop_index("idx_stock_price_session_date", table_name="stock_price_session")
    op.drop_table("stock_price_session")
