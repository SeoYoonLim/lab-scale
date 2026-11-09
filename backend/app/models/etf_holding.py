from sqlalchemy import BigInteger, Column, Date, ForeignKey, Index, Integer, Numeric, String, TIMESTAMP, UniqueConstraint, func

from app.db.base import Base

# 수집 출처(운용사 공시 PDF). app/etf_holdings.py의 SOURCES와 같은 값.
SOURCE_KODEX = "samsung_kodex"
SOURCE_TIGER = "mirae_tiger"


class EtfHolding(Base):
    """ETF 구성종목과 비중(운용사가 공시하는 PDF, Portfolio Deposit File)의 기준일별 스냅샷.

    한 행 = (ETF, 기준일, 구성종목). 현금성 자산(예금·설정현금액)과 비중이 없는 행은 저장하지 않는다.
    holding_code는 운용사가 주는 종목 식별자 그대로다(국내 종목코드 6자리, 선물 ISIN, 해외 종목은 "NVDA US EQUITY" 같은 티커)이고
    없으면 NULL이다. 같은 (ETF, 기준일)은 수집을 다시 해도 통째로 교체되므로(멱등) 유니크 키가 중복을 막는 안전장치 역할만 한다.
    NULLS NOT DISTINCT: 코드가 NULL인 행끼리도 같은 이름이면 중복으로 본다(PostgreSQL 15+).
    """

    __tablename__ = "etf_holding"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "as_of_date", "holding_name", "holding_code",
            name="uq_etf_holding_etf_date_holding", postgresql_nulls_not_distinct=True,
        ),
        Index("idx_etf_holding_company_date", "company_id", "as_of_date"),
    )

    id = Column(BigInteger, primary_key=True)
    company_id = Column(Integer, ForeignKey("company.id", ondelete="CASCADE"), nullable=False)
    as_of_date = Column(Date, nullable=False)
    holding_name = Column(String(200), nullable=False)
    holding_code = Column(String(30))
    weight_pct = Column(Numeric(9, 4), nullable=False)
    source = Column(String(20), nullable=False)
    collected_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
