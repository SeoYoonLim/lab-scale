"""2026-11-04(23차)를 실제 dev DB로 확인한다: SK하이닉스 2026-11-03 국면, 결론 먼저 답변의 숫자 SQL 검산, 짧은 후속 답,
실적 분기 변환(SQL 대조). 전망 경로는 LLM을 부르면 실패한다."""

import re
from datetime import date

import pytest
from sqlalchemy import text

from app import agent, earnings, outlook
from app.models import Company, MarketIndex, StockPrice
from evals import forecast_check as FC

pytestmark = pytest.mark.integration


@pytest.fixture
def no_llm(monkeypatch):
    def boom(**kw):
        raise AssertionError("이 경로에서 LLM을 부르면 안 된다")

    monkeypatch.setattr(agent, "_llm_chat", boom)


def _company(db, name):
    return db.query(Company).filter(Company.name == name).one()


@pytest.mark.needs_collected_data
def test_skhynix_regime_on_2026_11_03_is_a_rebound_attempt(dev_db):
    """기준일을 2026-11-03으로 고정한다(뒤에 시세가 더 쌓여도 같은 값)."""
    c = _company(dev_db, "SK하이닉스")
    base = date(2026, 11, 3)
    rows = (dev_db.query(StockPrice.price_date, StockPrice.close_price, StockPrice.volume)
            .filter(StockPrice.company_id == c.id, StockPrice.price_date <= base)
            .order_by(StockPrice.price_date.desc()).limit(outlook.PRICE_ROWS).all())
    if not rows or rows[0][0] != base:
        pytest.skip("2026-11-03 시세가 없는 DB")
    rows = list(reversed(rows))
    index_rows = (dev_db.query(MarketIndex.price_date, MarketIndex.close_price)
                  .filter(MarketIndex.index_code == "KS11", MarketIndex.price_date >= rows[0][0], MarketIndex.price_date <= base)
                  .order_by(MarketIndex.price_date).all())
    p = outlook.PERIODS["1주일"]
    ind = outlook.compute_indicators(rows, p, index_rows)
    sig = outlook.classify_signals(ind, p)
    label = outlook.judge(sig, {"hold": False})
    assert ind["regime_inputs"]["r20"] == -23.24 and ind["day_change_pct"] == -4.46
    assert outlook.classify_regime(ind, label) == outlook.REGIME_REBOUND
    votes = outlook.group_votes(sig)
    assert votes["중기 추세"] == outlook.NEUTRAL  # 골든크로스와 5일선 > 20일선이 2표가 되지 않는다
    assert ind["relative"]["end_date"] == "2026-11-03"  # 지수 날짜가 맞으면 지수 대비를 낸다


@pytest.mark.needs_collected_data
def test_conclusion_first_answer_and_numbers_match_sql(dev_db, no_llm):
    r = agent.ask_question("SK하이닉스 일주일 안에 오를지 내릴지 분석해줘", save_report=False)
    a = r["answer"]
    lines = a.splitlines()
    assert lines[0].startswith("SK하이닉스(000660) · 향후 5거래일 · 방향: ") and lines[2].startswith("현재는 ")  # 25차: 제목에 방향
    assert next(x for x in lines if x.startswith("## ")) == "## 종합 판단"
    assert FC.check_numbers(dev_db, a, "SK하이닉스", "1주일") == []
    assert FC.check_groups(a) == [] and FC.check_backtest_line(a, "1주일") == []
    assert FC.assertion_hits(a) == []
    for line in lines:  # 값이 있는 최고·최저 종가 줄은 모두 조회 기간(날짜 범위)을 함께 보인다
        if re.search(r"(최고|최저) 종가 [\d,]+원", line):
            assert re.search(r"\d{4}-\d{2}-\d{2}~\d{4}-\d{2}-\d{2}|최근 \d+거래일 기준", line), line


# 25차(2026-11-04): 후속 답은 종류와 관계없이 둘째 줄 첫 문장이 방향(상승 쪽입니다/하락 쪽입니다/우세하지 않습니다/보류)이다
FIRST25 = ("상승 쪽입니다", "하락 쪽입니다", "현재는 상승과 하락 중", "데이터가 부족해")


@pytest.mark.needs_collected_data
@pytest.mark.parametrize("q,first", [("그래서 니 판단은 뭐냐고 오르냐고 내리냐고", FIRST25),
                                     ("하락 위험은 어떤데", FIRST25), ("확률은 얼마야", FIRST25)])
def test_short_followups_keep_company_and_period(dev_db, no_llm, q, first):
    r1 = agent.ask_question("하이닉스가 일주일안에 오를거 같아 내릴거 같아?", save_report=False)
    prev = {"report_id": None, "question": "x", "answer": r1["answer"], "company_name": "SK하이닉스"}
    r2 = agent.ask_question(q, save_report=False, previous=prev)
    lines = [x for x in r2["answer"].splitlines() if x.strip()]
    assert len(lines) <= 6 and lines[1].startswith(first)
    assert "규칙" not in r2["answer"] and "## " not in r2["answer"]
    assert outlook.marker_of(r2["answer"]) == {"name": "SK하이닉스", "ticker": "000660", "period": "1주일"}
    prev = {"report_id": None, "question": q, "answer": r2["answer"], "company_name": "SK하이닉스"}
    r3 = agent.ask_question("그래서 결론이 뭐야", save_report=False, previous=prev)
    assert outlook.marker_of(r3["answer"])["period"] == "1주일" and len([x for x in r3["answer"].splitlines() if x.strip()]) <= 6


@pytest.mark.needs_collected_data
def test_quarter_values_match_sql_and_q4_is_annual_minus_q3_cumulative(dev_db):
    from app.models import QuarterlyEarnings

    c = _company(dev_db, "삼성전자")
    rows = dev_db.query(QuarterlyEarnings).filter(QuarterlyEarnings.company_id == c.id).all()
    if not rows:
        pytest.skip("실적이 수집되지 않은 DB")
    series = earnings.quarterly_series(rows)
    for (year, q), v in series.items():
        if q == 4:
            want = dev_db.execute(text(
                "select a.revenue - q.revenue_cum from quarterly_earnings a join quarterly_earnings q on q.company_id = a.company_id "
                "and q.bsns_year = a.bsns_year and q.reprt_code = '11014' and q.fs_div = a.fs_div "
                "where a.company_id = :c and a.bsns_year = :y and a.reprt_code = '11011'"), {"c": c.id, "y": year}).scalar()
            if want is not None:
                assert v["revenue"] == float(want)
        else:
            code = {1: "11013", 2: "11012", 3: "11014"}[q]
            want = dev_db.execute(text("select revenue from quarterly_earnings where company_id = :c and bsns_year = :y and reprt_code = :r"),
                                  {"c": c.id, "y": year, "r": code}).scalar()
            assert v["revenue"] == float(want)
