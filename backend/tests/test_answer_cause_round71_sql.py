"""71차 근거 수집(answer_cause.collect)의 실제 SQL 검사. 롤백되는 트랜잭션 안에서 가짜 종목·기사·공시를 넣고 돌린다(커밋 없음).

CI(backend-integration, 빈 pgvector DB)에서 돈다. 수집 데이터가 필요 없어 needs_collected_data가 아니다.
개발 DB(ai_investment)에는 롤백이라도 쓰지 않는다: 연결한 DB 이름이 ai_investment면 건너뛴다(로컬은 평가 DB로 돌린다).
"""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import answer_cause
from app.db.session import DB_URL
from app.models import Company, Disclosure, News
from tests.test_outlook_round23 import KST

pytestmark = pytest.mark.integration

FROM, BASE = date(2026, 11, 6), date(2026, 11, 12)
NAME = "검증칠일전자"


@pytest.fixture
def session():
    engine = create_engine(DB_URL, connect_args={"connect_timeout": 5})
    try:
        conn = engine.connect()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB에 연결할 수 없음: {type(e).__name__}")
    trans = conn.begin()
    if conn.execute(text("select current_database()")).scalar() == "ai_investment":
        trans.rollback()
        conn.close()
        pytest.skip("개발 DB(ai_investment)에는 쓰지 않는다(롤백 테스트도). 평가·CI DB로 돌린다")
    db = Session(bind=conn)
    try:
        yield db
    finally:
        db.close()
        trans.rollback()
        conn.close()
        engine.dispose()


def at(d, h, m=0):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def _seed(db):
    company = Company(ticker="T71A01", name=NAME, market="KOSPI", asset_type="stock")
    other = Company(ticker="T71A02", name="다른칠일회사", market="KOSPI", asset_type="stock")
    db.add_all([company, other])
    db.flush()
    news = [
        ("n_before_from_close", f"{NAME}, 신제품 공개", at(FROM, 14), True),          # 출발 종가 전 -> 이미 반영
        ("n_after_from_close", f"{NAME}, 대형 수주", at(FROM, 16), True),            # 출발 종가 뒤 -> 후보
        ("n_mid", f"{NAME} 공장 증설", at(date(2026, 11, 10), 10), True),            # 후보
        ("n_unrelated_title", "다른 회사 신제품 소식", at(date(2026, 11, 10), 11), True),  # 제목에 종목명 없음 -> 제외
        ("n_price_report", f"거래대금 1,234백만원 {NAME}, 상승 마감 전 흐름", at(date(2026, 11, 11), 9), True),  # 시세 기사
        ("n_base_before_close", f"{NAME} 신규 계약", at(BASE, 10), True),            # 후보(기준일 마감 전)
        ("n_base_after_close", f"{NAME} 장 마감 뒤 소식", at(BASE, 16), True),        # 마감 뒤 -> 건수만
        ("n_base_midnight", f"{NAME} 자정 기사", at(BASE, 0), True),                 # 시각 모름 -> 건수만
        ("n_after_base", f"{NAME} 기준일 뒤 기사", at(date(2026, 11, 13), 9), True),   # 기준일 뒤 -> 건수만
        ("n_irrelevant", f"{NAME} 관련 없음 판정", at(date(2026, 11, 10), 12), False),  # is_relevant false -> 제외
    ]
    for key, title, when, relevant in news:
        db.add(News(company_id=company.id, title=title, source="가신문", url=f"https://t71/{key}", published_at=when,
                    is_relevant=relevant))
    db.add(News(company_id=other.id, title=f"{NAME} 언급한 다른 회사 기사", source="가신문", url="https://t71/other",
                published_at=at(date(2026, 11, 10), 13), is_relevant=True))  # 다른 종목 기사 -> 제외
    for key, d in (("d_from_day", FROM), ("d_mid", date(2026, 11, 9)), ("d_base_day", BASE), ("d_after", date(2026, 11, 13))):
        db.add(Disclosure(company_id=company.id, title=f"{key} 공시", disclosure_type="기타", source_url=f"https://t71/{key}",
                          disclosed_at=at(d, 0)))
    db.flush()
    return company


def test_period_collect_uses_only_window_items_before_the_base_point(session):
    company = _seed(session)
    found = answer_cause.collect(session, company, BASE, FROM, after=answer_cause.cutoff_at(FROM), news_limit=5,
                                 disclosure_limit=5)
    titles = {n["title"] for n in found["news"]}
    assert titles == {f"{NAME}, 대형 수주", f"{NAME} 공장 증설", f"{NAME} 신규 계약"}
    assert all(n["date"] and n["source"] and n["url"] for n in found["news"])  # 근거마다 날짜·출처 보존
    assert found["price_reports"] == 1
    assert [d["title"] for d in found["disclosures"]] == ["d_mid 공시"]
    assert found["after_close"] == 1 and found["unknown_news"] == 1 and found["unknown_disclosures"] == 1
    assert found["after_count"] == 1 and found["after_first"] == "2026-11-13"


def test_round69_default_window_is_unchanged_except_price_reports(session):
    # after 없이(69차 원인 단독): 창 시작일 0시부터. 출발일 14시 기사도 후보, 출발일 공시도 후보(기존 동작). 시세 기사는 71차부터 제외
    company = _seed(session)
    found = answer_cause.collect(session, company, BASE, FROM)
    assert f"{NAME}, 신제품 공개" in {n["title"] for n in found["news"]} or len(found["news"]) == answer_cause.NEWS_LIMIT
    assert "d_from_day 공시" in [d["title"] for d in found["disclosures"]]
    assert found["price_reports"] == 1
