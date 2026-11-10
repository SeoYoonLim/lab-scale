"""40차(2026-11-09): 잔여 품질 개선의 단위 테스트(DB·외부 HTTP 없음).

- 시장 폭 집계: 전체 = 상승 + 하락 + 보합 + 등락률 미확인. 결측을 상승·하락·보합에 넣지 않고, 결측이 없으면 답 문장은 예전과 같다.
- 주문 응답 asset_type: 종목 마스터 값, 없으면 주식으로 가정하지 않고 null.
- test_etf_stage2 픽스처가 전망 경로를 끄는 이유(질문이 전망 질문으로도 분류됨)를 고정한다.
"""

import typing
from datetime import date, datetime

import pytest

from app import market_overview, outlook
from app.api.portfolio import OrderResponse
from app.briefing import KST

SAT = datetime(2026, 11, 7, 10, tzinfo=KST)
OLD_LINE = "- 같은 날 주식 300종목(수집 대상) 중 상승 67, 하락 230, 보합 3종목(2026-11-03 종가 기준)"


class FakeResult:
    def __init__(self, rows=None, scalar=None, first=None):
        self._rows, self._scalar, self._first = rows or [], scalar, first

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar

    def first(self):
        return self._first


class FakeDB:
    """market_overview.compute가 보내는 세 종류의 SQL에 정해 둔 값을 돌려준다."""

    def __init__(self, counts):
        self.counts = counts

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM market_index" in sql:
            closes = [2500.0 - i for i in range(21)]
            return FakeResult(rows=[(date(2026, 11, 3), c) for c in closes])
        if sql.startswith("SELECT max(p.price_date)"):
            return FakeResult(scalar=date(2026, 11, 3))
        return FakeResult(first=self.counts)


class TestMarketBreadth:
    def test_total_equals_up_down_flat_missing(self):
        # (상승, 하락, 보합, 등락률 없음, 그날 행 수, 등록된 주식 수)
        data = market_overview.compute(FakeDB((60, 220, 3, 7, 290, 300)))
        b = data["breadth"]
        assert (b["up"], b["down"], b["flat"], b["change_missing_count"], b["total"], b["no_price_count"]) == (60, 220, 3, 7, 290, 10)
        assert b["total"] == b["up"] + b["down"] + b["flat"] + b["change_missing_count"]

    def test_answer_mentions_missing_and_no_price_only_when_present(self):
        data = market_overview.compute(FakeDB((60, 220, 3, 7, 290, 300)))
        a = market_overview.answer(data, SAT)
        assert "상승 60, 하락 220, 보합 3종목, 등락률 미확인 7종목(2026-11-03 종가 기준)" in a
        assert "그날 시세가 없는 10종목은 집계에서 뺐습니다" in a

    def test_answer_is_unchanged_without_missing(self):
        full = market_overview.compute(FakeDB((67, 230, 3, 0, 300, 300)))
        assert full["breadth"]["change_missing_count"] == 0 and full["breadth"]["no_price_count"] == 0
        assert OLD_LINE in market_overview.answer(full, SAT).splitlines()
        # 이전 형식의 데이터(새 키 없음)도 같은 문장
        legacy = {"indexes": full["indexes"], "breadth": {"date": date(2026, 11, 3), "up": 67, "down": 230, "flat": 3, "total": 300}}
        assert OLD_LINE in market_overview.answer(legacy, SAT).splitlines()


class TestOrderAssetType:
    def test_field_is_optional_and_never_defaults_to_stock(self):
        field = OrderResponse.model_fields["asset_type"]
        assert field.default is None
        assert set(typing.get_args(typing.get_args(field.annotation)[0])) == {"stock", "etf"}
        base = {"ticker": "Q00001", "company_name": "x", "side": "buy", "quantity": 1, "price": 1.0,
                "executed_at": "2026-11-09T00:00:00+00:00", "cash_balance": 1.0, "holding": None}
        assert OrderResponse(**base).asset_type is None  # 예전 결과(필드 없음)는 null
        assert OrderResponse(**base, asset_type="etf").asset_type == "etf"
        with pytest.raises(ValueError):
            OrderResponse(**base, asset_type="bond")


def test_etf_stage2_question_is_also_a_forecast_question():
    """test_etf_stage2의 마스킹 테스트 질문은 전망 질문으로도 분류된다(그래서 시세가 있는 DB에서는 전망 코드 경로가 먼저 답했다)."""
    assert outlook.is_forecast_question("KODEX 레버리지 위험해?")
    assert not outlook.is_forecast_question("KODEX 200 최근 흐름 알려줘")
