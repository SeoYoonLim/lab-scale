"""진단 이력의 순수 로직(DB 없음): 이력 항목 요약, 두 진단의 비교, 저장 실패 처리. DB 동작은 test_diagnosis_history_devdb.py."""

import logging
from datetime import datetime, timezone

import app.diagnosis_history as h
from app.diagnosis import HoldingInput, compute_flags, compute_portfolio

T1 = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)


def samsung(price=270_000.0, qty=10, ret=-2.0):
    return HoldingInput("005930", "삼성전자", "KOSPI", qty, 280_000.0, price, True, ret)


def kakao(price=45_000.0, qty=20, ret=5.0):
    return HoldingInput("035720", "카카오", "KOSPI", qty, 45_000.0, price, True, ret)


def eco(price=150_000.0, qty=5, ret=None):
    return HoldingInput("247540", "에코프로비엠", "KOSDAQ", qty, 150_000.0, price, True, ret)


def make(id_, created_at, cash, holdings, source="llm", summary="요약 문장입니다."):
    """진단 응답 본문(필요한 키만)을 실제 계산 함수로 만들고 저장된 record 모양으로 감싼다."""
    items, metrics = compute_portfolio(cash, holdings)
    flags = compute_flags(items, metrics)
    result = {"source": source, "summary": summary, "metrics": metrics, "holdings": items, "flags": flags}
    return {"id": id_, "created_at": created_at, "source": source, "total_asset": metrics["total_asset"],
            "holdings_count": metrics["holding_count"], "result": result}


def by_ticker(rows):
    return {r["ticker"]: r for r in rows}


class TestHistoryItem:
    def test_shape_and_values(self):
        rec = make(5, T1, 4_000_000.0, [samsung(), kakao(price=30_000.0)])
        item = h.history_item(rec)
        assert set(item) == {"id", "created_at", "source", "total_asset", "holdings_count", "flags", "summary_preview"}
        assert item["id"] == 5 and item["created_at"] == T1.isoformat() and item["source"] == "llm"
        assert item["total_asset"] == rec["total_asset"] == 7_300_000.0 and item["holdings_count"] == 2
        assert item["summary_preview"] == "요약 문장입니다."

    def test_flags_are_name_only_summary(self):
        rec = make(5, T1, 4_000_000.0, [samsung(), kakao(price=30_000.0)])
        flags = h.history_item(rec)["flags"]
        assert {f["code"] for f in flags} == {"FEW_HOLDINGS", "BIG_LOSS", "MARKET_SKEW"}
        for f in flags:
            assert set(f) == {"code", "ticker", "company_name", "market"}  # message/value/threshold는 뺀다
        assert by_ticker([f for f in flags if f["code"] == "BIG_LOSS"])["035720"]["company_name"] == "카카오"
        assert [f["market"] for f in flags if f["code"] == "MARKET_SKEW"] == ["KOSPI"]

    def test_summary_is_cut_to_80_chars(self):
        rec = make(1, T1, 4_000_000.0, [samsung()], summary="가" * 200)
        assert h.history_item(rec)["summary_preview"] == "가" * 80
        short = make(1, T1, 4_000_000.0, [samsung()], summary="가" * 80)
        assert h.history_item(short)["summary_preview"] == "가" * 80

    def test_missing_summary_and_flags_do_not_break(self):
        rec = {"id": 1, "created_at": T1, "source": "rule_based", "total_asset": 1.0, "holdings_count": 0, "result": {}}
        item = h.history_item(rec)
        assert item["flags"] == [] and item["summary_preview"] == ""


class TestFirstDiagnosis:
    def test_no_previous_gives_null_previous_and_changes(self):
        cur = make(1, T1, 4_000_000.0, [samsung()])
        out = h.build_comparison(cur, None)
        assert out == {"current": h.history_item(cur), "previous": None, "changes": None}


