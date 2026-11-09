"""데이터 갱신 실행 기록(data_refresh_run)과 단계 기록(data_refresh_log) 모니터링 열

Revision ID: b8d4f2a6c913
Revises: 5e7b9d2c4a10
Create Date: 2026-11-07 15:00:00

- data_refresh_run: scripts/refresh_data.py 한 번의 실행(running -> success | partial | failed | aborted). 중단된 실행은 다음 실행이
  락을 잡은 뒤 aborted로 바꾼다(app/refresh_runs.py).
- data_refresh_log: run_id(실행), 부분 성공(partial) 상태, 소요 시간·재시도 수·품질 상태·경고 수·오류 코드. 모두 null 허용이라
  기존 38행은 그대로이고 기존 읽기(GET /api/status)는 바뀌지 않는다.
- 다운그레이드: partial 행은 이전 의미(일부 실패는 경고만 남긴 success)로 되돌린 뒤 열과 표를 지운다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8d4f2a6c913'
down_revision: Union[str, Sequence[str], None] = '5e7b9d2c4a10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUN_STATUSES = ("running", "success", "partial", "failed", "aborted")


def upgrade() -> None:
    op.create_table(
        "data_refresh_run",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("steps", sa.String(200), nullable=False),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("pid", sa.Integer()),
        sa.Column("note", sa.String(200)),
        sa.CheckConstraint("status IN ('running', 'success', 'partial', 'failed', 'aborted')", name="ck_data_refresh_run_status"),
    )
    op.create_index("idx_data_refresh_run_started", "data_refresh_run", ["started_at"])

    op.add_column("data_refresh_log", sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("data_refresh_run.id", ondelete="SET NULL")))
    op.add_column("data_refresh_log", sa.Column("duration_ms", sa.Integer()))
    op.add_column("data_refresh_log", sa.Column("retry_count", sa.Integer()))
    op.add_column("data_refresh_log", sa.Column("quality_status", sa.String(10)))
    op.add_column("data_refresh_log", sa.Column("warning_count", sa.Integer()))
    op.add_column("data_refresh_log", sa.Column("error_code", sa.String(40)))
    op.create_index("idx_data_refresh_log_step_id", "data_refresh_log", ["step", "id"])
    op.drop_constraint("ck_data_refresh_log_status", "data_refresh_log", type_="check")
    op.create_check_constraint("ck_data_refresh_log_status", "data_refresh_log", "status IN ('success', 'partial', 'failed', 'skipped')")


def downgrade() -> None:
    op.execute("UPDATE data_refresh_log SET status = 'success' WHERE status = 'partial'")
    op.drop_constraint("ck_data_refresh_log_status", "data_refresh_log", type_="check")
    op.create_check_constraint("ck_data_refresh_log_status", "data_refresh_log", "status IN ('success', 'failed', 'skipped')")
    op.drop_index("idx_data_refresh_log_step_id", table_name="data_refresh_log")
    for col in ("error_code", "warning_count", "quality_status", "retry_count", "duration_ms", "run_id"):
        op.drop_column("data_refresh_log", col)
    op.drop_index("idx_data_refresh_run_started", table_name="data_refresh_run")
    op.drop_table("data_refresh_run")
