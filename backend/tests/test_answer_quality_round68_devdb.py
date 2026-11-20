"""68차 dev DB 확인(읽기 전용, 수집 데이터 필요): KODEX 200 벡터 검색이 근거 판정 뒤에도 결과를 내는지, 원인 단락 근거가 시세 기준일을 넘지 않는지.

질문 임베딩(Ollama) 대신 DB에 이미 있는 기사 벡터를 질문 벡터로 쓴다(CI·로컬 모두 Ollama 없이 결정적으로 돈다)."""
from datetime import date

import pytest
from sqlalchemy import text

from app import answer_cause
from app.models import Company
from app.tools import rag_search_tool as rag

pytestmark = [pytest.mark.integration, pytest.mark.needs_collected_data]


def _vector_of(db, title_like: str, company: str):
    row = db.execute(text("SELECT n.embedding::text FROM news n JOIN company c ON c.id = n.company_id "
                          "WHERE c.name = :c AND n.title LIKE :t AND n.embedding IS NOT NULL ORDER BY n.id LIMIT 1"),
                     {"c": company, "t": title_like}).scalar()
    if row is None:
        pytest.skip(f"{company} '{title_like}' 기사가 없다")
    return [float(x) for x in row.strip("[]").split(",")]


def test_kodex200_flow_search_returns_subject_matched_news(dev_db, monkeypatch):
    vec = _vector_of(dev_db, "%자금%", "KODEX 200") if dev_db.execute(text(
        "SELECT 1 FROM news n JOIN company c ON c.id=n.company_id WHERE c.name='KODEX 200' AND n.title LIKE '%자금%' LIMIT 1")).first() \
        else _vector_of(dev_db, "%KODEX 200%", "KODEX 200")
    monkeypatch.setattr(rag, "_embed_query", lambda q: vec)
    r = rag.rag_search_tool("KODEX 200 최근 자금 유입", company_name="KODEX 200", limit=5)
    assert r["found"] is True, r.get("message")
    assert r["search"]["candidates"]["news"] >= rag.CANDIDATE_POOL_MIN
    news = [x for x in r["results"] if x["type"] == "news"]
    assert news and all(x["match"] in ("title", "content_lead") for x in news)


def test_cause_evidence_never_comes_after_the_price_base_date(dev_db):
    company = dev_db.query(Company).filter(Company.name == "SK하이닉스").one()
    base = dev_db.execute(text("SELECT max(price_date) FROM stock_price WHERE company_id = :c"), {"c": company.id}).scalar()
    start = dev_db.execute(text("SELECT price_date FROM stock_price WHERE company_id = :c AND price_date < :b "
                                "ORDER BY price_date DESC OFFSET 4 LIMIT 1"), {"c": company.id, "b": base}).scalar()
    found = answer_cause.collect(dev_db, company, base, start)
    assert all(start.isoformat() <= n["date"] <= base.isoformat() for n in found["news"])
    assert all(start.isoformat() <= d["date"] <= base.isoformat() for d in found["disclosures"])
    after = dev_db.execute(text("SELECT count(*) FROM news WHERE company_id = :c AND is_relevant IS NOT FALSE "
                                "AND published_at >= (CAST(:b AS date) + 1)::timestamp AT TIME ZONE 'Asia/Seoul'"),
                           {"c": company.id, "b": base}).scalar()
    assert found["after_count"] == after
    assert isinstance(base, date)
