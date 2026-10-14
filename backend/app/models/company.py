from sqlalchemy import CheckConstraint, Column, Integer, String, TIMESTAMP, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class Company(Base):
    __tablename__ = "company"
    __table_args__ = (CheckConstraint("asset_type IN ('stock', 'etf')", name="ck_company_asset_type"),)

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    market = Column(String(20))
    sector = Column(String(50))
    # 'stock'(주식) | 'etf'. ETF도 주식처럼 company 한 행으로 다루고, 이 값으로만 구분한다. ETF는 market/sector가 NULL이다.
    asset_type = Column(String(10), nullable=False, server_default="stock")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    stock_prices = relationship("StockPrice", back_populates="company", cascade="all, delete-orphan")
    news = relationship("News", back_populates="company")
    disclosures = relationship("Disclosure", back_populates="company", cascade="all, delete-orphan")
    research_reports = relationship("ResearchReport", back_populates="company")
