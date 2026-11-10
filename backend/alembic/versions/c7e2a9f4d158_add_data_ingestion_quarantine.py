"""저장 전 검증에서 걸린 수집 레코드 격리 표(data_ingestion_quarantine)

Revision ID: c7e2a9f4d158
Revises: b8d4f2a6c913
Create Date: 2026-11-08 18:00:00

- 새 표 하나만 만든다. 기존 표·행은 바꾸지 않는다(data_refresh_run·data_refresh_log는 그대로 재사용: 단계 결과는 기존
  status=partial·error_code로 남기고, 격리 건수는 이 표의 run_id·step으로 센다).
- 같은 오류 데이터의 반복 수집은 (source, source_key, error_code, payload_hash) 고유 키로 한 행에 모은다(occurrences).
- payload는 JSONB이고 크기는 앱이 제한한다(app.quarantine.MAX_PAYLOAD_BYTES). 다운그레이드는 표를 지운다(격리 기록만 사라짐).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c7e2a9f4d158'
down_revision: Union[str, Sequence[str], None] = 'b8d4f2a6c913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_ingestion_quarantine",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_key", sa.String(300), nullable=False),
        sa.Column("data_as_of", sa.Date()),
        sa.Column("validation_status", sa.String(12), nullable=False),
        sa.Column("error_code", sa.String(40), nullable=False),
        sa.Column("error_detail", sa.String(500), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_truncated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolution_status", sa.String(15), nullable=False, server_default="open"),
        sa.Column("resolution_note", sa.String(300)),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("data_refresh_run.id", ondelete="SET NULL")),
        sa.Column("step", sa.String(20)),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True)),
        sa.CheckConstraint("validation_status IN ('quarantined', 'warning')", name="ck_ingest_q_validation_status"),
        sa.CheckConstraint("resolution_status IN ('open', 'revalidated', 'reprocessed', 'kept_existing', 'on_hold', 'discarded')",
                           name="ck_ingest_q_resolution_status"),
        sa.UniqueConstraint("source", "source_key", "error_code", "payload_hash", name="uq_ingest_q_identity"),
    )
    op.create_index("idx_ingest_q_status_source", "data_ingestion_quarantine", ["resolution_status", "source"])
    op.create_index("idx_ingest_q_last_seen", "data_ingestion_quarantine", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("idx_ingest_q_last_seen", table_name="data_ingestion_quarantine")
    op.drop_index("idx_ingest_q_status_source", table_name="data_ingestion_quarantine")
    op.drop_table("data_ingestion_quarantine")
