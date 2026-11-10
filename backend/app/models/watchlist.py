from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, String, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class Watchlist(Base):
    """소유자별 관심종목. device_id 컬럼에는 로그인 사용자의 소유자 키 `user:{id}`가 들어간다
    (로그인 도입 전 X-Device-Id로 만든 행은 디바이스ID 그대로 남아 있다).

    FR-12. db/schema.sql 원안에는 없던 확장이다(README "서윤님이 설계한 스키마와..." 참고).
    """

    __tablename__ = "watchlist"
    __table_args__ = (
        UniqueConstraint("device_id", "company_id", name="uq_watchlist_device_company"),
        Index("idx_watchlist_device", "device_id"),
    )

    id = Column(BigInteger, primary_key=True)
    device_id = Column(String(100), nullable=False)
    company_id = Column(Integer, ForeignKey("company.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    company = relationship("Company")
