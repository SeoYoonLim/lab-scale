"""(A) 보유 시세 범위 밖 기간 처리: app/period_scope.py(순수)와 app/agent.py의 연결(dev DB integration)."""

from datetime import date

import pytest

from app import agent
from app.period_scope import RequestedPeriod, Scope, classify, outside_answer, partial_answer, requested_period
from evals.metrics import is_refusal, match_numbers, period_label_violation

TODAY = date(2026, 10, 27)
DS, DE = date(2026, 6, 26), date(2026, 10, 14)


@pytest.mark.parametrize("q, start, end", [
    ("삼성전자 2024년 3월 주가 알려줘", date(2024, 3, 1), date(2024, 3, 31)),
    ("삼성전자 2023년 주가", date(2023, 1, 1), date(2023, 12, 31)),
    ("현대차 2026년 3월 15일 종가", date(2026, 3, 15), date(2026, 3, 15)),
    ("현대차 2026-03-15 종가", date(2026, 3, 15), date(2026, 3, 15)),
    ("NAVER 작년 이맘때 주가", date(2025, 10, 27), date(2025, 10, 27)),
    ("NAVER 작년 주가", date(2025, 1, 1), date(2025, 12, 31)),
    ("NAVER 재작년 주가", date(2024, 1, 1), date(2024, 12, 31)),
    ("카카오 3년 전 주가", date(2023, 10, 27), date(2023, 10, 27)),
    ("카카오 6개월 전 주가", date(2026, 4, 27), date(2026, 4, 27)),
    ("LG전자 6월 주가", date(2026, 6, 1), date(2026, 6, 30)),
    ("LG전자 11월 주가", date(2025, 11, 1), date(2025, 11, 30)),  # 아직 안 온 달은 작년
    ("셀트리온 2026년 1월부터 등락률", date(2026, 1, 1), TODAY),
    ("셀트리온 올해 1월부터 지금까지 등락률", date(2026, 1, 1), TODAY),
    ("현대차 1년 주가 추이", date(2025, 10, 27), TODAY),  # parse_period의 상대 기간
])
def test_requested_period(q, start, end):
    r = requested_period(q, TODAY)
    assert (r.start, r.end) == (start, end)


@pytest.mark.parametrize("q", ["삼성전자 주가 알려줘", "삼성전자 월요일 주가", "삼성전자 3개월 흐름 1위"])
def test_no_or_relative_period_is_not_explicit_out_of_range(q):
    assert classify(requested_period(q, TODAY), DS, DE) is None


@pytest.mark.parametrize("start, end, kind", [
    (date(2024, 3, 1), date(2024, 3, 31), "outside"),
    (date(2026, 6, 1), date(2026, 6, 30), "partial"),
    (date(2026, 9, 1), date(2026, 9, 30), None),
    (date(2026, 10, 27), date(2026, 10, 27), None),  # 보유 범위 뒤(수집 전 최근)는 기존 흐름
])
def test_classify(start, end, kind):
    s = classify(RequestedPeriod(start, end, "x"), DS, DE)
    assert (s.kind if s else None) == kind


def test_outside_answer_is_refusal_and_never_labels_the_value_with_the_period():
    scope = Scope("outside", RequestedPeriod(date(2024, 3, 1), date(2024, 3, 31), "2024년 3월"), DS, DE)
    text = outside_answer("삼성전자", scope, {"price_date": "2026-10-14", "close_price": 235000.0})
    assert "제공하지 않습니다" in text and "2026-06-26부터 2026-10-14까지" in text and "요청하신 기간의 값이 아닙니다" in text
    assert is_refusal(text)
    assert period_label_violation("삼성전자 2024년 3월 주가", text, DS, TODAY) is False
    _, unmatched = match_numbers(text, [{"close_price": 235000.0}])
    assert unmatched == []


