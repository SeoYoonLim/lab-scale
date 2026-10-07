from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, TIMESTAMP, Text, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class ResearchReport(Base):
    __tablename__ = "research_report"
    __table_args__ = (Index("ix_research_report_user_id", "user_id"),)

    id = Column(BigInteger, primary_key=True)
    company_id = Column(Integer, ForeignKey("company.id", ondelete="SET NULL"))
    # 후속 질문이 이어받은 바로 직전 보고서. 직전 것이 삭제돼도 후속 보고서는 남도록 SET NULL이다.
    previous_report_id = Column(BigInteger, ForeignKey("research_report.id", ondelete="SET NULL"))
    # 리포트 소유자. 로그인 도입 전에 만들어진 리포트는 NULL로 남고, 어떤 사용자의 목록/조회에도 나오지 않는다.
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    question = Column(Text, nullable=False)
    summary = Column(Text)
    content = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    company = relationship("Company", back_populates="research_reports")
    tool_call_logs = relationship("ToolCallLog", back_populates="report", cascade="all, delete-orphan")
