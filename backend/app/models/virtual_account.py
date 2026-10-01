from sqlalchemy import Column, Numeric, String, TIMESTAMP, func
from sqlalchemy.orm import relationship

from app.db.base import Base

# 모의투자 초기 잔고(1,000만원). 디바이스ID로 첫 요청이 오면 이 값으로 계좌가 자동 생성된다.
INITIAL_BALANCE = 10_000_000


class VirtualAccount(Base):
    """디바이스ID당 1개인 모의투자 가상 계좌. 로그인이 없어 device_id 자체를 기본키로 쓴다.

    설계에 없던 확장이다(README "서윤님이 설계한 스키마와..." 참고).
    """

    __tablename__ = "virtual_account"

    device_id = Column(String(100), primary_key=True)
    cash_balance = Column(Numeric(14, 2), nullable=False, default=INITIAL_BALANCE)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    holdings = relationship("Holding", back_populates="account", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="account", cascade="all, delete-orphan")
