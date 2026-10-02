from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, String, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class Watchlist(Base):
    """디바이스ID(로그인 없이 프론트가 만들어 localStorage에 저장하는 식별자)별 관심종목.

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
