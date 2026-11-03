from sqlalchemy import BigInteger, Column, Date, ForeignKey, Integer, Numeric, String, TIMESTAMP, UniqueConstraint, func

from app.db.base import Base


class QuarterlyEarnings(Base):
    """DART 단일회사 주요계정(fnlttSinglAcnt)의 손익 세 항목(2026-11-03, app/earnings.py).

    보고서 하나(사업연도 + 보고서 코드)당 한 행. 분기·반기보고서의 금액은 그 분기(3개월) 금액(thstrm_amount), 사업보고서는 연간 금액이다.
    *_prev는 같은 보고서에 적힌 전기 금액(frmtrm_amount, 전년 동기)이다. 연결(CFS)이 있으면 연결, 없으면 별도(OFS). 금액 단위는 원.
    """

    __tablename__ = "quarterly_earnings"
    __table_args__ = (UniqueConstraint("company_id", "bsns_year", "reprt_code", name="uq_quarterly_earnings_report"),)

    id = Column(BigInteger, primary_key=True)
    company_id = Column(Integer, ForeignKey("company.id", ondelete="CASCADE"), nullable=False, index=True)
    bsns_year = Column(Integer, nullable=False)
    reprt_code = Column(String(5), nullable=False)  # 11013 1분기, 11012 반기, 11014 3분기, 11011 사업보고서
    fs_div = Column(String(3), nullable=False)  # CFS 연결, OFS 별도
    revenue = Column(Numeric(20, 0))
    operating_income = Column(Numeric(20, 0))
    net_income = Column(Numeric(20, 0))
    revenue_prev = Column(Numeric(20, 0))
    operating_income_prev = Column(Numeric(20, 0))
    net_income_prev = Column(Numeric(20, 0))
    rcept_no = Column(String(14), nullable=False)
    disclosed_date = Column(Date, nullable=False)  # 접수번호 앞 8자리
    source = Column(String(30), nullable=False, server_default="dart_fnlttSinglAcnt")
    collected_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
