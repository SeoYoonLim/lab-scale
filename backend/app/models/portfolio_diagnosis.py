from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, Numeric, String, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class PortfolioDiagnosis(Base):
    """POST /api/portfolio/diagnosis 결과의 저장본(사용자당 최근 MAX_KEPT_PER_USER건만 보관).

    result에는 그 응답 본문 전체를 그대로 넣는다(diagnosis_id/created_at 제외). source, total_asset, holdings_count는
    목록/정렬용으로 result에서 꺼내 둔 복사본이다. 사용자가 삭제되면 함께 지워진다(CASCADE).
    """

    __tablename__ = "portfolio_diagnosis"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    source = Column(String(20), nullable=False)  # "llm" 또는 "rule_based"
    total_asset = Column(Numeric, nullable=False)
    holdings_count = Column(Integer, nullable=False)
    result = Column(JSONB, nullable=False)


Index(
    "ix_portfolio_diagnosis_user_id_created_at",
    PortfolioDiagnosis.user_id,
    PortfolioDiagnosis.created_at.desc(),
)
