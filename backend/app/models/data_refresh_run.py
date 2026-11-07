from sqlalchemy import BigInteger, CheckConstraint, Column, Index, Integer, String, TIMESTAMP, func

from app.db.base import Base


class DataRefreshRun(Base):
    """scripts/refresh_data.py 한 번의 실행(2026-11-07). 단계 기록(data_refresh_log.run_id)을 묶는다.

    status: running(실행 중) -> success | partial | failed, 또는 aborted(프로세스가 끝나지 못하고 사라짐: 다음 실행이 락을 잡은 뒤 바꾼다).
    heartbeat_at은 단계가 끝날 때마다 갱신한다. 중복 실행 방지는 이 행이 아니라 Postgres advisory lock이 한다(app/refresh_runs.py).
    """

    __tablename__ = "data_refresh_run"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'success', 'partial', 'failed', 'aborted')", name="ck_data_refresh_run_status"),
        Index("idx_data_refresh_run_started", "started_at"),
    )

    id = Column(BigInteger, primary_key=True)
    started_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(TIMESTAMP(timezone=True))
    heartbeat_at = Column(TIMESTAMP(timezone=True))
    status = Column(String(10), nullable=False)
    trigger = Column(String(20), nullable=False, server_default="manual")  # manual | scheduler | retry
    steps = Column(String(200), nullable=False)  # 실행한 단계(쉼표)
    exit_code = Column(Integer)
    pid = Column(Integer)
    note = Column(String(200))