def test_partial_answer_names_the_missing_part():
    scope = Scope("partial", RequestedPeriod(date(2026, 6, 1), date(2026, 6, 30), "2026년 6월"), DS, DE)
    prices = [{"price_date": "2026-06-26"}, {"price_date": "2026-06-30"}]
    text = partial_answer("LG전자", scope, prices, "요약 문장")
    assert "2026-06-01~2026-06-25 구간은 시세 데이터가 없습니다" in text
    assert "(2026-06-26~2026-06-30)만의 값" in text and text.endswith("요약 문장")
    assert is_refusal(text)


def test_partial_answer_mentions_uncollected_tail():
    scope = Scope("partial", RequestedPeriod(date(2026, 1, 1), TODAY, "올해"), DS, DE)
    text = partial_answer("셀트리온", scope, [{"price_date": "2026-06-26"}, {"price_date": "2026-10-14"}], "s")
    assert "2026-10-14 이후는 아직 수집되지 않았습니다" in text


class Co:
    def __init__(self, name, asset_type="stock", id=1, ticker="000001"):
        self.name, self.asset_type, self.id, self.ticker = name, asset_type, id, ticker


def test_agent_scope_requires_one_company_and_price_intent():
    assert agent._answer_period_scope("2024년 3월 주가", [], TODAY) is None
    assert agent._answer_period_scope("2024년 3월 주가", [Co("A"), Co("B", id=2)], TODAY) is None
    assert agent._answer_period_scope("삼성전자 2024년 실적 뉴스", [Co("삼성전자")], TODAY) is None  # 시세 질문이 아님


@pytest.mark.integration
class TestAgentScopeDevDB:
    def _companies(self, dev_db, q):
        from app.tools.company_resolver import extract_from_text

        return extract_from_text(dev_db, q)

    def _first_price_date(self, dev_db, ticker: str) -> date:
        from sqlalchemy import text

        d = dev_db.execute(text("SELECT min(p.price_date) FROM stock_price p JOIN company c ON c.id = p.company_id "
                                "WHERE c.ticker = :t"), {"t": ticker}).scalar()
        dev_db.rollback()
        return d

    # 질문의 달은 DB 시세 시작일에서 정한다(2026-10-29 3년치 백필 뒤로 시작일이 2026-06-26에서 2023-10-30으로 바뀌었다).
    @pytest.mark.needs_collected_data
    def test_outside_answer_uses_db_range_and_latest_close(self, dev_db):
        first = self._first_price_date(dev_db, "005930")
        q = f"삼성전자 {first.year - 1}년 3월 주가 알려줘"  # 시세 시작일보다 앞선 달
        resp, records = agent._answer_period_scope(q, self._companies(dev_db, q), TODAY)
        r = records[0]["result"]
        assert r["scope"] == "outside" and r["called_via"] == "period_scope"
        assert f"{r['data_start']}부터 {r['data_end']}까지" in resp["answer"]
        assert f"{r['latest']['close_price']:,.0f}원" in resp["answer"]
        assert period_label_violation(q, resp["answer"], date.fromisoformat(r["data_start"]), TODAY) is False

    @pytest.mark.needs_collected_data
    def test_partial_answer_values_come_from_overlap_rows(self, dev_db):
        first = self._first_price_date(dev_db, "066570")
        assert first.day > 1  # 시작일이 그 달 1일보다 뒤여야 "일부만 겹침"이 된다
        q = f"LG전자 {first.year}년 {first.month}월 주가 흐름 알려줘"
        resp, records = agent._answer_period_scope(q, self._companies(dev_db, q), TODAY)
        r = records[0]["result"]
        assert r["scope"] == "partial"
        month = f"{first.year}-{first.month:02d}"
        assert all(p["price_date"].startswith(month) for p in r["prices"])
        matched, unmatched = match_numbers(resp["answer"], [r, q])
        assert matched and unmatched == []

    @pytest.mark.parametrize("q", ["삼성전자 2026년 9월 주가", "삼성전자 3개월 주가 추이", "삼성전자 오늘 주가", "삼성전자 주가 알려줘"])
    def test_in_range_or_no_period_goes_to_existing_flow(self, dev_db, q):
        assert agent._answer_period_scope(q, self._companies(dev_db, q), TODAY) is None
