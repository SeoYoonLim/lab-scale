from sqlalchemy import BigInteger, Column, ForeignKey, Integer, Numeric, String, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class Holding(Base):
    """디바이스ID별 보유 종목(수량, 평균 매입 단가). 전량 매도되면 row를 지운다(portfolio.py 참고)."""

    __tablename__ = "holding"
    __table_args__ = (UniqueConstraint("device_id", "company_id", name="uq_holding_device_company"),)

    id = Column(BigInteger, primary_key=True)
    device_id = Column(String(100), ForeignKey("virtual_account.device_id", ondelete="CASCADE"), nullable=False)
    company_id = Column(Integer, ForeignKey("company.id", ondelete="CASCADE"), nullable=False)
    quantity = Column(BigInteger, nullable=False)
    avg_price = Column(Numeric(14, 2), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    account = relationship("VirtualAccount", back_populates="holdings")
    company = relationship("Company")
