from sqlalchemy import BigInteger, CheckConstraint, Column, Date, ForeignKey, Index, Integer, String, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class DailyBriefing(Base):
    """사용자별 일일 투자 브리핑 저장본(2026-11-06, app/daily_briefing). 한 번 저장하면 바꾸지 않는 스냅샷이다.

    같은 사용자·같은 날짜(local_date, 사용자 시간대 기준)에는 기본 1건이고, 사용자가 명시적으로 다시 만들면 version을 올린 새 행을
    추가한다(기존 행은 덮어쓰지 않는다). 과거 브리핑을 열 때 현재 시세로 다시 계산하지 않는다(content를 그대로 돌려준다).

    content: 사용자에게 보인 브리핑(locale 언어). original_content: 같은 계산 결과의 한국어 브리핑(ko-KR이면 content와 같다).
    tickers: 브리핑에 쓴 종목(보유·관심, 중복 제거). sources: 근거 뉴스·공시(제목·링크·날짜). section_status: 영역별 생성 상태.
    """

    __tablename__ = "daily_briefing"
    __table_args__ = (
        UniqueConstraint("user_id", "local_date", "version", name="uq_daily_briefing_user_date_version"),
        Index("ix_daily_briefing_user_date", "user_id", "local_date"),
        CheckConstraint("version >= 1", name="ck_daily_briefing_version"),
        CheckConstraint("locale IN ('ko-KR', 'en-US', 'ja-JP', 'zh-CN')", name="ck_daily_briefing_locale"),
    )

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    local_date = Column(Date, nullable=False)
    timezone = Column(String(64), nullable=False)
    locale = Column(String(10), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    generated_at = Column(TIMESTAMP(timezone=True), nullable=False)
    data_as_of = Column(Date)  # 브리핑에 쓴 시세 중 가장 최근 날짜(없으면 NULL)
    market_data_status = Column(String(20), nullable=False)  # latest | delayed | stale | no_data
    summary_source = Column(String(10), nullable=False)  # llm | code
    content = Column(JSONB, nullable=False)
    original_content = Column(JSONB, nullable=False)
    tickers = Column(JSONB, nullable=False)
    sources = Column(JSONB, nullable=False)
    section_status = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
