"""2026-11-03 2차를 실제 dev DB로 확인한다: 줄임말·띄어쓰기 없는 이름 식별, 업종·기초지수 간접 뉴스 분리, 실적 SQL 검산,
주문 문장 경로(LLM 없음), 묶음 판정. 전망·주문 경로는 LLM을 부르면 실패한다."""

import pytest

from app import agent, earnings, outlook, related_news
from app.news_relevance import name_forms
from app.tools.company_resolver import extract_from_text, resolve_company
from evals import forecast_check as FC


@pytest.fixture
def no_llm(monkeypatch):
    def boom(**kw):
        raise AssertionError("이 경로에서 LLM을 부르면 안 된다")

    monkeypatch.setattr(agent, "_llm_chat", boom)


@pytest.mark.integration
@pytest.mark.parametrize("q,name", [("SK하닉 다음 주 전망", "SK하이닉스"), ("엘지엔솔 한 달 안에 오를까", "LG에너지솔루션"),
                                    ("삼바 주가 앞으로 어떻게 될까", "삼성바이오로직스"), ("현차 지금 사도 될까", "현대차"),
                                    ("TIGER미국나스닥100 전망 알려줘", "TIGER 미국나스닥100"), ("KODEX레버리지 이번 주 오를까", "KODEX 레버리지"),
                                    ("ＳＫ하이닉스 전망", "SK하이닉스")])
def test_abbreviations_and_unspaced_names_resolve_to_one_company(dev_db, q, name):
    assert [c.name for c in extract_from_text(dev_db, q)] == [name]


@pytest.mark.integration
def test_new_aliases_never_pick_another_company_in_strict_mode(dev_db):
    for alias, name in (("SK하닉", "SK하이닉스"), ("삼바", "삼성바이오로직스"), ("엘지엔솔", "LG에너지솔루션"), ("현차", "현대차")):
        assert resolve_company(dev_db, alias, allow_fuzzy=False).company.name == name


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_sector_map_comes_from_sector_etf_holdings(dev_db):
    m = related_news.sector_map(dev_db)
    from app.models import Company

    ticker = {n: t for n, t in dev_db.query(Company.name, Company.ticker)}
    if not m:
        pytest.skip("업종 ETF 구성종목이 수집되지 않은 DB")
    assert m.get(ticker["SK하이닉스"]) == "반도체" and m.get(ticker["삼성SDI"]) == "2차전지"
    assert ticker["현대차"] not in m  # 업종 ETF에 없는 종목은 미분류


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_forecast_keeps_direct_and_indirect_news_apart(dev_db, no_llm):
    r = agent.ask_question("SK하이닉스 한 달 전망 분석해줘", save_report=False)
    assert FC.check_indirect(r["answer"], "업종", "SK하이닉스", name_forms("SK하이닉스")) == []
    assert all(s["company_names"] == ["SK하이닉스"] for s in r["sources"])  # 근거(sources)는 직접 관련만
    assert FC.check_groups(r["answer"]) == []
    assert FC.check_backtest_line(r["answer"], "1개월") == []


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_index_etf_shows_index_news_separately(dev_db, no_llm):
    r = agent.ask_question("TIGER 미국나스닥100 한 달 전망 자세히 분석해줘", save_report=False)  # 26차: 간접 뉴스는 상세 리포트
    assert "## 기초지수 관련 뉴스(간접)" in r["answer"]
    assert FC.check_indirect(r["answer"], "기초지수", "TIGER 미국나스닥100", name_forms("TIGER 미국나스닥100")) == []


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_earnings_section_matches_sql(dev_db, no_llm):
    from app.models import Company

    company = dev_db.query(Company).filter(Company.name == "삼성전자").one()
    if not earnings.recent_for_answer(dev_db, company):
        pytest.skip("실적이 수집되지 않은 DB")
    r = agent.ask_question("삼성전자 3개월 뒤 전망 분석해줘", save_report=False)
    assert FC.check_earnings(dev_db, r["answer"], "삼성전자") == []
    assert "신호 판정에는 쓰지 않는 참고 정보" in r["answer"]


@pytest.mark.integration
def test_order_sentence_gets_the_fixed_guide_without_llm(dev_db, no_llm):
    r = agent.ask_question("카카오 20주 매수해줘", save_report=False)
    assert r["answer"].startswith(outlook.ORDER_GUIDE_ANSWER)
    for bad in ("주문이 완료", "주문이 체결", "매수했습니다", "체결되었습니다", "최종 신호 판단"):
        assert bad not in r["answer"]


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_order_forecast_and_quote_take_different_paths(dev_db, no_llm, monkeypatch):
    seen = []
    monkeypatch.setattr(agent, "_answer_forecast", lambda q, p, _orig=agent._answer_forecast: seen.append(("forecast", q)) or _orig(q, p))
    agent.ask_question("삼성전자 사도 될까?", save_report=False)
    order = agent.ask_question("삼성전자 10주 사줘", save_report=False)
    assert order["answer"].startswith(outlook.ORDER_GUIDE_ANSWER)
    assert seen == [("forecast", "삼성전자 사도 될까?")]