class TestTotalAsset:
    def test_increase(self):
        prev = make(1, T1, 4_000_000.0, [samsung(price=270_000.0)])   # 4,000,000 + 2,700,000 = 6,700,000
        cur = make(2, T2, 4_000_000.0, [samsung(price=283_500.0)])    # 4,000,000 + 2,835,000 = 6,835,000
        c = h.build_comparison(cur, prev)
        assert c["previous"]["id"] == 1 and c["current"]["id"] == 2
        assert c["changes"]["total_asset"] == {
            "previous": 6_700_000.0, "current": 6_835_000.0, "change_amount": 135_000.0, "change_pct": 2.01,
        }

    def test_decrease_is_negative(self):
        prev = make(1, T1, 4_000_000.0, [samsung(price=300_000.0)])   # 7,000,000
        cur = make(2, T2, 4_000_000.0, [samsung(price=240_000.0)])    # 6,400,000
        t = h.build_comparison(cur, prev)["changes"]["total_asset"]
        assert t["change_amount"] == -600_000.0 and t["change_pct"] == -8.57

    def test_no_change_is_zero(self):
        prev = make(1, T1, 4_000_000.0, [samsung()])
        cur = make(2, T2, 4_000_000.0, [samsung()])
        t = h.build_comparison(cur, prev)["changes"]["total_asset"]
        assert t["change_amount"] == 0 and t["change_pct"] == 0

    def test_zero_previous_asset_gives_null_pct(self):
        prev = make(1, T1, 0.0, [samsung(price=0.0)])
        cur = make(2, T2, 1_000_000.0, [samsung(price=0.0)])
        t = h.build_comparison(cur, prev)["changes"]["total_asset"]
        assert t["change_amount"] == 1_000_000.0 and t["change_pct"] is None


class TestHoldingWeights:
    def setup_method(self):
        # prev: 총자산 10,000,000 = 현금 5,000,000 + 삼성전자 3,000,000(30%) + 카카오 2,000,000(20%)
        self.prev = make(1, T1, 5_000_000.0, [samsung(price=300_000.0), kakao(price=100_000.0)])

    def test_added_removed_and_kept(self):
        # cur: 총자산 10,000,000 = 현금 4,000,000 + 삼성전자 3,500,000(35%) + 에코프로비엠 2,500,000(25%), 카카오 전량 제외
        cur = make(2, T2, 4_000_000.0, [samsung(price=350_000.0), eco(price=500_000.0)])
        rows = by_ticker(h.build_comparison(cur, self.prev)["changes"]["holdings"])
        assert set(rows) == {"005930", "035720", "247540"}
        assert rows["005930"] == {
            "ticker": "005930", "company_name": "삼성전자", "status": "kept",
            "previous_weight_pct": 30.0, "current_weight_pct": 35.0, "weight_change_pp": 5.0,
        }
        assert rows["247540"] == {
            "ticker": "247540", "company_name": "에코프로비엠", "status": "added",
            "previous_weight_pct": None, "current_weight_pct": 25.0, "weight_change_pp": 25.0,
        }
        assert rows["035720"] == {
            "ticker": "035720", "company_name": "카카오", "status": "removed",
            "previous_weight_pct": 20.0, "current_weight_pct": None, "weight_change_pp": -20.0,
        }

    def test_sorted_by_size_of_change(self):
        cur = make(2, T2, 4_000_000.0, [samsung(price=350_000.0), eco(price=500_000.0)])
        rows = h.build_comparison(cur, self.prev)["changes"]["holdings"]
        assert [r["ticker"] for r in rows] == ["247540", "035720", "005930"]  # +25, -20, +5

    def test_same_holdings_are_all_kept_with_zero_change(self):
        cur = make(2, T2, 5_000_000.0, [samsung(price=300_000.0), kakao(price=100_000.0)])
        rows = h.build_comparison(cur, self.prev)["changes"]["holdings"]
        assert {r["status"] for r in rows} == {"kept"} and {r["weight_change_pp"] for r in rows} == {0.0}

    def test_unpriced_holding_has_unknown_change(self):
        """현재가를 못 구한 종목은 비중이 null이라 변화도 null이고, 목록의 맨 뒤로 간다."""
        cur = make(2, T2, 5_000_000.0, [samsung(price=None), kakao(price=100_000.0, qty=10)])
        rows = h.build_comparison(cur, self.prev)["changes"]["holdings"]
        assert rows[-1]["ticker"] == "005930" and rows[-1]["status"] == "kept"
        assert rows[-1]["current_weight_pct"] is None and rows[-1]["weight_change_pp"] is None
        # 카카오: 총자산이 10,000,000 → 6,000,000(삼성전자 평가금액이 빠짐)이라 20% → 1,000,000 / 6,000,000 = 16.67%
        assert by_ticker(rows)["035720"]["current_weight_pct"] == 16.67
        assert by_ticker(rows)["035720"]["weight_change_pp"] == -3.33

    def test_completely_different_portfolio(self):
        cur = make(2, T2, 5_000_000.0, [eco(price=1_000_000.0)])
        rows = by_ticker(h.build_comparison(cur, self.prev)["changes"]["holdings"])
        assert {t: r["status"] for t, r in rows.items()} == {"005930": "removed", "035720": "removed", "247540": "added"}


