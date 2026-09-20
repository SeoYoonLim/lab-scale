from sqlalchemy import Column, Integer, String, TIMESTAMP, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class Company(Base):
    __tablename__ = "company"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    market = Column(String(20))
    sector = Column(String(50))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    stock_prices = relationship("StockPrice", back_populates="company", cascade="all, delete-orphan")
    news = relationship("News", back_populates="company")
    disclosures = relationship("Disclosure", back_populates="company", cascade="all, delete-orphan")
    research_reports = relationship("ResearchReport", back_populates="company")
