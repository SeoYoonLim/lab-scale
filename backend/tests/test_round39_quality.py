"""39차(2026-11-09): 종합 진단에서 고친 문제의 회귀 테스트(DB·외부 HTTP·LLM 없음).

1. 뉴스·공시 도구가 UTC 문자열을 내보내 LLM·ETF 뉴스 줄·근거 우선 답변·답변 검증기가 하루 이른 날짜를 쓰던 문제
   → 도구 출력은 같은 순간의 KST 문자열(app.display_dates.kst_iso), 앞 10자를 쓰던 곳은 kst_day.
2. ETF look-through 요약이 ETF별 잔여를 부호째 상쇄해 미확인 노출·초과분이 작게 나오던 문제.
3. 주가 흐름 계산(수익률·이동평균·거래량 배수·기간 최고최저)을 제품 코드와 다른 독립 계산식으로 대조.
"""

import statistics
from datetime import date, datetime, timedelta, timezone

import pytest

from app import price_flow
from app.answer_guard import date_violations
from app.display_dates import KST, kst_day, kst_iso
from app.etf import compose_etf_answer
from app.evidence_answer import _item
from app.exposure import ConstituentIn, EtfIn, HoldingIn
from app.look_through import compute as look_through

UTC_EARLY_KST = "2026-10-27T23:30:00+00:00"  # KST 2026-10-28 08:30
KST_SAME = "2026-10-28T08:30:00+09:00"
DISCLOSURE_UTC = "2026-10-27T15:00:00+00:00"  # 공시 저장 방식(KST 자정)


# ---------- 1. 날짜 ----------