class TestFlags:
    def test_added_and_resolved(self):
        # prev: 종목 1개 + 현금 부족 + 집중 → FEW_HOLDINGS, CONCENTRATION_HIGH, MARKET_SKEW, CASH_LOW 등
        prev = make(1, T1, 100_000.0, [samsung(price=300_000.0, qty=10)])
        # cur: 종목 3개로 분산, 현금 충분. 카카오는 -20%라 BIG_LOSS가 새로 생긴다
        cur = make(2, T2, 2_000_000.0, [
            samsung(price=300_000.0, qty=2), kakao(price=36_000.0, qty=10), eco(price=150_000.0, qty=4),
        ])
        prev_codes = {(f["code"], f.get("ticker"), f.get("market")) for f in prev["result"]["flags"]}
        cur_codes = {(f["code"], f.get("ticker"), f.get("market")) for f in cur["result"]["flags"]}
        assert ("FEW_HOLDINGS", None, None) in prev_codes and ("BIG_LOSS", "035720", None) in cur_codes

        flags = h.build_comparison(cur, prev)["changes"]["flags"]
        added = {(f["code"], f["ticker"], f["market"]) for f in flags["added"]}
        resolved = {(f["code"], f["ticker"], f["market"]) for f in flags["resolved"]}
        assert added == cur_codes - prev_codes and resolved == prev_codes - cur_codes
        assert ("BIG_LOSS", "035720", None) in added
        assert ("FEW_HOLDINGS", None, None) in resolved and ("CONCENTRATION_HIGH", None, None) in resolved
        # 양쪽에 모두 있는 플래그는 어느 쪽에도 나오지 않는다
        assert not (prev_codes & cur_codes) & (added | resolved)

    def test_flag_entries_have_full_detail(self):
        prev = make(1, T1, 4_000_000.0, [samsung(price=280_000.0), kakao(price=45_000.0), eco()])
        cur = make(2, T2, 4_000_000.0, [samsung(price=280_000.0), kakao(price=36_000.0), eco()])
        (added,) = h.build_comparison(cur, prev)["changes"]["flags"]["added"]
        assert added == {
            "code": "BIG_LOSS", "message": "카카오의 손익률이 -20.0%로 기준(-10%) 이하입니다.",
            "value": -20.0, "threshold": -10.0, "ticker": "035720", "company_name": "카카오", "market": None,
        }

    def test_same_code_for_different_tickers_is_a_different_flag(self):
        """BIG_LOSS는 종목마다 다른 플래그다: 카카오 손실이 해소되고 삼성전자 손실이 새로 생기면 각각 해소/신규."""
        prev = make(1, T1, 4_000_000.0, [samsung(price=280_000.0), kakao(price=36_000.0), eco()])
        cur = make(2, T2, 4_000_000.0, [samsung(price=240_000.0), kakao(price=45_000.0), eco()])
        flags = h.build_comparison(cur, prev)["changes"]["flags"]
        assert [(f["code"], f["ticker"]) for f in flags["added"]] == [("BIG_LOSS", "005930")]
        assert [(f["code"], f["ticker"]) for f in flags["resolved"]] == [("BIG_LOSS", "035720")]

    def test_no_flags_either_side(self):
        prev = make(1, T1, 4_000_000.0, [samsung(price=280_000.0), kakao(price=45_000.0), eco()])
        cur = make(2, T2, 4_000_000.0, [samsung(price=280_000.0), kakao(price=45_000.0), eco()])
        assert h.build_comparison(cur, prev)["changes"]["flags"] == {"added": [], "resolved": []}

    def test_old_stored_flag_without_optional_keys_does_not_break(self):
        prev = make(1, T1, 4_000_000.0, [samsung()])
        prev["result"]["flags"] = [{"code": "OLD_RULE", "message": "m", "value": 1, "threshold": 2}]
        cur = make(2, T2, 4_000_000.0, [samsung()])
        resolved = h.build_comparison(cur, prev)["changes"]["flags"]["resolved"]
        assert resolved[0]["code"] == "OLD_RULE" and resolved[0]["ticker"] is None


