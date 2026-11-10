from sqlalchemy import BigInteger, Column, ForeignKey, Index, String, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class ToolCallLog(Base):
    __tablename__ = "tool_call_log"
    __table_args__ = (
        Index("idx_tool_call_log_report", "report_id"),
    )

    id = Column(BigInteger, primary_key=True)
    report_id = Column(BigInteger, ForeignKey("research_report.id", ondelete="CASCADE"), nullable=False)
    tool_name = Column(String(50), nullable=False)
    arguments = Column(JSONB)
    result = Column(JSONB)
    called_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    report = relationship("ResearchReport", back_populates="tool_call_logs")
