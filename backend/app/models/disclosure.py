from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, String, TIMESTAMP, Text, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class Disclosure(Base):
    __tablename__ = "disclosure"
    __table_args__ = (
        Index("idx_disclosure_company_date", "company_id", "disclosed_at"),
    )

    id = Column(BigInteger, primary_key=True)
    company_id = Column(Integer, ForeignKey("company.id", ondelete="CASCADE"), nullable=False)
    title = Column(Text, nullable=False)
    disclosure_type = Column(String(50))
    content = Column(Text)
    source_url = Column(Text)
    disclosed_at = Column(TIMESTAMP(timezone=True))
    embedding = Column(Vector(1024))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    company = relationship("Company", back_populates="disclosures")