class TestReturns20d:
    def test_change_only_for_tickers_with_both_values(self):
        prev = make(1, T1, 5_000_000.0, [samsung(ret=-2.59), kakao(ret=-11.35), eco(ret=None)])
        cur = make(2, T2, 5_000_000.0, [samsung(ret=3.41), kakao(ret=None), eco(ret=18.56)])
        rows = h.build_comparison(cur, prev)["changes"]["returns_20d"]
        assert rows == [{
            "ticker": "005930", "company_name": "삼성전자",
            "previous_return_20d_pct": -2.59, "current_return_20d_pct": 3.41, "change_pp": 6.0,
        }]

    def test_added_and_removed_tickers_are_not_included(self):
        prev = make(1, T1, 5_000_000.0, [samsung(ret=1.0), kakao(ret=2.0)])
        cur = make(2, T2, 5_000_000.0, [samsung(ret=4.0), eco(ret=9.0)])
        rows = h.build_comparison(cur, prev)["changes"]["returns_20d"]
        assert [r["ticker"] for r in rows] == ["005930"] and rows[0]["change_pp"] == 3.0

    def test_zero_return_is_a_value_not_missing(self):
        prev = make(1, T1, 5_000_000.0, [samsung(ret=0.0)])
        cur = make(2, T2, 5_000_000.0, [samsung(ret=-1.5)])
        assert h.build_comparison(cur, prev)["changes"]["returns_20d"][0]["change_pp"] == -1.5

    def test_float_noise_is_rounded(self):
        prev = make(1, T1, 5_000_000.0, [samsung(ret=-2.59)])
        cur = make(2, T2, 5_000_000.0, [samsung(ret=18.56)])
        assert h.build_comparison(cur, prev)["changes"]["returns_20d"][0]["change_pp"] == 21.15

    def test_sorted_by_size_of_change(self):
        prev = make(1, T1, 5_000_000.0, [samsung(ret=0.0), kakao(ret=0.0)])
        cur = make(2, T2, 5_000_000.0, [samsung(ret=1.0), kakao(ret=-5.0)])
        rows = h.build_comparison(cur, prev)["changes"]["returns_20d"]
        assert [r["ticker"] for r in rows] == ["035720", "005930"]

    def test_no_return_data_gives_empty_list(self):
        prev = make(1, T1, 5_000_000.0, [samsung(ret=None)])
        cur = make(2, T2, 5_000_000.0, [samsung(ret=None)])
        assert h.build_comparison(cur, prev)["changes"]["returns_20d"] == []


class TestChangesKeys:
    def test_changes_has_exactly_the_documented_keys(self):
        prev = make(1, T1, 5_000_000.0, [samsung()])
        cur = make(2, T2, 5_000_000.0, [samsung()])
        changes = h.build_comparison(cur, prev)["changes"]
        assert set(changes) == {"total_asset", "holdings", "flags", "returns_20d"}
        assert set(changes["total_asset"]) == {"previous", "current", "change_amount", "change_pct"}
        assert set(changes["holdings"][0]) == {
            "ticker", "company_name", "status", "previous_weight_pct", "current_weight_pct", "weight_change_pp",
        }


class FailingSession:
    def __init__(self, error):
        self.error = error
        self.rolled_back = False

    def add(self, row):
        pass

    def commit(self):
        raise self.error

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class TestSaveNeverRaises:
    BODY = {"source": "llm", "metrics": {"total_asset": 1.0, "holding_count": 1},
            "summary": "비밀스러운 포트폴리오 내용 삼성전자 10주"}

    def test_db_failure_returns_none_and_logs_only_the_error_type(self, monkeypatch, caplog):
        # SQLAlchemy 예외의 str()에는 INSERT 파라미터(포트폴리오 내용)가 들어간다 → 로그에 새지 않아야 한다
        err = RuntimeError("INSERT ... [parameters: ('llm', '비밀스러운 포트폴리오 내용 삼성전자 10주')]")
        session = FailingSession(err)
        monkeypatch.setattr(h, "SessionLocal", lambda: session)
        with caplog.at_level(logging.DEBUG, logger="app.diagnosis_history"):
            assert h.save_diagnosis(7, self.BODY) is None
        assert session.rolled_back
        text = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
        assert "RuntimeError" in text and "user_id=7" in text
        assert "비밀스러운" not in text and "삼성전자" not in text
        assert all(r.exc_info is None for r in caplog.records)

    def test_cannot_connect_returns_none(self, monkeypatch):
        def boom():
            raise ConnectionError("db down")

        monkeypatch.setattr(h, "SessionLocal", boom)
        assert h.save_diagnosis(7, self.BODY) is None

    def test_missing_key_in_body_returns_none(self, monkeypatch):
        monkeypatch.setattr(h, "SessionLocal", lambda: FailingSession(RuntimeError()))
        assert h.save_diagnosis(7, {}) is None


def test_limits():
    assert h.MAX_KEPT_PER_USER == 50 and h.SUMMARY_PREVIEW_LEN == 80

