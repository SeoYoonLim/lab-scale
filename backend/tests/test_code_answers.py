"""(B) 코드 템플릿 답변(app/code_answers.py)과 에이전트 연결. DB·Ollama 없이 돈다(시세 행은 가짜)."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import agent, code_answers as C
from app.briefing import KST

AFTER_CLOSE = datetime(2026, 10, 28, 16, 0, tzinfo=KST)  # 수요일 장 마감 뒤
DURING = datetime(2026, 10, 28, 10, 0, tzinfo=KST)  # 수요일 장중
SAMSUNG = SimpleNamespace(id=1, ticker="005930", name="삼성전자", asset_type="stock")
HYNIX = SimpleNamespace(id=2, ticker="000660", name="SK하이닉스", asset_type="stock")


def rows(closes, last=date(2026, 10, 28)):
    """최신순 가짜 시세 행. closes는 오래된 순, 날짜는 평일만 거꾸로 센다."""
    out, d = [], last
    for c in reversed(closes):
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        out.append(SimpleNamespace(price_date=d, close_price=c, volume=1000, change_pct=None))
        d -= timedelta(days=1)
    return out


class TestClassify:
    @pytest.mark.parametrize("q,n,kind", [
        ("삼성전자 3개월 수익률 알려줘", 1, C.KIND_RETURN),
        ("삼성전자 최근 한 달 동안 얼마나 올랐어?", 1, C.KIND_RETURN),
        ("SK하이닉스 최근 한 달 동안 최고가와 최저가는 언제였어?", 1, C.KIND_HIGH_LOW),
        ("현대차 최근 3개월 중에 가장 비쌌던 날은?", 1, C.KIND_HIGH_LOW),
        ("삼성전자와 SK하이닉스 중 최근 한 달 동안 어디가 더 올랐어?", 2, C.KIND_COMPARE),
        ("카카오랑 NAVER 최근 일주일 등락률 비교해줘", 2, C.KIND_COMPARE),
        ("KB금융과 HMM의 최근 3개월 수익률을 각각 알려줘", 2, C.KIND_COMPARE),
    ])
    def test_kinds(self, q, n, kind):
        assert C.classify(q, n, True) == kind

    @pytest.mark.parametrize("q,n,has_period", [
        ("삼성전자 수익률 알려줘", 1, False),  # 기간 없음
        ("삼성전자 3개월 동안 왜 떨어졌어?", 1, True),  # 원인 질문
        ("삼성전자 최근 한 달 흐름 어때?", 1, True),  # 흐름 질문(서술)
        ("삼성전자 3개월 수익률이랑 뉴스 알려줘", 1, True),  # 다른 정보
        ("삼성전자 한 달 수익률 코스피랑 비교해줘", 1, True),  # 시장 비교(market_tool)
        ("LG화학과 삼성SDI의 최근 일주일 거래량을 비교해줘", 2, True),  # 거래량
        ("삼성전자 SK하이닉스 현대차 한 달 수익률", 3, True),  # 종목 3개
        ("삼성전자 최근 한 달 주가 알려줘", 1, True),  # 수익률·최고가가 아닌 기간 시세
        ("삼성전자 3개월 수익률로 볼 때 지금 사도 돼?", 1, True),
    ])
    def test_ambiguous_or_other_questions_stay_on_the_llm_path(self, q, n, has_period):
        assert C.classify(q, n, has_period) is None


class TestWindow:
    def test_date_window_with_base_close_before_start(self):
        r = C.window_result(SAMSUNG, rows([100.0, 110.0, 105.0, 120.0]), date(2026, 10, 26), AFTER_CLOSE, "t")
        # 10-26(월)부터: 110(10-26)·105(10-27)·120(10-28), 기준은 직전 거래일 10-23의 100
        assert [p["close_price"] for p in r["prices"]] == [110.0, 105.0, 120.0]
        assert (r["period_start_date"], r["period_change_pct"], r["trading_days"]) == ("2026-10-23", 20.0, 3)
        assert r["summary"].startswith("삼성전자의 2026-10-28 종가는 120원으로, 2026-10-23 종가 100원 대비 3거래일간 +20.00%")

    def test_open_session_drops_todays_row(self):
        r = C.window_result(SAMSUNG, rows([100.0, 110.0, 105.0, 120.0]), date(2026, 10, 26), DURING, "t")
        assert r["period_end_date"] == "2026-10-27" and r["period_change_pct"] == 5.0  # 105 / 100

    def test_no_base_close_is_none(self):
        assert C.window_result(SAMSUNG, rows([100.0, 110.0]), date(2026, 10, 1), AFTER_CLOSE, "t") is None

    def test_latest_of_drops_intraday_row(self):
        result = {"found": True, "company_name": "삼성전자", "summary": "x", "period_change_pct": 1.0,
                  "prices": [{"price_date": "2026-10-27", "close_price": 225000.0, "volume": 10, "change_pct": 5.39},
                             {"price_date": "2026-10-28", "close_price": 238000.0, "volume": 3, "change_pct": 5.78}]}
        during = C.latest_of(result, DURING)
        assert during["prices"] == [result["prices"][0]] and during["change_pct"] == 5.39 and "summary" not in during
        assert C.latest_of(result, AFTER_CLOSE)["change_pct"] == 5.78
        assert C.latest_of({"found": False}, DURING) == {"found": False}


class TestCompare:
    def results(self, b_closes):
        a = C.window_result(SAMSUNG, rows([100.0, 110.0, 120.0]), date(2026, 10, 27), AFTER_CLOSE, "t")
        b = C.window_result(HYNIX, rows(b_closes), date(2026, 10, 27), AFTER_CLOSE, "t")
        return a, b

    def test_different_dates_fall_back(self):
        a, _ = self.results([200.0, 210.0, 205.0])
        b = C.window_result(HYNIX, rows([200.0, 210.0, 205.0], last=date(2026, 10, 27)), date(2026, 10, 26), AFTER_CLOSE, "t")
        assert C.compare(a, b) is None


def test_compare_numbers_are_computed_from_both_windows():
    a = C.window_result(SAMSUNG, rows([100.0, 110.0, 120.0]), date(2026, 10, 27), AFTER_CLOSE, "t")
    b = C.window_result(HYNIX, rows([200.0, 210.0, 205.0]), date(2026, 10, 27), AFTER_CLOSE, "t")
    # 창 10-27~10-28, 기준은 직전 거래일 10-26 종가: 삼성 100 -> 120 = +20.00%, 하이닉스 200 -> 205 = +2.50%, 차이 17.50%p
    assert (a["period_change_pct"], b["period_change_pct"]) == (20.0, 2.5)
    text, diff = C.compare(a, b)
    assert diff == 17.5 and text.endswith("삼성전자가 SK하이닉스보다 17.50%p 높았습니다.")
    assert text.startswith("2026-10-26 종가 대비 2026-10-28까지 2거래일 기준 비교입니다")


class TestAgentPath:
    @pytest.fixture
    def fake(self, monkeypatch):
        data = {"삼성전자": rows([100.0, 110.0, 120.0]), "SK하이닉스": rows([200.0, 210.0, 205.0])}
        monkeypatch.setattr(agent, "_load_price_rows", lambda company, limit: data[company.name])
        monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: AFTER_CLOSE)}))
        monkeypatch.setattr(agent, "parse_period", lambda q, now=None: SimpleNamespace(
            start_date=date(2026, 10, 27), end_date=date(2026, 10, 28), period_days=2, matched_text="이틀"))

        def no_llm(**kw):
            raise AssertionError("LLM을 부르면 안 된다")

        monkeypatch.setattr(agent, "_llm_chat", no_llm)

    def test_period_return_is_code_answered(self, fake):
        resp, records = agent._answer_period_quote("삼성전자 이틀 수익률 알려줘", [SAMSUNG])
        assert "+20.00%" in resp["answer"] and records[0]["result"]["called_via"] == "period_quote"
        assert records[0]["arguments"] == {"ticker": "삼성전자", "period_days": 2}

    def test_comparison_records_both_companies_and_the_difference(self, fake):
        resp, records = agent._answer_period_quote("삼성전자와 SK하이닉스 중 이틀 동안 어디가 더 올랐어?", [SAMSUNG, HYNIX])
        assert [r["result"]["company_name"] for r in records] == ["삼성전자", "SK하이닉스"]
        assert all(r["result"]["compared_diff_pct_point"] == 17.5 for r in records)
        from app.answer_guard import guard_answer

        assert guard_answer(resp["answer"], records, "")[1]["replaced"] is False  # 코드 답변은 검증을 그대로 통과한다

    def test_not_a_template_question(self, fake):
        assert agent._answer_period_quote("삼성전자 이틀 동안 왜 올랐어?", [SAMSUNG]) is None
