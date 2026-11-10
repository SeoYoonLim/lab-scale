from sqlalchemy import BigInteger, Column, Date, Numeric, String, TIMESTAMP, UniqueConstraint, func

from app.db.base import Base

# 수집 대상 통화쌍. 금리는 쓸 만한 무료 데이터 소스를 찾지 못해 범위 밖이고(README FR-07 참고) 환율만 다룬다.
# market_index(MARKET_INDEXES)처럼 dict로 둬서, 통화쌍이 늘어나도 테이블/수집 로직은 그대로 쓸 수 있게 했다.
EXCHANGE_RATES = {"USD/KRW": "달러/원"}


class ExchangeRate(Base):
    """환율(지금은 USD/KRW만) 일별 종가. FR-07(경제지표 영향 분석, 1차 범위는 환율만) 대상.

    market_index와 같은 구조(코드 + 날짜 + 종가 + 등락률)다. db/schema.sql 원안에는 없던 확장이다
    (README "서윤님이 설계한 스키마와..." 참고).
    """

    __tablename__ = "exchange_rate"
    __table_args__ = (UniqueConstraint("pair_code", "price_date", name="uq_exchange_rate_pair_date"),)

    id = Column(BigInteger, primary_key=True)
    pair_code = Column(String(10), nullable=False)
    price_date = Column(Date, nullable=False)
    close_price = Column(Numeric(12, 2), nullable=False)
    change_pct = Column(Numeric(6, 2))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
