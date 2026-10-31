"""과거 기간 시세 답변(2026-10-31): 3년치 백필 뒤 live 평가의 회귀 5건(ref_08, ref_10, ref_11, ref_13, stab_10)과 경계 조건.
DB 없이 고정된 시세 fixture로 확인한다(날짜 조회 함수만 바꿔 끼운다)."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import agent, code_answers, period_answers
from app.answer_guard import date_violations, guard_answer
from app.period_answers import closed_day_text, closed_reason, day_text, is_history_question, needs_date_lookup, window_text
from app.period_parser import KST, mentions_period, parse_period
from app.period_scope import RequestedPeriod, classify, requested_period
from app.tools import stock_tool as stock_module
from evals.metrics import forbidden_hits, is_refusal, match_numbers

TODAY = date(2026, 10, 31)  # 토요일
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=KST)


def row(day: str, close: float, pct: float | None = 1.0, volume: int | None = 1000):
    return SimpleNamespace(price_date=date.fromisoformat(day), close_price=close, change_pct=pct, volume=volume,
                           open_price=close - 1, high_price=close + 1, low_price=close - 2)


def co(name="기아", id=1, asset_type="stock"):
    return SimpleNamespace(id=id, name=name, ticker="000270", asset_type=asset_type)


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


# 2024-01-02부터 2026-10-30까지 평일 시세. 종가가 날짜마다 달라서 다른 날짜의 값을 붙이면 드러난다.
ROWS = [row(d.isoformat(), 90000 + i * 10) for i, d in enumerate(weekdays(date(2023, 10, 30), date(2026, 10, 30)))]
HOLIDAYS = {date(2024, 8, 15), date(2025, 10, 6)}
ROWS = [r for r in ROWS if r.price_date not in HOLIDAYS]
LAST = ROWS[-1]


@pytest.fixture
def fake_prices(monkeypatch):
    """agent의 DB 접근을 ROWS로 바꾼다. 두 번째 종목(id=2)은 종가가 다르다."""

    def of(company_id):
        return ROWS if company_id == 1 else [SimpleNamespace(**{**vars(r), "close_price": r.close_price * 2}) for r in ROWS]

    monkeypatch.setattr(agent, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(agent, "price_range", lambda db, cid: (ROWS[0].price_date, ROWS[-1].price_date))
    monkeypatch.setattr(agent, "market_has_rows", lambda db, day: any(r.price_date == day for r in ROWS))
    monkeypatch.setattr(agent, "price_rows_between",
                        lambda db, cid, s, e: [r for r in of(cid) if s <= r.price_date <= e])
    monkeypatch.setattr(agent, "last_row_before",
                        lambda db, cid, d: next((r for r in reversed(of(cid)) if r.price_date < d), None))
    monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)}))
    # 범위 밖·걸침은 이 파일에서 period_scope 문장만 본다(DB 조회는 _period_scope_parts 안에 있어 통합 테스트가 확인)
    monkeypatch.setattr(agent, "_period_scope_parts", lambda company, requested: ("범위 밖 문장", {"company_name": company.name, "found": True}))


def close_of(day: str) -> float:
    return next(r.close_price for r in ROWS if r.price_date.isoformat() == day)


def previous_close(day: str) -> tuple[str, float]:
    r = next(r for r in reversed(ROWS) if r.price_date.isoformat() < day)
    return r.price_date.isoformat(), r.close_price


class TestSymptoms:
    def test_ref_13_year_return_uses_that_year_not_recent_rows(self, fake_prices):
        q = "기아 2024년 등락률 알려줘"
        resp, records = agent._answer_period_history(q, [co()])
        r = records[0]["result"]
        assert r["period_end_date"] == "2024-12-31"
        base_day, base_close = previous_close("2024-01-01")
        assert r["period_start_date"] == base_day
        answer = resp["answer"]
        assert f"{close_of('2024-12-31'):,.0f}원" in answer and f"{base_close:,.0f}원" in answer
        assert "2024-01-01~2024-12-31" in answer
        assert "2026-" not in answer
        assert records[0]["arguments"]["start_date"] == "2024-01-01"
        assert records[0]["arguments"]["period_days"] == r["period_days"]
        _, unmatched = match_numbers(answer, [r, q])
        assert unmatched == []

    def test_ref_10_years_ago_answers_with_the_value_on_that_day(self, fake_prices):
        resp, records = agent._answer_period_history("카카오 3년 전 주가 알려줘", [co("카카오")])
        assert "2023-10-31" in resp["answer"]
        assert f"{close_of('2023-10-31'):,.0f}원" in resp["answer"]
        assert not is_refusal(resp["answer"])
        assert records[0]["result"]["prices"][0]["price_date"] == "2023-10-31"

    def test_ref_08_sunday_close_says_closed_and_previous_trading_day(self, fake_prices):
        q = "현대차 2026년 3월 15일 종가 알려줘"
        assert parse_period(q) is None  # "15일"을 15일간으로 읽지 않는다
        resp, records = agent._answer_period_history(q, [co("현대차")])
        answer = resp["answer"]
        assert "2026-03-15(일)" in answer and "휴장" in answer
        assert "직전 거래일(2026-03-13)" in answer
        assert f"{close_of('2026-03-13'):,.0f}원" in answer
        assert is_refusal(answer)  # 그날 값은 없다고 밝힌다
        assert records[0]["result"]["closed_reason"] == "weekend"

    def test_ref_11_month_flow_reports_that_month(self, fake_prices):
        resp, records = agent._answer_period_history("LG전자 2026년 6월 주가 흐름 알려줘", [co("LG전자")])
        r = records[0]["result"]
        assert r["period_end_date"] == "2026-06-30"
        assert all(p["price_date"].startswith("2026-06") for p in r["prices"])
        assert "최근 1일" not in resp["answer"] and "2026-10" not in resp["answer"]

    @pytest.mark.parametrize("raw", ["현대차, 기아", "현대차와 기아", "현대차,기아", None])
    def test_stab_10_combined_company_argument_is_split(self, monkeypatch, raw):
        hyundai, kia = co("현대차", 1), co("기아", 2)
        monkeypatch.setattr(agent, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
        monkeypatch.setattr(agent, "extract_from_text", lambda db, text: [c for c in (hyundai, kia) if c.name in text])
        monkeypatch.setattr(agent, "resolve_company", lambda db, r, allow_fuzzy=True: SimpleNamespace(
            company=next((c for c in (hyundai, kia) if c.name == r), None)))
        calls = [{"function": {"name": "rag_search_tool", "arguments": {"company_name": raw, "query": "사업 구조"}}}]
        agent._expand_multi_company_calls(calls, "현대차와 기아의 사업 구조 차이와 각각의 위험 요인을 설명해줘", [hyundai, kia])
        assert [c["function"]["arguments"]["company_name"] for c in calls] == ["현대차", "기아"]
        assert all(c["function"]["arguments"]["query"] == "사업 구조" for c in calls)

    def test_stab_10_leaves_resolved_and_single_company_calls_alone(self, monkeypatch):
        hyundai, kia = co("현대차", 1), co("기아", 2)
        monkeypatch.setattr(agent, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
        monkeypatch.setattr(agent, "extract_from_text", lambda db, text: [c for c in (hyundai, kia) if c.name in text])
        monkeypatch.setattr(agent, "resolve_company", lambda db, r, allow_fuzzy=True: SimpleNamespace(
            company=next((c for c in (hyundai, kia) if c.name == r), None)))
        two = [{"function": {"name": "stock_tool", "arguments": {"ticker": "현대차"}}},
               {"function": {"name": "stock_tool", "arguments": {"ticker": "기아"}}},
               {"function": {"name": "fx_tool", "arguments": {}}}]
        agent._expand_multi_company_calls(two, "현대차와 기아 비교해줘", [hyundai, kia])
        assert [c["function"]["arguments"].get("ticker") for c in two] == ["현대차", "기아", None]
        # 질문의 종목이 하나면 종목 없는 RAG 호출에 그 종목을 채운다(stab_07)
        one = [{"function": {"name": "rag_search_tool", "arguments": {"query": "x"}}},
               {"function": {"name": "rag_search_tool", "arguments": {"query": "y", "company_name": "현대차"}}},
               {"function": {"name": "news_tool", "arguments": {"limit": 3}}}]
        agent._expand_multi_company_calls(one, "기아 사업 구조 설명해줘", [kia])
        assert one[0]["function"]["arguments"] == {"query": "x", "company_name": "기아"}
        assert one[1]["function"]["arguments"]["company_name"] == "현대차"  # 모델이 고른 종목은 그대로
        assert one[2]["function"]["arguments"] == {"limit": 3}
        # 질문에 종목이 없으면 그대로
        none = [{"function": {"name": "rag_search_tool", "arguments": {"query": "반도체 업황"}}}]
        agent._expand_multi_company_calls(none, "반도체 업황 알려줘", [])
        assert none[0]["function"]["arguments"] == {"query": "반도체 업황"}


def test_compare_question_calls_the_same_tool_for_the_company_the_model_skipped(monkeypatch):
    hyundai, kia = co("현대차", 1), co("기아", 2)
    monkeypatch.setattr(agent, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(agent, "extract_from_text", lambda db, text: [c for c in (hyundai, kia) if c.name in text])
    monkeypatch.setattr(agent, "resolve_company", lambda db, r, allow_fuzzy=True: SimpleNamespace(
        company=next((c for c in (hyundai, kia) if c.name == r), None)))
    calls = [{"function": {"name": "stock_tool", "arguments": {"ticker": "현대차", "period_days": 1}}}]
    agent._expand_multi_company_calls(calls, "현대차와 기아 비교해줘", [hyundai, kia])
    assert [c["function"]["arguments"] for c in calls] == [{"ticker": "현대차", "period_days": 1}, {"ticker": "기아", "period_days": 1}]
    # 비교가 아니면 모델이 고른 대로 둔다
    other = [{"function": {"name": "stock_tool", "arguments": {"ticker": "현대차"}}}]
    agent._expand_multi_company_calls(other, "현대차 주가랑 기아 뉴스 알려줘", [hyundai, kia])
    assert len(other) == 1


def test_rag_searches_every_named_company_even_without_compare_words(monkeypatch):
    samsung, hynix = co("삼성전자", 1), co("SK하이닉스", 2)
    monkeypatch.setattr(agent, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(agent, "extract_from_text", lambda db, text: [c for c in (samsung, hynix) if c.name in text])
    monkeypatch.setattr(agent, "resolve_company", lambda db, r, allow_fuzzy=True: SimpleNamespace(
        company=next((c for c in (samsung, hynix) if c.name == r), None)))
    q = "삼성전자와 SK하이닉스에 투자할 때 고려할 위험 요인을 정리해줘"
    calls = [{"function": {"name": "rag_search_tool", "arguments": {"query": "위험", "company_name": "삼성전자"}}}]
    agent._expand_multi_company_calls(calls, q, [samsung, hynix])
    assert [c["function"]["arguments"]["company_name"] for c in calls] == ["삼성전자", "SK하이닉스"]
    # 다른 도구는 비교 질문일 때만 덧붙인다(모델이 종목별로 다른 도구를 고른 경우를 건드리지 않는다)
    other = [{"function": {"name": "stock_tool", "arguments": {"ticker": "삼성전자"}}}]
    agent._expand_multi_company_calls(other, "삼성전자 주가랑 SK하이닉스 뉴스 알려줘", [samsung, hynix])
    assert len(other) == 1


def test_expand_leaves_calls_alone_without_named_companies_or_when_db_fails(monkeypatch):
    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(agent, "SessionLocal", boom)
    calls = [{"function": {"name": "rag_search_tool", "arguments": {"company_name": "현대차, 기아"}}}]
    agent._expand_multi_company_calls(calls, "현대차와 기아", None)  # DB를 열지 않는다
    agent._expand_multi_company_calls(calls, "현대차와 기아", [co("현대차", 1), co("기아", 2)])  # DB를 못 열면 그대로
    assert calls[0]["function"]["arguments"]["company_name"] == "현대차, 기아"


class TestHistoryAnswers:
    def test_trading_day_close(self, fake_prices):
        resp, _ = agent._answer_period_history("기아 2025년 3월 4일 종가 얼마야?", [co()])
        assert f"2025-03-04 종가는 {close_of('2025-03-04'):,.0f}원" in resp["answer"]

    def test_weekday_holiday_is_named_closed(self, fake_prices):
        resp, records = agent._answer_period_history("기아 2024년 8월 15일 종가 알려줘", [co()])
        assert "휴장" in resp["answer"] and "2024-08-14" in resp["answer"]
        assert records[0]["result"]["closed_reason"] == "holiday"

    def test_iso_date(self, fake_prices):
        resp, _ = agent._answer_period_history("기아 2025-10-06 종가 알려줘", [co()])
        assert "휴장" in resp["answer"] and "2025-10-03" in resp["answer"]

    def test_day_after_data_end_on_weekday_is_not_called_holiday(self, fake_prices):
        resp, records = agent._answer_period_history("기아 2026년 10월 29일 종가", [co()])
        assert "휴장" not in resp["answer"]
        assert closed_reason(date(2026, 11, 2), False, date(2026, 10, 30)) == period_answers.REASON_NOT_COLLECTED  # 월요일, 데이터 끝 뒤

    def test_year_ago_on_weekend_gives_previous_trading_day(self, fake_prices):
        # 오늘이 토요일이므로 "1주 전"도 토요일
        resp, _ = agent._answer_period_history("기아 1주 전 주가", [co()])
        assert "주말" in resp["answer"] and "2026-10-23" in resp["answer"]

    def test_whole_year_wording_is_not_years_ago(self, fake_prices):
        _, records = agent._answer_period_history("기아 2025년 전체 등락률은?", [co()])
        assert records[0]["result"]["period_end_date"] == "2025-12-31"

    def test_quarter_return_has_start_and_end_dates(self, fake_prices):
        resp, records = agent._answer_period_history("기아 2025년 3분기 수익률 알려줘", [co()])
        r = records[0]["result"]
        assert r["period_end_date"] == "2025-09-30"
        assert r["period_start_date"] == "2025-06-30"  # 구간 직전 거래일 종가 기준
        assert "2025-07-01~2025-09-30" in resp["answer"]

    def test_parser_period_days_is_recorded(self, fake_prices):
        # 평가의 live_period와 분석이 읽는 값이다
        _, records = agent._answer_period_history("기아 작년 주가 흐름 알려줘", [co()])
        assert records[0]["arguments"]["period_days"] == parse_period("기아 작년 주가 흐름 알려줘").period_days
        assert records[0]["result"]["period_end_date"] == "2025-12-31"

    def test_last_year_same_month(self, fake_prices):
        _, records = agent._answer_period_history("기아 작년 같은 달 수익률 알려줘", [co()])
        assert records[0]["result"]["period_end_date"] == "2025-10-31"

    def test_year_to_date_mentions_last_data_date(self, fake_prices):
        resp, records = agent._answer_period_history("기아 2026년 등락률", [co()])
        assert records[0]["result"]["period_end_date"] == "2026-10-30"
        assert "마지막 데이터는 2026-10-30 기준" in resp["answer"]

    def test_all_time_high_is_stated_as_held_data_range(self, fake_prices):
        resp, records = agent._answer_period_history("기아 역대 최고가 알려줘", [co()])
        assert "보유 데이터 전체(2023-10-30~2026-10-30)" in resp["answer"]
        assert "종가 기준" in resp["answer"]
        assert f"{LAST.close_price:,.0f}원" in resp["answer"]

    def test_volume_month_reports_average_and_max(self, fake_prices):
        resp, records = agent._answer_period_history("기아 2025년 1월 거래량은?", [co()])
        r = records[0]["result"]
        assert r["volume_avg"] == 1000 and "일평균 거래량은 1,000주" in resp["answer"]

    def test_two_companies_same_window_compared(self, fake_prices):
        q = "현대차와 기아 2025년 수익률 비교해줘"
        resp, records = agent._answer_period_history(q, [co("현대차", 1), co("기아", 2)])
        assert len(records) == 2
        assert "현대차" in resp["answer"] and "기아" in resp["answer"] and "거래일 기준 비교" in resp["answer"]
        _, unmatched = match_numbers(resp["answer"], [r["result"] for r in records] + [q])
        assert unmatched == []

    def test_two_companies_day_answers_both(self, fake_prices):
        resp, records = agent._answer_period_history("현대차와 기아 2025년 3월 4일 종가", [co("현대차", 1), co("기아", 2)])
        assert len(records) == 2 and "현대차의 2025-03-04" in resp["answer"] and "기아의 2025-03-04" in resp["answer"]

    def test_future_period_says_no_data_and_range(self, fake_prices):
        resp, _ = agent._answer_period_history("기아 2027년 3월 주가 알려줘", [co()])
        assert is_refusal(resp["answer"]) and "2026-10-30까지" in resp["answer"]

    def test_leveraged_etf_keeps_notice(self, fake_prices):
        resp, _ = agent._answer_period_history("KODEX 레버리지 2025년 등락률", [co("KODEX 레버리지", asset_type="etf")])
        assert "레버리지" in resp["answer"] and "복리" in resp["answer"]

    @pytest.mark.parametrize("q", ["기아 2024년 실적 뉴스", "기아 2024년 왜 떨어졌어", "기아 2024년 주가 전망", "기아 2024년 코스피 대비 수익률",
                                   "기아 오늘 주가", "기아 주가 알려줘", "기아 최근 한 달 주가 흐름", "기아 지난주 주가 흐름", "기아 지난달 주가"])
    def test_other_questions_keep_existing_flow(self, fake_prices, q):
        assert agent._answer_period_history(q, [co()]) is None

    def test_three_companies_or_none_keep_existing_flow(self, fake_prices):
        assert agent._answer_period_history("2024년 등락률", []) is None
        assert agent._answer_period_history("2024년 등락률", [co(), co("A", 2), co("B", 3)]) is None

    def test_templates_have_no_forbidden_expressions(self, fake_prices):
        for q in ("기아 2024년 등락률", "기아 2025년 5월 3일 종가", "기아 2025년 1월 거래량", "기아 역대 최저가"):
            resp, _ = agent._answer_period_history(q, [co()])
            assert forbidden_hits(resp["answer"]) == []


class TestPeriodExpressions:
    @pytest.mark.parametrize("q,start,end", [
        ("현대차 작년 같은 달 수익률", "2025-10-01", "2025-10-31"),
        ("작년 동월 주가", "2025-10-01", "2025-10-31"),
        ("셀트리온 연초 대비 수익률", "2026-01-01", "2026-10-31"),
        ("카카오 2025년 3분기 수익률", "2025-07-01", "2025-09-30"),
        ("작년 4분기 주가", "2025-10-01", "2025-12-31"),
        ("올해 1분기 수익률", "2026-01-01", "2026-03-31"),
        ("3분기 수익률", "2026-07-01", "2026-09-30"),
        ("4분기 수익률", "2026-10-01", "2026-10-31"),  # 진행 중인 분기는 오늘까지
        ("2025년 상반기 주가", "2025-01-01", "2025-06-30"),
        ("작년 하반기 주가", "2025-07-01", "2025-12-31"),
        ("작년 3월 주가", "2025-03-01", "2025-03-31"),
        ("재작년 5월 주가", "2024-05-01", "2024-05-31"),
        ("올해 3월 15일 종가", "2026-03-15", "2026-03-15"),
        ("지난해 수익률", "2025-01-01", "2025-12-31"),
        ("2024년 3월부터 2024년 6월까지 주가", "2024-03-01", "2024-06-30"),
        ("2023년 11월부터 12월까지 주가", "2023-11-01", "2023-12-31"),
        ("2024년 3월부터 주가", "2024-03-01", "2026-10-31"),
        ("기아 2025년 전체 등락률", "2025-01-01", "2025-12-31"),  # "025년 전"이 아니다
        ("기아 2025년 연간 등락률", "2025-01-01", "2025-12-31"),
    ])
    def test_requested_period(self, q, start, end):
        r = requested_period(q, TODAY)
        assert (r.start.isoformat(), r.end.isoformat()) == (start, end)

    @pytest.mark.parametrize("q", ["현대차 2026년 3월 15일 종가", "3월 5일 주가", "12월 31일 등락률", "2024년 1월 2일 거래량"])
    def test_month_day_is_not_a_quantity_of_days(self, q):
        assert parse_period(q) is None

    @pytest.mark.parametrize("q,days", [("최근 15일 주가", 10), ("3개월 수익률", 66), ("2주간 주가", 10), ("5일 주가", 4)])
    def test_quantities_still_parse(self, q, days):
        assert parse_period(q, NOW).period_days == days

    def test_needs_date_lookup(self):
        def need(q):
            return needs_date_lookup(requested_period(q, TODAY), parse_period(q, NOW), TODAY)

        assert need("2024년 등락률") and need("3년 전 주가") and need("어제 주가") and need("지난달 주가")
        assert not need("최근 1년 주가 흐름") and not need("3개월 추이")  # 끝이 오늘인 창은 기존 흐름
        assert not need("오늘 주가") and not need("최근 한 달 주가") and not need("이번 달 주가") and not need("주가 알려줘")

    def test_is_history_question(self):
        assert is_history_question("기아 2025년 1월 거래량은?")
        assert not is_history_question("기아 2024년 실적") and not is_history_question("기아 2024년 뉴스와 주가")


class TestBoundaries:
    START, END = date(2023, 10, 30), date(2026, 10, 30)

    @pytest.mark.parametrize("q,kind", [
        ("2023년 10월 27일 종가", "outside"),  # 시작일 직전
        ("2023년 9월 주가", "outside"),
        ("2022년 주가", "outside"),
        ("2023년 10월 주가", "partial"),  # 시작일에 걸침
        ("2023년 주가", "partial"),
        ("5년 수익률", "partial"),
        ("2023년 10월 30일 종가", None),  # 시작일 당일은 범위 안
        ("2023년 11월 주가", None),
        ("2026년 10월 30일 종가", None),
    ])
    def test_classify_after_backfill(self, q, kind):
        scope = classify(requested_period(q, TODAY), self.START, self.END)
        assert (scope.kind if scope else None) == kind

    def test_window_at_first_data_day_has_no_base_and_uses_first_close(self, fake_prices):
        _, records = agent._answer_period_history("기아 2023년 11월 수익률", [co()])
        assert records[0]["result"]["period_start_date"] == "2023-10-31"  # 11-01 직전 거래일
        rows = [r for r in ROWS[:3]]
        result = code_answers.window_result(co(), list(reversed(rows)), rows[0].price_date, NOW, "period_history",
                                            require_base=False)
        assert result["period_start_date"] == rows[0].price_date.isoformat()
        assert code_answers.window_result(co(), list(reversed(rows)), rows[0].price_date, NOW, "x") is None

    def test_window_end_excludes_later_rows(self):
        rows = list(reversed(ROWS[-12:]))  # 10월 중순~10-30
        end = ROWS[-6].price_date
        result = code_answers.window_result(co(), rows, ROWS[-10].price_date, NOW, "period_quote", end=end)
        assert result["period_end_date"] == end.isoformat()
        assert all(p["price_date"] <= end.isoformat() for p in result["prices"])

    def test_single_row_window_answers_that_day(self, fake_prices, monkeypatch):
        monkeypatch.setattr(agent, "price_rows_between", lambda db, cid, s, e: [r for r in ROWS if r.price_date == date(2025, 3, 4)]
                            if s != e else [r for r in ROWS if r.price_date == s])
        resp, _ = agent._answer_period_history("기아 2025년 3월 수익률", [co()])
        assert "2025-03-04 종가는" in resp["answer"]


class TestStockToolWindow:
    @pytest.fixture
    def tool(self, monkeypatch):
        monkeypatch.setattr(stock_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
        monkeypatch.setattr(stock_module, "resolve_company", lambda db, t: SimpleNamespace(company=co(), corrected_from=None))
        monkeypatch.setattr(stock_module, "recent_price_rows", lambda db, cid, n: list(reversed(ROWS))[:n])
        monkeypatch.setattr(stock_module, "price_rows_between", lambda db, cid, s, e: [r for r in ROWS if s <= r.price_date <= e])
        monkeypatch.setattr(stock_module, "last_row_before",
                            lambda db, cid, d: next((r for r in reversed(ROWS) if r.price_date < d), None))
        return stock_module.stock_tool

    def test_long_period_is_not_silently_clipped(self, tool):
        r = tool("기아", 400)
        assert r["period_days"] == 400 and r["trading_days"] == 400
        assert len(r["prices"]) == stock_module.MAX_PRICE_ROWS and r["prices"][-1]["price_date"] == "2026-10-30"
        assert "최근 60거래일" in r["prices_note"]
        # 요약은 전체 400거래일의 시작 종가 기준이다(60거래일로 자르면 다른 값)
        assert r["period_start_date"] == ROWS[-401].price_date.isoformat()

    def test_short_period_is_unchanged(self, tool):
        r = tool("기아", 5)
        assert r["period_days"] == 5 and len(r["prices"]) == 5 and "prices_note" not in r and "data_note" not in r

    def test_period_longer_than_held_data_says_so(self, tool):
        r = tool("기아", 2000)
        assert "보유 시세가 2023-10-30부터" in r["data_note"]

    def test_date_range_reads_that_window_not_recent_rows(self, tool):
        r = tool("기아", 1, start_date="2024-03-01", end_date="2024-03-31")
        assert r["period_end_date"] == "2024-03-29" and r["period_start_date"] == "2024-02-29"
        assert all(p["price_date"].startswith("2024-03") for p in r["prices"])

    def test_empty_date_range_is_not_found_not_another_window(self, tool):
        r = tool("기아", 1, start_date="2024-08-15", end_date="2024-08-15")
        assert r["found"] is False and "2024-08-15" in r["message"]

    def test_bad_dates_fall_back_to_recent_window(self, tool):
        assert tool("기아", 3, start_date="아무거나", end_date=None)["period_days"] == 3


class TestApplyPastWindow:
    def _call(self):
        return [{"function": {"name": "stock_tool", "arguments": {"ticker": "기아", "period_days": 1}}},
                {"function": {"name": "news_tool", "arguments": {"company_name": "기아"}}}]

    def test_past_window_goes_to_stock_tool_only(self, monkeypatch):
        monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)}))
        calls = self._call()
        agent._apply_past_window(calls, "기아 2024년 3월 주가와 뉴스 알려줘")
        assert calls[0]["function"]["arguments"]["start_date"] == "2024-03-01"
        assert calls[0]["function"]["arguments"]["end_date"] == "2024-03-31"
        assert "start_date" not in calls[1]["function"]["arguments"]

    def test_short_past_window_in_llm_flow_reads_that_window(self, monkeypatch):
        monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)}))
        calls = self._call()
        agent._apply_past_window(calls, "기아 지난주 주가 흐름 알려줘")
        assert calls[0]["function"]["arguments"]["start_date"] == "2026-10-19"
        assert calls[0]["function"]["arguments"]["end_date"] == "2026-10-25"

    @pytest.mark.parametrize("q", ["기아 주가와 뉴스 알려줘", "기아 최근 한 달 주가와 뉴스", "기아 오늘 주가와 뉴스"])
    def test_recent_or_no_period_is_untouched(self, monkeypatch, q):
        monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)}))
        calls = self._call()
        agent._apply_past_window(calls, q)
        assert "start_date" not in calls[0]["function"]["arguments"]


def stock_result(rows, summary=None, name="기아"):
    prices = [{"price_date": d, "close_price": c, "volume": 1000, "change_pct": p} for d, c, p in rows]
    return {"found": True, "company_name": name, "prices": prices, "change_pct": prices[-1]["change_pct"], "volume": 1000,
            **({"summary": summary} if summary else {})}


class TestGuardDatePairs:
    RECENT = stock_result(
        [("2026-07-31", 131900.0, 0.5), ("2026-10-30", 94800.0, -12.71)],
        "기아의 2026-10-30 종가는 94,800원으로, 2026-07-30 종가 130,000원 대비 2거래일간 -27.08%입니다.",
    )

    def test_ref_13_wrong_year_on_recent_value_is_caught(self):
        bad = date_violations("기아의 2024-10-30 종가는 94,800원입니다.", [self.RECENT], "기아 2024년 등락률", TODAY)
        assert bad

    def test_ref_13_full_sentence_is_replaced_by_code_summary(self):
        records = [{"tool_name": "stock_tool", "result": self.RECENT}]
        answer, log = guard_answer("기아의 2024-10-30 종가는 94,800원으로, 2024-07-31 종가 131,900원 대비 60거래일간 -28.13%입니다.",
                                   records, "기아 2024년 등락률 알려줘", TODAY)
        assert log["replaced"] and log["mode"] == "code_summary" and log["kinds"]["date"] >= 1
        assert "2024-10-30" not in answer and "2026-10-30" in answer

    def test_year_label_on_recent_value_is_caught(self):
        records = [{"tool_name": "stock_tool", "result": self.RECENT}]
        _, log = guard_answer("기아의 2024년 등락률은 -12.71%입니다.", records, "기아 2024년 등락률", TODAY)
        assert log["replaced"]

    def test_date_in_question_with_value_of_another_date_is_caught(self):
        bad = date_violations("2024-10-30 종가는 94,800원입니다.", [self.RECENT], "기아 2024-10-30 종가 알려줘", TODAY)
        assert bad

    def test_right_date_value_pair_passes(self):
        assert date_violations("기아의 2026-10-30 종가는 94,800원입니다.", [self.RECENT], "기아 주가", TODAY) == []

    def test_month_day_without_year_still_uses_rows(self):
        assert date_violations("10월 30일 종가는 94,800원입니다.", [self.RECENT], "기아 주가", TODAY) == []
        assert date_violations("10월 30일 종가는 131,900원입니다.", [self.RECENT], "기아 주가", TODAY)

    def test_base_date_value_from_summary_passes(self):
        # 기준일(구간 직전 거래일)은 prices 행이 아니라 요약 문장에만 있다
        assert date_violations("2026-07-30 종가 130,000원 대비 올랐습니다.", [self.RECENT], "기아 주가", TODAY) == []

    def test_label_with_rows_in_that_period_passes(self):
        results = [stock_result([("2024-03-04", 90000.0, 1.0), ("2024-03-05", 91000.0, 1.11)])]
        assert date_violations("기아의 2024년 3월 종가는 91,000원입니다.", results, "기아 2024년 3월 주가", TODAY) == []

    def test_label_with_value_not_from_rows_is_not_this_rule(self):
        # 뉴스 수치처럼 시계열 값이 아니면 기간 라벨 규칙으로 잡지 않는다(근거 없는 숫자는 숫자 규칙이 본다)
        assert date_violations("2024년 영업이익은 3조 원입니다.", [self.RECENT], "기아 뉴스", TODAY) == []

    def test_relative_label_uses_today(self):
        assert date_violations("작년 종가는 94,800원입니다.", [self.RECENT], "기아 작년 주가", TODAY)
        assert date_violations("3년 전 종가는 94,800원입니다.", [self.RECENT], "기아 3년 전 주가", TODAY)

    def test_news_only_results_are_not_checked_by_label_rule(self):
        news = {"found": True, "news": [{"title": "t", "published_at": "2026-10-30"}]}
        assert date_violations("2024년 매출은 94,800원입니다.", [news], "기아 뉴스", TODAY) == []

    def test_correct_llm_answer_is_kept(self):
        records = [{"tool_name": "stock_tool", "result": self.RECENT}]
        text = "기아의 2026-10-30 종가는 94,800원입니다."
        answer, log = guard_answer(text, records, "기아 주가", TODAY)
        assert answer == text and not log["replaced"]


class TestTemplates:
    def test_closed_day_texts(self):
        prev = {"price_date": "2026-03-13", "close_price": 100000.0}
        for reason, word in ((period_answers.REASON_WEEKEND, "주말"), (period_answers.REASON_HOLIDAY, "휴장일"),
                             (period_answers.REASON_COMPANY, "거래정지"), (period_answers.REASON_NOT_COLLECTED, "수집되지")):
            text = closed_day_text("현대차", date(2026, 3, 15), reason, prev)
            assert word in text and "100,000원" in text and "2026-03-13" in text and is_refusal(text)

    def test_day_text_includes_ohlc_only_when_asked(self):
        r = {"price_date": "2025-03-04", "close_price": 100.0, "change_pct": 1.5, "volume": 10, "open_price": 99.0, "high_price": 101.0,
             "low_price": 98.0}
        assert "시가" not in day_text("A", r, "A 종가")
        assert "시가 99원" in day_text("A", r, "A 시가 알려줘")
        assert "시가" not in day_text("A", r, "A 최고가") and "시가" not in day_text("A", r, "A 시가총액")

    def test_window_text_requested_range_and_tail_note(self):
        req = RequestedPeriod(date(2026, 1, 1), date(2026, 12, 31), "2026년")
        result = {"summary": "요약.", "company_name": "A", "volume_avg": 5, "volume_max": 9, "volume_max_date": "2026-01-02"}
        text = window_text(result, req, date(2026, 10, 30), "A 2026년 거래량")
        assert "2026-01-01~2026-12-31" in text and "일평균 거래량은 5주" in text and "마지막 데이터는 2026-10-30" in text
        assert "일평균" not in window_text(result, req, date(2026, 10, 30), "A 2026년 등락률")


def test_alias_is_exact_only_and_strict_mode_is_not_loosened():
    from app.company_aliases import COMPANY_ALIASES

    assert COMPANY_ALIASES["기아차"] == "기아"
    assert "현대" not in COMPANY_ALIASES and "기아" not in COMPANY_ALIASES


class TestCalendarExpressions:
    """올해·작년 + 월·분기·반기, 두 자리 연도(2026-10-31). 파서가 일부만 읽어 다른 기간을 답하던 문제."""

    @pytest.mark.parametrize("q,start,end", [
        ("LG전자 올해 1분기 수익률", "2026-01-01", "2026-03-31"),
        ("올해 2분기 수익률", "2026-04-01", "2026-06-30"),
        ("올해 3분기 수익률", "2026-07-01", "2026-09-30"),
        ("올해 4분기 수익률", "2026-10-01", "2026-10-31"),  # 진행 중
        ("올해 3월 수익률", "2026-03-01", "2026-03-31"),
        ("올해 상반기 최고가", "2026-01-01", "2026-06-30"),
        ("올해 하반기 주가", "2026-07-01", "2026-10-31"),
        ("금년 2월 주가", "2026-02-01", "2026-02-28"),
        ("작년 3월 주가", "2025-03-01", "2025-03-31"),
        ("작년 4분기 비교", "2025-10-01", "2025-12-31"),
        ("작년 상반기 최저가", "2025-01-01", "2025-06-30"),
        ("지난해 하반기 수익률", "2025-07-01", "2025-12-31"),
        ("재작년 2분기 수익률", "2024-04-01", "2024-06-30"),
        ("24년 3월 주가", "2024-03-01", "2024-03-31"),
        ("24년 2월 주가", "2024-02-01", "2024-02-29"),  # 윤년
        ("25년 2월 주가", "2025-02-01", "2025-02-28"),
        ("24년 3월 15일 종가", "2024-03-15", "2024-03-15"),
        ("24년 3분기 수익률", "2024-07-01", "2024-09-30"),
        ("25년 상반기 주가", "2025-01-01", "2025-06-30"),
        ("24년 주가", "2024-01-01", "2024-12-31"),
        ("24년 전체 등락률", "2024-01-01", "2024-12-31"),
        ("27년 3월 주가", "2027-03-01", "2027-03-31"),  # 올해를 넘어도 20YY(데이터 없음 안내로 간다)
        ("95년 3월 주가", "2095-03-01", "2095-03-31"),  # 19YY로 고치지 않는다
        ("올해 12월 주가", "2026-12-01", "2026-12-31"),  # 아직 오지 않은 달: 시작이 끝보다 뒤로 가지 않는다
    ])
    def test_requested_period(self, q, start, end):
        r = requested_period(q, TODAY)
        assert (r.start.isoformat(), r.end.isoformat()) == (start, end)

    @pytest.mark.parametrize("today,q,start,end", [
        (date(2026, 1, 15), "올해 4분기 수익률", "2026-10-01", "2026-12-31"),  # 1월에 묻는 미래 구간
        (date(2026, 1, 15), "4분기 수익률", "2025-10-01", "2025-12-31"),  # 연도 없으면 지난 4분기
        (date(2026, 1, 15), "1분기 수익률", "2026-01-01", "2026-01-15"),  # 진행 중
        (date(2026, 3, 31), "1분기 수익률", "2026-01-01", "2026-03-31"),  # 분기 마지막 날
        (date(2026, 4, 1), "2분기 수익률", "2026-04-01", "2026-04-01"),  # 분기 첫날
        (date(2026, 4, 1), "3분기 수익률", "2025-07-01", "2025-09-30"),
        (date(2028, 2, 29), "올해 2월 주가", "2028-02-01", "2028-02-29"),  # 윤년
        (date(2026, 6, 30), "상반기 수익률", "2026-01-01", "2026-06-30"),
        (date(2026, 6, 30), "하반기 수익률", "2025-07-01", "2025-12-31"),
    ])
    def test_boundaries(self, today, q, start, end):
        r = requested_period(q, today)
        assert (r.start.isoformat(), r.end.isoformat()) == (start, end) and r.start <= r.end

    @pytest.mark.parametrize("q", ["올해 1분기 수익률", "올해 3월 수익률", "작년 3월 수익률", "작년 4분기", "작년 상반기", "작년 이맘때 수익률",
                                   "작년 같은 달 수익률", "24년 3월 주가", "24년 주가", "22년 주가", "24년 3분기"])
    def test_parser_does_not_read_part_of_a_calendar_expression(self, q):
        assert parse_period(q, NOW) is None
        # 값은 달력 표현이 맡아도 "기간을 말했다"는 사실은 남아야 한다(단순 시세로 새던 ref_09 "작년 이맘때 주가")
        assert mentions_period(q)

    @pytest.mark.parametrize("q", ["NAVER 작년 이맘때 주가는 얼마였어?", "카카오 작년 같은 달 주가", "삼성전자 24년 주가 얼마야", "기아 올해 1분기 주가"])
    def test_calendar_expression_is_not_a_simple_quote(self, q):
        from app.answer_guard import is_simple_quote

        assert not is_simple_quote(q, mentions_period(q), agent._period_text(q))

    def test_plain_quote_is_still_simple(self):
        from app.answer_guard import is_simple_quote

        q = "NAVER 주가 얼마야?"
        assert not mentions_period(q) and is_simple_quote(q, False, None)

    @pytest.mark.parametrize("q,text", [("올해 수익률", "올해"), ("작년 주가", "작년"), ("5년 주가", "5년"), ("30년 주가", "30년"),
                                        ("최근 24년 주가", "24년"), ("24년간 주가", "24년"), ("27년 주가", "27년"), ("1년 수익률", "1년")])
    def test_plain_year_words_and_quantities_still_parse(self, q, text):
        assert parse_period(q, NOW).matched_text == text

    def test_quarter_answer_is_not_the_year_to_date_path(self, fake_prices, monkeypatch):
        # hist_17: "올해"만 읽어 _answer_period_quote가 연초~현재로 답하던 문제
        monkeypatch.setattr(agent, "parse_period", parse_period)
        q = "기아 올해 1분기 수익률은?"
        assert agent._answer_period_quote(q, [co()]) is None
        resp, records = agent._answer_period_history(q, [co()])
        assert records[0]["result"]["period_end_date"] == "2026-03-31"
        assert "2026-01-01~2026-03-31" in resp["answer"]

    def test_two_digit_year_answer_states_the_range(self, fake_prices):
        resp, records = agent._answer_period_history("기아 24년 3월 주가 알려줘", [co()])
        assert "24년 3월: 2024-03-01~2024-03-31" in resp["answer"]
        assert records[0]["result"]["period_end_date"] == "2024-03-29"

    def test_future_month_says_no_data(self, fake_prices):
        resp, _ = agent._answer_period_history("기아 올해 12월 주가 알려줘", [co()])
        assert is_refusal(resp["answer"]) and "2026-12-01~2026-12-31" in resp["answer"]
        resp, _ = agent._answer_period_history("기아 27년 3월 주가 알려줘", [co()])
        assert is_refusal(resp["answer"]) and "2027-03-01~2027-03-31" in resp["answer"]


D = date
STOCK = [(D(2023, 12, 27), 100.0), (D(2023, 12, 28), 100.0), (D(2024, 1, 2), 101.0), (D(2024, 6, 3), 110.0), (D(2024, 12, 30), 120.0), (D(2026, 10, 30), 300.0)]
INDEX = [(D(2023, 12, 27), 2000.0), (D(2023, 12, 28), 2000.0), (D(2024, 1, 2), 2010.0), (D(2024, 6, 3), 2100.0), (D(2024, 12, 30), 2200.0), (D(2026, 10, 30), 4000.0)]


class TestMarketRange:
    def test_stock_and_index_use_the_same_window(self):
        from app.tools.market_tool import compare_range

        cmp = compare_range(STOCK, INDEX, D(2024, 1, 1), D(2024, 12, 31))
        assert (cmp["start_date"], cmp["end_date"]) == ("2023-12-28", "2024-12-30")
        assert cmp["stock_change_pct"] == 20.0 and cmp["market_change_pct"] == 10.0 and cmp["period_days"] == 3

    def test_long_window_is_not_cut_to_sixty_days(self):
        from app.tools.market_tool import compare_range

        stock = [(D(2024, 1, 1) + timedelta(days=i), 100.0 + i) for i in range(400)]
        index = [(d, 1000.0 + i) for i, (d, _) in enumerate(stock)]
        cmp = compare_range(stock, index, D(2024, 1, 2), D(2025, 1, 30))
        assert cmp["period_days"] > 60 and cmp["start_date"] == "2024-01-01"

    def test_no_overlap_is_none(self):
        from app.tools.market_tool import compare_range

        assert compare_range(STOCK, INDEX, D(2022, 1, 1), D(2022, 12, 31)) is None
        assert compare_range(STOCK[:2], [], D(2023, 12, 1), D(2023, 12, 31)) is None

    def test_uses_only_days_both_have(self):
        from app.tools.market_tool import compare_range

        cmp = compare_range(STOCK, [x for x in INDEX if x[0] != D(2024, 12, 30)], D(2024, 1, 1), D(2024, 12, 31))
        assert cmp["end_date"] == "2024-06-03"

    def _fake_db(self, stock, index):
        from app.models import StockPrice

        def query(*cols):
            rows = stock if cols[0].class_ is StockPrice else index
            chain = SimpleNamespace(filter=lambda *a: chain, order_by=lambda *a: chain, all=lambda: rows)
            return chain

        return SimpleNamespace(query=query)

    def test_range_result_notes_what_the_index_does_not_cover(self):
        from app.tools.market_tool import _market_range_result

        start, end = D(2023, 1, 1), D(2024, 12, 31)
        r = _market_range_result(self._fake_db(STOCK, INDEX), co(), {"company_name": "기아"}, "KS11", "코스피", start, end)
        assert r["found"] and r["requested_start"] == "2023-01-01" and "2023-12-27~2026-10-30" in r["note"]
        assert r["start_date"] == "2023-12-27"

    def test_holiday_at_the_edge_of_the_range_is_not_a_gap(self):
        # hist_28: 2024-01-01과 2024-12-31은 휴장일이다. "데이터가 12-30까지만 있다"고 말하면 안 된다
        from app.tools.market_tool import _market_range_result

        r = _market_range_result(self._fake_db(STOCK, INDEX), co(), {"company_name": "기아"}, "KS11", "코스피", D(2024, 1, 1), D(2024, 12, 31))
        assert r["found"] and "note" not in r and r["end_date"] == "2024-12-30"

    def test_range_past_the_last_data_is_noted(self):
        from app.tools.market_tool import _market_range_result

        r = _market_range_result(self._fake_db(STOCK, INDEX), co(), {"company_name": "기아"}, "KS11", "코스피", D(2026, 1, 1), D(2026, 12, 31))
        assert r["found"] and "2026-10-30" in r["note"]

    def test_range_result_without_note_when_covered(self):
        from app.tools.market_tool import _market_range_result

        r = _market_range_result(self._fake_db(STOCK, INDEX), co(), {"company_name": "기아"}, "KS11", "코스피", D(2024, 1, 2), D(2024, 6, 3))
        assert r["found"] and "note" not in r

    def test_range_result_not_found(self):
        from app.tools.market_tool import _market_range_result

        r = _market_range_result(self._fake_db([], []), co(), {"company_name": "기아"}, "KS11", "코스피", D(2022, 1, 1), D(2022, 12, 31))
        assert r["found"] is False and is_refusal(r["message"])

    def test_agent_answers_with_requested_range_and_summary(self, monkeypatch):
        monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)}))
        got = {}

        def fake_market(**kw):
            got.update(kw)
            return {"found": True, "company_name": "기아", "summary": "요약."}

        monkeypatch.setattr(agent, "market_tool", fake_market)
        resp, records = agent._answer_market_period("2024년 코스피 대비 기아 수익률 알려줘", [co()])
        assert got["start_date"] == "2024-01-01" and got["end_date"] == "2024-12-31"
        assert "2024-01-01~2024-12-31" in resp["answer"] and records[0]["tool_name"] == "market_tool"

    @pytest.mark.parametrize("q", ["기아 코스피 대비 수익률", "기아 최근 3개월 코스피 대비 수익률", "2024년 코스피 대비 기아 왜 떨어졌어", "기아 2024년 수익률"])
    def test_other_questions_keep_existing_flow(self, monkeypatch, q):
        monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)}))
        monkeypatch.setattr(agent, "market_tool", lambda **kw: pytest.fail("호출하면 안 된다"))
        assert agent._answer_market_period(q, [co()]) is None

    def test_etf_and_two_companies_keep_existing_flow(self, monkeypatch):
        monkeypatch.setattr(agent, "market_tool", lambda **kw: pytest.fail("호출하면 안 된다"))
        assert agent._answer_market_period("2024년 코스피 대비 KODEX 200", [co("KODEX 200", asset_type="etf")]) is None
        assert agent._answer_market_period("2024년 코스피 대비 현대차 기아", [co("현대차"), co("기아", 2)]) is None

    def test_past_window_is_passed_to_market_tool_too(self, monkeypatch):
        monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)}))
        calls = [{"function": {"name": "market_tool", "arguments": {"ticker": "기아", "period_days": 5}}},
                 {"function": {"name": "stock_tool", "arguments": {"ticker": "기아", "period_days": 1}}}]
        agent._apply_past_window(calls, "기아 2024년 코스피 대비 왜 떨어졌어")
        ranges = [(c["function"]["arguments"]["start_date"], c["function"]["arguments"]["end_date"]) for c in calls]
        assert ranges == [("2024-01-01", "2024-12-31")] * 2


class TestForeignScript:
    REC = [{"tool_name": "news_tool", "result": {"found": True, "news": [{"title": "中 시장 판매 급감, 韓 기업 영향"}]}}]

    def _strip(self, text, records=None, q="현대차 위험 요인 알려줘"):
        from app.answer_guard import strip_foreign_script

        return strip_foreign_script(text, records if records is not None else self.REC, q)

    @pytest.mark.parametrize("text", ["현대차는 시장シェア가 큽니다.", "ひらがな 문장입니다.", "기간期间 동안 올랐습니다.", "市場 상황이 나쁩니다.", "ｼｪｱ 반각입니다."])
    def test_sentence_with_unknown_foreign_script_is_removed(self, text):
        answer, n = self._strip(f"첫 문장입니다.\n{text}\n끝 문장입니다.")
        assert n == 1 and answer.split() == "첫 문장입니다. 끝 문장입니다.".split()

    def test_only_the_bad_sentence_in_a_line_goes(self):
        answer, n = self._strip("좋은 문장입니다. 나쁜シェア 문장입니다. 또 좋은 문장입니다.")
        assert n == 1 and answer == "좋은 문장입니다. 또 좋은 문장입니다."

    @pytest.mark.parametrize("text", ["中 시장에서 판매가 줄었습니다.", "韓 기업 영향이 있습니다.",  # 도구 결과에 그대로 있는 표기
                                      "NVIDIA(엔비디아)와 TSMC 영향입니다.", "삼성전자(Samsung Electronics, 005930)는 상승했습니다.",
                                      "KODEX 200 ETF의 PER은 10배입니다.", "S&P500과 AI 수요가 큽니다.", "주가는 123,400원(+1.5%)입니다.", "**굵게** 쓴 # 마크다운 - 항목"])
    def test_normal_answers_pass_unchanged(self, text):
        assert self._strip(text) == (text, 0)

    def test_question_text_counts_as_evidence(self):
        assert self._strip("三星 관련 답입니다.", [], "三星 주가 알려줘")[1] == 0

    def test_non_korean_question_is_not_checked(self):
        assert self._strip("シェア is share.", [], "what is share?")[1] == 0

    def test_answer_with_only_foreign_sentences_falls_back_to_code_summary(self, monkeypatch):
        agent._reset_llm_state()
        monkeypatch.setattr(agent, "guard_answer", lambda a, r, q: (a, {"replaced": False, "violations": [], "mode": None}))
        records = [{"tool_name": "stock_tool", "result": {"found": True, "company_name": "기아", "summary": "기아 요약입니다.", "prices": []}}]
        assert "기아 요약입니다." in agent._guard("すべて日本語です。", records, "기아 주가 알려줘")
        assert agent._meta()["answer_guard"]["foreign_script_sentences"] == 1

    def test_original_is_kept_in_internal_log_only(self, monkeypatch):
        agent._reset_llm_state()
        monkeypatch.setattr(agent, "guard_answer", lambda a, r, q: (a, {"replaced": False, "violations": [], "mode": None}))
        out = agent._guard("좋은 문장입니다. 나쁜シェア 문장입니다.", [], "기아 어때?")
        assert "シェア" not in out and "シェア" in agent._meta()["answer_guard"]["foreign_script_original"]

    def test_empty_section_is_removed_after_stripping(self, monkeypatch):
        agent._reset_llm_state()
        monkeypatch.setattr(agent, "guard_answer", lambda a, r, q: (a, {"replaced": False, "violations": [], "mode": None}))
        out = agent._guard("## 주가 동향\n오름세입니다.\n\n## 시장 상황\nシェア가 큽니다.", [], "기아 어때?")
        assert "오름세입니다." in out and "시장 상황" not in out and "シェア" not in out


class TestGuardShareUnit:
    RESULT = {"found": True, "company_name": "에코프로", "prices": [
        {"price_date": "2026-10-29", "close_price": 72000.0, "volume": 5000, "change_pct": 1.0},
        {"price_date": "2026-10-30", "close_price": 70900.0, "volume": 7000, "change_pct": -1.53}]}

    def test_price_difference_is_not_a_share_count(self):
        from app.answer_guard import unsupported_numbers

        assert [n.raw for n in unsupported_numbers("1,100주에 거래했습니다.", [self.RESULT])] == ["1,100주"]

    def test_price_difference_in_won_and_volume_difference_in_shares_still_pass(self):
        from app.answer_guard import unsupported_numbers

        assert unsupported_numbers("전일 대비 1,100원 내렸고 거래량은 2,000주 늘었습니다.", [self.RESULT]) == []

    def test_real_volume_passes(self):
        from app.answer_guard import unsupported_numbers

        assert unsupported_numbers("거래량은 7,000주입니다.", [self.RESULT]) == []


def test_tool_call_text_with_results_answers_with_code_summary(monkeypatch):
    calls = [{"function": {"name": "stock_tool", "arguments": {"ticker": "기아", "period_days": 1}}}]
    result = {"found": True, "company_name": "기아", "summary": "기아 요약입니다.", "prices": []}
    monkeypatch.setattr(agent, "_question_companies", lambda q: [])
    monkeypatch.setattr(agent, "_answer_briefing", lambda q, u: None)
    monkeypatch.setattr(agent, "_event_subject", lambda q, p: None)
    monkeypatch.setattr(agent, "_answer_concept", lambda q, c: None)
    monkeypatch.setattr(agent, "_first_call", lambda m, msgs, tools=None: ({"role": "assistant", "content": "", "tool_calls": calls}, calls))
    monkeypatch.setattr(agent, "_expand_multi_company_calls", lambda *a: None)
    monkeypatch.setattr(agent, "_repair_company_args", lambda *a: {})
    monkeypatch.setattr(agent, "_add_requested_tools", lambda *a: [])
    monkeypatch.setattr(agent, "_question_subjects", lambda *a: [])
    monkeypatch.setattr(agent, "_unresolved_company_answer", lambda *a: None)
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: result)
    monkeypatch.setattr(agent, "_llm_chat", lambda **kw: {"message": {"content": '조회합니다. {"name": "stock_tool", "arguments": {"ticker": "기아"}}'}})
    agent._reset_llm_state()
    response, _ = agent._answer("기아 어때?", "m")
    assert "기아 요약입니다." in response["answer"] and response["answer"] != agent.FALLBACK_ANSWER
