from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, Numeric, String, TIMESTAMP, func
from sqlalchemy.orm import relationship

from app.db.base import Base

# SQL 표준 예약어(TRANSACTION)와 겹치지 않도록 테이블명은 trade로 한다.
SIDE_BUY = "buy"
SIDE_SELL = "sell"


class Trade(Base):
    """모의투자 체결 내역(매수/매도). 수정하지 않는 로그라 research_report와 같은 원칙을 따른다."""

    __tablename__ = "trade"
    __table_args__ = (Index("idx_trade_device", "device_id"),)

    id = Column(BigInteger, primary_key=True)
    device_id = Column(String(100), ForeignKey("virtual_account.device_id", ondelete="CASCADE"), nullable=False)
    company_id = Column(Integer, ForeignKey("company.id", ondelete="CASCADE"), nullable=False)
    side = Column(String(4), nullable=False)  # "buy" | "sell"
    quantity = Column(BigInteger, nullable=False)
    price = Column(Numeric(14, 2), nullable=False)
    executed_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    account = relationship("VirtualAccount", back_populates="trades")
    company = relationship("Company")
