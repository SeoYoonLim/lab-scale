from sqlalchemy import BigInteger, Column, Date, Numeric, String, TIMESTAMP, UniqueConstraint, func

from app.db.base import Base


# 수집·비교 대상 지수. company.market이 KOSPI면 KS11, KOSDAQ/KOSDAQ GLOBAL이면 KQ11과 비교한다.
MARKET_INDEXES = {"KS11": "코스피", "KQ11": "코스닥"}


class MarketIndex(Base):
    """시장 전체 지수(코스피 KS11, 코스닥 KQ11)의 일별 종가. 종목 주가와 같은 기간으로 등락률을 비교하는 용도."""

    __tablename__ = "market_index"
    __table_args__ = (UniqueConstraint("index_code", "price_date", name="uq_market_index_code_date"),)

    id = Column(BigInteger, primary_key=True)
    index_code = Column(String(10), nullable=False)
    price_date = Column(Date, nullable=False)
    close_price = Column(Numeric(12, 2), nullable=False)
    change_pct = Column(Numeric(6, 2))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
