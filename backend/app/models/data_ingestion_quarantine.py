from sqlalchemy import (BigInteger, CheckConstraint, Column, Date, ForeignKey, Index, Integer, String, TIMESTAMP, UniqueConstraint,
                        func)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class DataIngestionQuarantine(Base):
    """저장 전 검증(app/ingest_validation.py)에서 걸린 수집 레코드(2026-11-08, 36차). 관리는 app/quarantine.py와 scripts/quarantine.py(CLI).

    validation_status: quarantined(업무 테이블에 넣지 않음) | warning(저장했지만 경고를 남김).
    resolution_status: open(검토 전) -> revalidated(다시 검증해 통과, 재처리 대기) -> reprocessed(업무 테이블에 넣음)
                       | kept_existing(같은 키의 정상 데이터가 이미 있어 넣지 않음) | on_hold(보류) | discarded(버림).
    payload: 재검증에 필요한 최소 필드만(API 키·사용자 데이터 없음, 크기 제한 app.quarantine.MAX_PAYLOAD_BYTES).
    같은 (source, source_key, error_code, payload_hash)가 다시 오면 새 행을 만들지 않고 occurrences·last_seen_at만 올린다.
    """

    __tablename__ = "data_ingestion_quarantine"
    __table_args__ = (
        CheckConstraint("validation_status IN ('quarantined', 'warning')", name="ck_ingest_q_validation_status"),
        CheckConstraint("resolution_status IN ('open', 'revalidated', 'reprocessed', 'kept_existing', 'on_hold', 'discarded')",
                        name="ck_ingest_q_resolution_status"),
        UniqueConstraint("source", "source_key", "error_code", "payload_hash", name="uq_ingest_q_identity"),
        Index("idx_ingest_q_status_source", "resolution_status", "source"),
        Index("idx_ingest_q_last_seen", "last_seen_at"),
    )

    id = Column(BigInteger, primary_key=True)
    source = Column(String(30), nullable=False)  # stock_price | market_index | exchange_rate | news | disclosure | etf_holding
    source_key = Column(String(300), nullable=False)  # 예: stock_price:005930:2026-11-03
    data_as_of = Column(Date)
    validation_status = Column(String(12), nullable=False)
    error_code = Column(String(40), nullable=False)  # 첫 번째 사유(나머지는 error_detail)
    error_detail = Column(String(500), nullable=False)
    payload = Column(JSONB, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    payload_truncated = Column(Integer, nullable=False, server_default="0")  # 1이면 재처리 불가(다시 수집해야 함)
    occurrences = Column(Integer, nullable=False, server_default="1")
    retry_count = Column(Integer, nullable=False, server_default="0")  # 재검증 횟수
    resolution_status = Column(String(15), nullable=False, server_default="open")
    resolution_note = Column(String(300))
    run_id = Column(BigInteger, ForeignKey("data_refresh_run.id", ondelete="SET NULL"))
    step = Column(String(20))  # refresh_data 단계(직접 실행이면 null)
    first_seen_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    last_seen_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    reviewed_at = Column(TIMESTAMP(timezone=True))
    resolved_at = Column(TIMESTAMP(timezone=True))
