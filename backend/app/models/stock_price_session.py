from sqlalchemy import (BigInteger, CheckConstraint, Column, Date, ForeignKey, Index, Integer, Numeric, SmallInteger, String, TIMESTAMP,
                        UniqueConstraint, func)

from app.db.base import Base


class StockPriceSession(Base):
    """세션별 시세(2026-11-14, 53차 설계). 표 정의는 alembic/pending/0f6d0ec52ac3_add_stock_price_session.py와 같다.

    **개발 DB에는 아직 없다(승인 대기).** 그래서 app/models/__init__.py에 등록하지 않았다(등록하면 alembic autogenerate가
    이 표를 새로 만들려 한다). 이 모델은 적재 도구(scripts/load_session_minute.py)와 시험 DB 검증만 쓴다.

    행 = 종목·거래일·거래소·세션 하나의 분봉 집계(app/session_bars.py). 행은 수정하지 않는다(DB 트리거가 UPDATE를 거부).
    가격은 원천(수집 시점 수정주가 체계, price_basis_date)의 값 그대로다. stock_price와 비교할 때는 날짜별 조정 계수가 필요하다.
    """

    __tablename__ = "stock_price_session"
    __table_args__ = (
        UniqueConstraint("company_id", "price_date", "venue", "session", name="uq_stock_price_session_key"),
        CheckConstraint("venue IN ('KRX')", name="ck_sps_venue"),
        CheckConstraint("session IN ('regular', 'closing_auction', 'post_close', 'after_market')", name="ck_sps_session"),
        Index("idx_stock_price_session_date", "price_date"),
    )

    id = Column(BigInteger, primary_key=True)
    company_id = Column(Integer, ForeignKey("company.id", ondelete="CASCADE"), nullable=False)
    price_date = Column(Date, nullable=False)  # KRX 거래일(KST)
    venue = Column(String(8), nullable=False, server_default="KRX")  # NXT를 더할 때 기존 행을 바꾸지 않도록 키에 넣었다
    session = Column(String(20), nullable=False)  # regular | closing_auction | post_close | after_market
    open_price = Column(Numeric, nullable=False)
    high_price = Column(Numeric, nullable=False)
    low_price = Column(Numeric, nullable=False)
    close_price = Column(Numeric, nullable=False)
    volume = Column(BigInteger, nullable=False)  # 세션 분봉 거래량 합계(주)
    bar_count = Column(Integer, nullable=False)
    first_bar_at = Column(TIMESTAMP(timezone=True), nullable=False)  # 첫 봉 시작 시각
    last_bar_at = Column(TIMESTAMP(timezone=True), nullable=False)  # 마지막 봉 시작 시각
    source = Column(String(24), nullable=False)  # naver_minute
    source_file_sha256 = Column(String(64), nullable=False)  # 원본 분봉 파일 SHA-256(보존본 MANIFEST와 대조)
    rules_version = Column(SmallInteger, nullable=False)  # app.session_bars.RULES_VERSION
    price_basis_date = Column(Date, nullable=False)  # 수집일(KST) = 가격의 수정주가 체계 기준일
    collected_at = Column(TIMESTAMP(timezone=True), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
