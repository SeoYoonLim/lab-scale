from sqlalchemy import BigInteger, CheckConstraint, Column, Date, ForeignKey, Index, Integer, String, TIMESTAMP, func

from app.db.base import Base


class DataRefreshLog(Base):
    """scripts/refresh_data.py가 단계(step)마다 한 행씩 남기는 갱신 기록. GET /api/status의 last_refresh가 읽는다.

    error_type에는 예외 클래스 이름만 저장한다(메시지에는 URL/키가 섞일 수 있어 저장하지 않는다).
    2026-11-07부터 run_id(data_refresh_run)로 한 번의 실행을 묶는다. run_id가 없는 옛 행은 "같은 step이 두 번 나오기 전까지의
    최근 행들"로 구분한다(app/data_status.py). partial = 일부 대상만 실패(실패가 절반 미만). quality_status는 단계 직후 품질 검사 결과.
    """

    __tablename__ = "data_refresh_log"
    __table_args__ = (
        CheckConstraint("status IN ('success', 'partial', 'failed', 'skipped')", name="ck_data_refresh_log_status"),
        Index("idx_data_refresh_log_step_id", "step", "id"),
    )

    id = Column(BigInteger, primary_key=True)
    step = Column(String(30), nullable=False)
    status = Column(String(10), nullable=False)
    started_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(TIMESTAMP(timezone=True))
    rows_added = Column(Integer)
    latest_date = Column(Date)
    error_type = Column(String(100))
    run_id = Column(BigInteger, ForeignKey("data_refresh_run.id", ondelete="SET NULL"))
    duration_ms = Column(Integer)
    retry_count = Column(Integer)
    quality_status = Column(String(10))  # ok | warning | error (검사하지 않았으면 None)
    warning_count = Column(Integer)
    error_code = Column(String(40))  # 실패 분류: timeout | rate_limited | auth | network | bad_data | failure_ratio | ...
