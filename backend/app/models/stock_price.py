from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    TIMESTAMP,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class StockPrice(Base):
    __tablename__ = "stock_price"
    __table_args__ = (
        UniqueConstraint("company_id", "price_date", name="uq_stock_price_company_date"),
        Index("idx_stock_price_company_date", "company_id", "price_date"),
    )

    id = Column(BigInteger, primary_key=True)
    company_id = Column(Integer, ForeignKey("company.id", ondelete="CASCADE"), nullable=False)
    price_date = Column(Date, nullable=False)
    open_price = Column(Numeric(12, 2))
    high_price = Column(Numeric(12, 2))
    low_price = Column(Numeric(12, 2))
    close_price = Column(Numeric(12, 2), nullable=False)
    volume = Column(BigInteger)
    change_pct = Column(Numeric(6, 2))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    company = relationship("Company", back_populates="stock_prices")