class TestKstConversion:
    @pytest.mark.parametrize("value, expected", [
        ("2026-11-08T15:00:00Z", "2026-11-09"), ("2026-11-08T14:59:00Z", "2026-11-08"),
        ("2026-11-08T15:00:00+00:00", "2026-11-09"), ("2026-11-09T00:00:00+09:00", "2026-11-09"),
        ("2026-11-08", "2026-11-08"),  # 날짜만 있는 값은 그대로
        ("2026-11-08T23:30:00", "2026-11-08"),  # 시간대 없는 값은 바꾸지 않는다
        (datetime(2026, 11, 8, 23, 30), "2026-11-08"),
    ])
    def test_kst_day_examples(self, value, expected):
        assert kst_day(value) == expected

    def test_kst_iso_keeps_instant_and_never_invents_timezone(self):
        utc = datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc)
        assert kst_iso(utc) == "2026-11-09T00:00:00+09:00" and datetime.fromisoformat(kst_iso(utc)) == utc
        assert kst_iso(datetime(2026, 11, 8, 15, 0)) == "2026-11-08T15:00:00"
        assert kst_iso(None) is None

    def test_tools_use_kst_iso(self):
        """뉴스·공시·RAG 도구가 DB 시각을 kst_iso로 쓴다(.isoformat() 그대로 쓰면 UTC 문자열)."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "app" / "tools"
        for name, n in (("news_tool.py", 1), ("disclosure_tool.py", 1), ("rag_search_tool.py", 2)):
            src = (root / name).read_text(encoding="utf-8")
            assert src.count("kst_iso(row.") == n, name
            assert "_at.isoformat()" not in src, name


class TestDatesInAnswers:
    def test_etf_news_line_uses_korean_date_for_old_and_new_strings(self):
        for published in (UTC_EARLY_KST, KST_SAME):
            text = compose_etf_answer("", [], [{"found": True, "news": [{"title": "KODEX 200 순자산 증가", "source": "연합",
                                                                         "published_at": published}]}])
            assert "(연합, 2026-10-28)" in text and "2026-10-27" not in text

    def test_evidence_item_date(self):
        assert _item("삼성전자", "t", "c", DISCLOSURE_UTC, "DART", "disclosure")["date"] == "2026-10-28"
        assert _item("삼성전자", "t", "c", "20261028", "DART", "disclosure")["date"] == "20261028"  # rcept_dt는 그대로

    def test_guard_knows_korean_date_from_kst_tool_result(self):
        """도구 결과가 KST 문자열이면 검증기는 한국 날짜를 '아는 날짜'로 본다(ETF 뉴스 줄이 다시 검증을 거쳐도 지워지지 않는다)."""
        answer = "KODEX 200 순자산 증가 기사(2026-10-28)가 있습니다."
        assert date_violations(answer, [{"found": True, "news": [{"title": "x", "published_at": KST_SAME}]}]) == []
        # 같은 순간을 UTC로 주면(39차 이전 도구 출력) 한국 날짜를 모르는 날짜로 잡는다: 도구 출력을 바꾼 이유
        assert date_violations(answer, [{"found": True, "news": [{"title": "x", "published_at": UTC_EARLY_KST}]}]) != []


# ---------- 2. ETF look-through 잔여 ----------


def _etf(ticker, weights):
    return EtfIn(ticker=ticker, name=f"TEST ETF {ticker}", as_of=date(2026, 11, 6),
                 constituents=[ConstituentIn(name=f"구성{ticker}{i}", code=None, weight_pct=w) for i, w in enumerate(weights)])


class TestLookThroughResidual:
    NOW = datetime(2026, 11, 9, 10, tzinfo=KST)

    def run(self, etfs, cash=0.0):
        holdings = [HoldingIn(ticker=t, name=f"TEST ETF {t}", asset_type="etf", quantity=1, price=1_000_000.0,
                              price_date=date(2026, 11, 6)) for t in etfs]
        return look_through(holdings, etfs, cash, self.NOW, date(2026, 11, 6))["summary"]

    def test_over_and_under_allocated_etfs_do_not_cancel(self):
        s = self.run({"Q00001": _etf("Q00001", [60, 50]), "Q00002": _etf("Q00002", [50, 40])}, cash=500_000.0)
        # 독립 계산: A는 110% → 초과 100,000원, B는 90% → 잔여(미확인) 100,000원. 순합계는 0
        assert s["residual_amount"] == 0.0
        assert s["over_allocated_amount"] == 100_000.0  # 39차 이전: 0.0
        assert s["unidentified_amount"] == 100_000.0  # 39차 이전: 0.0
        assert s["unidentified_weight_pct"] == round(100_000 / 2_500_000 * 100, 2)
        # 항등식: 기업 노출 + 미확인 - 초과분 + 현금 = 총자산
        assert s["companies_amount"] + s["unidentified_amount"] - s["over_allocated_amount"] + s["cash_balance"] == s["total_asset"]

    @pytest.mark.parametrize("weights, unidentified, over", [([60, 30], 100_000.0, 0.0), ([70, 40], 0.0, 100_000.0), ([50, 50], 0.0, 0.0)])
    def test_single_etf_is_unchanged(self, weights, unidentified, over):
        s = self.run({"Q00003": _etf("Q00003", weights)})
        assert (s["unidentified_amount"], s["over_allocated_amount"]) == (unidentified, over)
        assert str(s["over_allocated_amount"]) != "-0.0"


# ---------- 3. 주가 흐름 계산: 독립 계산식과 대조 ----------


def _fixture_rows(n=30):
    """휴장일이 끼어 날짜가 건너뛰는 거래일 시세(행 = 거래일). 종가·거래량은 규칙 없이 정한 값."""
    closes = [100, 103, 101, 98, 105, 110, 107, 111, 115, 112, 108, 109, 113, 120, 118, 121, 125, 119, 117, 122,
              126, 124, 129, 131, 128, 133, 130, 136, 140, 138][:n]
    vols = [1000 + (i * 37) % 400 for i in range(n)]
    vols[-1] = 5000  # 마지막 날 급증
    days, d = [], date(2026, 9, 1)
    while len(days) < n:
        if d.weekday() < 5 and d not in (date(2026, 9, 24), date(2026, 9, 25), date(2026, 10, 9)):  # 추석·한글날 휴장
            days.append(d)
        d += timedelta(days=1)
    return list(zip(days, closes, vols))


class TestPriceFlowAgainstIndependentFormula:
    def test_returns_moving_averages_volume_and_window(self):
        rows = _fixture_rows()
        closes = [c for _, c, _ in rows]
        out = price_flow.compute(rows, {"returns": (1, 5, 20), "mas": (5, 20), "volume_avg": 20, "window": 20})
        assert out["base_date"] == rows[-1][0].isoformat() and out["close"] == 138
        for n in (1, 5, 20):
            expected = round((closes[-1] - closes[-1 - n]) / closes[-1 - n] * 100, 2)
            assert out["returns"][n]["pct"] == expected
            assert out["returns"][n]["from_date"] == rows[-1 - n][0].isoformat()  # 휴장일이 아니라 n거래일 전 날짜
        for n in (5, 20):
            ma = statistics.mean(closes[-n:])
            assert out["mas"][n]["value"] == round(ma, 2)
            assert out["mas"][n]["gap_pct"] == round((closes[-1] / ma - 1) * 100, 2)
        prior = [v for _, _, v in rows][-21:-1]  # 당일 제외 직전 20거래일
        assert out["volume"]["avg"] == round(statistics.mean(prior)) and out["volume"]["ratio"] == round(5000 / statistics.mean(prior), 2)
        inside = rows[-20:]  # 기간 20거래일(비교 기준일 제외)
        assert out["window"]["high"] == max(c for _, c, _ in inside) and out["window"]["low"] == min(c for _, c, _ in inside)
        assert out["window"]["from_date"] == rows[-21][0].isoformat()

    def test_short_history_returns_none_instead_of_inventing(self):
        out = price_flow.compute(_fixture_rows(4), {"returns": (1, 5, 20), "mas": (5, 20), "volume_avg": 20, "window": 20})
        assert out["returns"][1] is not None and out["returns"][5] is None and out["returns"][20] is None
        assert out["mas"][5] is None and out["mas"][20] is None
        assert out["volume"]["avg"] is None
        assert out["window"]["days"] == 3  # 있는 만큼만(요청 20)
