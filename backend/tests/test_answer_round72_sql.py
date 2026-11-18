"""72차 SQL 검사: 뉴스·공시 수집 요약(app.data_status.collection_summary)과 긴 기간 근거 건수(answer_cause.count_evidence).

롤백되는 트랜잭션 안에서 가짜 기록·종목·기사·공시를 넣고 돌린다(커밋 없음). CI backend-integration(빈 pgvector DB)에서 돈다.
개발 DB(ai_investment)에는 롤백이라도 쓰지 않는다(71차 SQL 테스트와 같은 session 고정물).
"""
from datetime import date, datetime

from app import answer_cause
from app.data_status import collection_summary
from app.models import Company, DataRefreshLog, Disclosure, News
from tests.test_answer_cause_round71_sql import at, session  # noqa: F401 - 고정물 재사용
from tests.test_outlook_round23 import KST

import pytest

pytestmark = pytest.mark.integration


def log(db, step, status, finished):
    db.add(DataRefreshLog(step=step, status=status, started_at=finished, finished_at=finished))
    db.flush()


def test_collection_summary_sees_recovery_steps_and_separates_success_from_attempt(session):  # noqa: F811
    log(session, "news", "success", datetime(2026, 11, 3, 19, 54, tzinfo=KST))
    log(session, "news_recovery", "success", datetime(2026, 11, 17, 9, 34, tzinfo=KST))
    log(session, "news", "skipped", datetime(2026, 11, 17, 20, 0, tzinfo=KST))  # skipped는 시도가 아니다
    s = collection_summary(session, "news")
    assert s["last_collection_step"] == "news_recovery" and s["last_collection_status"] == "success"
    assert s["last_successful_collection_at"].startswith("2026-11-17T09:34") and s["last_attempt_finished_at"] == s["last_successful_collection_at"]
    log(session, "news", "failed", datetime(2026, 11, 18, 19, 0, tzinfo=KST))
    s = collection_summary(session, "news")
    assert s["last_collection_status"] == "failed" and s["last_attempt_finished_at"].startswith("2026-11-18T19:00")
    assert s["last_successful_collection_at"].startswith("2026-11-17T09:34")  # 실패는 성공 시각을 바꾸지 않는다
    log(session, "disclosure_recovery", "partial", datetime(2026, 11, 18, 21, 0, tzinfo=KST))
    d = collection_summary(session, "disclosure")
    assert d["last_collection_step"] == "disclosure_recovery" and d["last_collection_status"] == "partial"
    assert d["last_successful_collection_at"].startswith("2026-11-03T19:54")  # partial은 성공으로 치지 않는다(news 단계 성공)
    assert collection_summary(session, "news")["last_collection_step"] == "news"  # 공시 복구는 뉴스 요약에 없다


def test_count_evidence_counts_window_only_and_reports_after_base(session):  # noqa: F811
    frm, base = date(2026, 5, 15), date(2026, 11, 12)
    c = Company(ticker="T72A01", name="검증칠이전자", market="KOSPI", asset_type="stock")
    session.add(c)
    session.flush()
    rows = [(at(frm, 14), True), (at(frm, 16), True), (at(date(2026, 9, 1), 10), True), (at(date(2026, 9, 2), 10), False),
            (at(base, 10), True), (at(base, 16), True), (at(base, 0), True), (at(date(2026, 11, 16), 9), True)]
    for i, (when, rel) in enumerate(rows):
        session.add(News(company_id=c.id, title=f"검증칠이전자 기사 {i}", url=f"https://t72/{i}", published_at=when, is_relevant=rel))
    for d in (frm, date(2026, 7, 1), base):
        session.add(Disclosure(company_id=c.id, title="공시", disclosure_type="기타", source_url=f"https://t72/d{d}", disclosed_at=at(d, 0)))
    session.flush()
    f = answer_cause.count_evidence(session, c, frm, base)
    assert f["long"] and f["news"] == [] and f["disclosures"] == []  # 목록은 만들지 않는다
    assert f["news_count"] == 3  # 출발일 마감 뒤·9-01·기준일 마감 전(관련 없음·마감 뒤·자정·기준일 뒤 제외)
    assert (f["after_close"], f["unknown_news"], f["after_count"], f["after_first"]) == (1, 1, 1, "2026-11-16")
    assert f["disclosure_count"] == 1 and f["news_first"] == "2026-05-15"
