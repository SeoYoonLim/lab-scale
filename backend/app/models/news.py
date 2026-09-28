from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, String, TIMESTAMP, Text, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class News(Base):
    __tablename__ = "news"
    __table_args__ = (
        Index("idx_news_company_published", "company_id", "published_at"),
        Index(
            "idx_news_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id = Column(BigInteger, primary_key=True)
    company_id = Column(Integer, ForeignKey("company.id", ondelete="SET NULL"))
    title = Column(Text, nullable=False)
    content = Column(Text)
    source = Column(String(100))
    url = Column(Text)
    published_at = Column(TIMESTAMP(timezone=True))
    embedding = Column(Vector(1024))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    company = relationship("Company", back_populates="news")
