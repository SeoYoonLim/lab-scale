"""app/diagnosis.py 순수 로직(DB/LLM 없음): 지표·플래그 계산, 숫자 검증, LLM 출력 검증, 폴백 템플릿, 재시도 흐름."""

import json
from decimal import Decimal

import pytest

import app.diagnosis as d
from app.diagnosis import (
    BIG_LOSS_PCT,
    CASH_HIGH_PCT,
    CASH_LOW_PCT,
    CONCENTRATION_TOP1_PCT,
    MARKET_SKEW_PCT,
    MIN_HOLDING_COUNT,
    HoldingInput,
    allowed_numbers,
    build_diagnosis,
    build_llm_payload,
    compute_flags,
    compute_portfolio,
    explain_with_llm,
    find_disallowed_numbers,
    normalize_market,
    rule_based_text,
    validate_llm_output,
)
from app.disclaimer import DISCLAIMER


def H(ticker="005930", name="삼성전자", market="KOSPI", qty=10, avg=100.0, cur=100.0, rt=True, r20=None):
    return HoldingInput(ticker, name, market, qty, avg, cur, rt, r20)


def codes(flags):
    return [f["code"] for f in flags]


class TestComputePortfolio:
    def test_single_holding(self):
        items, m = compute_portfolio(1000, [H(qty=10, avg=100, cur=110, r20=3.456)])
        i = items[0]
        assert (i["eval_amount"], i["profit_loss"], i["profit_loss_pct"]) == (1100, 100, 10.0)
        assert i["weight_pct"] == pytest.approx(52.38)
        assert i["return_20d_pct"] == 3.46 and i["price_unavailable"] is False
        assert m["total_asset"] == 2100 and m["stock_eval_amount"] == 1100
        assert m["cash_weight_pct"] == pytest.approx(47.62)
        assert m["holding_count"] == 1 and m["priced_holding_count"] == 1
        assert m["top1_weight_pct"] == m["top3_weight_pct"] == pytest.approx(52.38)
        assert m["herfindahl_index"] == 1.0
        assert m["market_weights_pct"] == {"KOSPI": 100.0}
        assert m["total_profit_loss"] == 100 and m["total_profit_loss_pct"] == 10.0
        assert m["best_holding"] == m["worst_holding"] == {"ticker": "005930", "company_name": "삼성전자", "profit_loss_pct": 10.0}

    def test_multiple_holdings_ranking_hhi_and_markets(self):
        hs = [
            H("A", "에이", "KOSPI", 1, 100, 400),        # 400, +300%
            H("B", "비", "KOSDAQ", 1, 400, 300),         # 300, -25%
            H("C", "씨", "KOSDAQ GLOBAL", 1, 200, 200),  # 200, 0%
            H("D", "디", None, 1, 50, 100),              # 100, +100%
        ]
        items, m = compute_portfolio(0, hs)
        assert m["total_asset"] == 1000 and m["cash_weight_pct"] == 0.0
        assert m["top1_weight_pct"] == 40.0 and m["top3_weight_pct"] == 90.0
        assert m["herfindahl_index"] == pytest.approx(0.16 + 0.09 + 0.04 + 0.01)
        assert m["market_weights_pct"] == {"KOSDAQ": 50.0, "KOSPI": 40.0, "OTHER": 10.0}
        assert m["best_holding"]["ticker"] == "A" and m["worst_holding"]["ticker"] == "B"
        assert m["total_profit_loss"] == 1000 - 750
        assert m["total_profit_loss_pct"] == pytest.approx(33.33)

    def test_price_missing_is_excluded(self):
        items, m = compute_portfolio(1000, [H("A", cur=200, qty=5, avg=100), H("B", "비", cur=None, qty=3, avg=100)])
        b = items[1]
        assert b["price_unavailable"] is True and b["is_realtime"] is False
        assert b["eval_amount"] is b["weight_pct"] is b["profit_loss"] is b["profit_loss_pct"] is None
        assert m["stock_eval_amount"] == 1000 and m["total_asset"] == 2000
        assert m["holding_count"] == 2 and m["priced_holding_count"] == 1
        # 매입금액도 현재가를 구한 종목만(B의 300은 제외)
        assert m["total_profit_loss"] == 500 and m["total_profit_loss_pct"] == 100.0

    def test_cash_only_and_all_unpriced_have_no_division_by_zero(self):
        items, m = compute_portfolio(5000, [H(cur=None)])
        assert m["stock_eval_amount"] == 0 and m["total_asset"] == 5000 and m["cash_weight_pct"] == 100.0
        assert m["top1_weight_pct"] is m["top3_weight_pct"] is m["herfindahl_index"] is None
        assert m["market_weights_pct"] == {} and m["total_profit_loss_pct"] is None
        assert m["best_holding"] is m["worst_holding"] is None

    def test_zero_total_asset(self):
        items, m = compute_portfolio(0, [H(cur=None)])
        assert m["total_asset"] == 0 and m["cash_weight_pct"] is None

    def test_zero_avg_price(self):
        items, m = compute_portfolio(0, [H(avg=0, cur=100)])
        assert items[0]["profit_loss_pct"] is None and m["total_profit_loss_pct"] is None

    def test_no_holdings(self):
        items, m = compute_portfolio(1000, [])
        assert items == [] and m["holding_count"] == 0 and m["cash_weight_pct"] == 100.0

    @pytest.mark.parametrize("raw, expected", [("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ"), ("KOSDAQ GLOBAL", "KOSDAQ"),
                                               (None, "OTHER"), ("", "OTHER"), ("KONEX", "OTHER")])
    def test_normalize_market(self, raw, expected):
        assert normalize_market(raw) == expected


class TestFlags:
    def _metrics(self, **over):
        m = {"top1_weight_pct": 10.0, "holding_count": 5, "cash_weight_pct": 30.0, "market_weights_pct": {"KOSPI": 50.0}}
        m.update(over)
        return m

    def test_no_flags(self):
        assert compute_flags([], self._metrics()) == []

    @pytest.mark.parametrize("top1, flagged", [(CONCENTRATION_TOP1_PCT - 0.01, False), (CONCENTRATION_TOP1_PCT, True)])
    def test_concentration_boundary(self, top1, flagged):
        assert ("CONCENTRATION_HIGH" in codes(compute_flags([], self._metrics(top1_weight_pct=top1)))) is flagged

    @pytest.mark.parametrize("n, flagged", [(MIN_HOLDING_COUNT - 1, True), (MIN_HOLDING_COUNT, False)])
    def test_few_holdings_boundary(self, n, flagged):
        assert ("FEW_HOLDINGS" in codes(compute_flags([], self._metrics(holding_count=n)))) is flagged

    @pytest.mark.parametrize(
        "cash, expected",
        [(CASH_HIGH_PCT, ["CASH_HIGH"]), (CASH_HIGH_PCT - 0.01, []), (CASH_LOW_PCT, []), (CASH_LOW_PCT - 0.01, ["CASH_LOW"])],
    )
    def test_cash_boundaries(self, cash, expected):
        assert codes(compute_flags([], self._metrics(cash_weight_pct=cash))) == expected

    @pytest.mark.parametrize("pl, flagged", [(BIG_LOSS_PCT, True), (BIG_LOSS_PCT + 0.01, False), (None, False)])
    def test_big_loss_boundary_per_holding(self, pl, flagged):
        items = [{"ticker": "X", "company_name": "엑스", "profit_loss_pct": pl}]
        flags = compute_flags(items, self._metrics())
        assert ("BIG_LOSS" in codes(flags)) is flagged
        if flagged:
            assert flags[0]["ticker"] == "X" and flags[0]["threshold"] == BIG_LOSS_PCT

    @pytest.mark.parametrize("pct, flagged", [(MARKET_SKEW_PCT, True), (MARKET_SKEW_PCT - 0.01, False)])
    def test_market_skew_boundary(self, pct, flagged):
        flags = compute_flags([], self._metrics(market_weights_pct={"KOSDAQ": pct}))
        assert ("MARKET_SKEW" in codes(flags)) is flagged

    def test_none_metrics_do_not_flag(self):
        m = self._metrics(top1_weight_pct=None, cash_weight_pct=None, market_weights_pct={})
        assert compute_flags([], m) == []

    def test_flag_shape_and_messages_use_metric_numbers(self):
        items, m = compute_portfolio(10, [H(qty=10, avg=100, cur=80)])  # 현금 10 / 총자산 810 = 1.23%
        flags = compute_flags(items, m)
        assert set(codes(flags)) == {"CONCENTRATION_HIGH", "FEW_HOLDINGS", "CASH_LOW", "BIG_LOSS", "MARKET_SKEW"}
        for f in flags:
            assert {"code", "message", "value", "threshold"} <= set(f)
            assert str(f["value"]) in f["message"] or f"{f['value']:g}" in f["message"]


class TestNumberValidation:
    def test_allowed_includes_payload_numbers_variants_and_small_ints(self):
        allowed = allowed_numbers({"a": 1234567.0, "b": 12.345, "c": "삼성전자 005930", "d": [-3.5], "e": None, "f": True})
        for ok in ["1234567", "1,234,567", "12.345", "12.35", "12.3", "12", "5930", "3.5", "123.5", "123", "0.01"] + [str(i) for i in range(1, 11)]:
            assert Decimal(ok.replace(",", "")).normalize() in allowed, ok
        assert Decimal(11) not in allowed

    def test_find_disallowed(self):
        allowed = allowed_numbers({"total": 9201500.0, "pct": 43.47, "threshold": 40.0})
        assert find_disallowed_numbers("총자산 9,201,500원, 현금 43.47%(약 43.5%), 기준 40%, 3개 종목", allowed) == []
        assert find_disallowed_numbers("약 920만원, 0.09억, 9,201.5천원", allowed) == []  # 천/만/억 단위 환산
        assert find_disallowed_numbers("총자산 9,200,500원", allowed) == ["9,200,500"]
        assert find_disallowed_numbers("15개 종목, 2027년", allowed) == ["15", "2027"]

    def test_sign_is_ignored(self):
        allowed = allowed_numbers({"pl": -15.56})
        assert find_disallowed_numbers("손익률 -15.56%", allowed) == []
        assert find_disallowed_numbers("손익률 +15.56%", allowed) == []


class TestValidateLlmOutput:
    ALLOWED = allowed_numbers({"x": 43.47})
    GOOD = {"summary": "현금 비중은 43.47%입니다.", "strengths": ["분산돼 있습니다."], "risks": ["없습니다."],
            "suggestions": ["비중 조정을 검토해볼 수 있습니다."]}

    def test_ok(self):
        out, reason = validate_llm_output(json.dumps(self.GOOD, ensure_ascii=False), self.ALLOWED)
        assert reason is None and out == self.GOOD

    @pytest.mark.parametrize(
        "content, why",
        [
            ("not json", "JSON 파싱"),
            ("[1, 2]", "JSON 객체"),
            (json.dumps({"strengths": [], "risks": [], "suggestions": []}), "summary"),
            (json.dumps({"summary": " ", "strengths": [], "risks": [], "suggestions": []}), "summary"),
            (json.dumps({"summary": "s", "strengths": "문자열", "risks": [], "suggestions": ["x"]}), "strengths"),
            (json.dumps({"summary": "s", "strengths": ["x"], "risks": [1], "suggestions": ["x"]}), "risks"),
            (json.dumps({"summary": "s", "strengths": ["x"], "risks": []}), "suggestions"),
            (json.dumps({"summary": "s", "strengths": [], "risks": [], "suggestions": ["x"]}), "strengths가 비어"),
            (json.dumps({"summary": "s", "strengths": ["x"], "risks": [], "suggestions": [" "]}), "suggestions가 비어"),
            (json.dumps({"summary": "현금 99.9%", "strengths": ["x"], "risks": [], "suggestions": ["x"]}), "숫자"),
            (json.dumps({"summary": "삼성전자를 매도하세요.", "strengths": ["x"], "risks": [], "suggestions": ["x"]}), "직접 매매"),
        ],
    )
    def test_rejects(self, content, why):
        out, reason = validate_llm_output(content, self.ALLOWED)
        assert out is None and why in reason

    def test_empty_risks_without_flags_is_filled_by_code(self):
        content = json.dumps({"summary": "s", "strengths": ["x"], "risks": [], "suggestions": ["y"]})
        out, reason = validate_llm_output(content, self.ALLOWED, flag_count=0)
        assert reason is None and out["risks"] == [d.NO_RISK_SENTENCE]

    def test_duplicate_sentences_are_removed(self):
        content = json.dumps({"summary": "s", "strengths": ["a", "a "], "risks": ["r"], "suggestions": ["b", "c", "b"]})
        out, reason = validate_llm_output(content, self.ALLOWED)
        assert out["strengths"] == ["a"] and out["suggestions"] == ["b", "c"]

    def test_empty_risks_with_flags_is_rejected(self):
        content = json.dumps({"summary": "s", "strengths": ["x"], "risks": [], "suggestions": ["y"]})
        out, reason = validate_llm_output(content, self.ALLOWED, flag_count=2)
        assert out is None and "risks" in reason


class TestRuleBasedText:
    def test_uses_only_allowed_numbers_and_has_all_sections(self):
        items, m = compute_portfolio(4_000_000, [H("005930", "삼성전자", "KOSPI", 10, 280000, 271250),
                                                 H("035720", "카카오", "KOSPI", 20, 45000, 38000)])
        flags = compute_flags(items, m)
        text = rule_based_text(items, m, flags)
        assert set(text) == {"summary", "strengths", "risks", "suggestions"}
        assert all(text[k] for k in text)
        joined = "\n".join([text["summary"], *text["strengths"], *text["risks"], *text["suggestions"]])
        assert find_disallowed_numbers(joined, allowed_numbers(build_llm_payload(items, m, flags))) == []
        assert not any(p in joined for p in d.FORBIDDEN_PHRASES)
        assert text["risks"] == [f["message"] for f in flags]

    def test_no_flags_still_has_non_empty_sections(self):
        hs = [H(t, t, "KOSPI" if k % 2 else "KOSDAQ", 1, 100, 110) for k, t in enumerate("ABCD")]
        items, m = compute_portfolio(100, hs)
        flags = compute_flags(items, m)
        assert flags == []
        text = rule_based_text(items, m, flags)
        assert text["risks"] == ["규칙 기준으로 확인된 위험 항목은 없습니다."] and text["suggestions"]


class TestExplainWithLlm:
    PAYLOAD = {"metrics": {"cash_weight_pct": 43.47}}
    GOOD = json.dumps({"summary": "현금 비중은 43.47%입니다.", "strengths": ["a"], "risks": ["b"], "suggestions": ["c"]},
                      ensure_ascii=False)

    def _fake(self, monkeypatch, responses):
        calls = []

        def fake(messages):
            calls.append(messages)
            r = responses[len(calls) - 1]
            if isinstance(r, Exception):
                raise r
            return r

        monkeypatch.setattr(d, "_chat", fake)
        return calls

    def test_first_try_ok(self, monkeypatch):
        calls = self._fake(monkeypatch, [self.GOOD])
        out, reason = explain_with_llm(self.PAYLOAD)
        assert reason is None and out["summary"] == "현금 비중은 43.47%입니다." and len(calls) == 1

    def test_retry_once_then_ok(self, monkeypatch):
        calls = self._fake(monkeypatch, ["{broken", self.GOOD])
        out, reason = explain_with_llm(self.PAYLOAD)
        assert out is not None and len(calls) == 2
        assert "규칙 위반" in calls[1][-1]["content"]  # 재시도 때 실패 사유를 알려준다

    def test_two_failures_give_up(self, monkeypatch):
        calls = self._fake(monkeypatch, ["{broken", "{broken"])
        out, reason = explain_with_llm(self.PAYLOAD)
        assert out is None and "검증 실패" in reason and len(calls) == 2

    @pytest.mark.parametrize("exc", [ConnectionError("down"), TimeoutError("slow"), RuntimeError("x")])
    def test_call_error_gives_up_without_retry(self, monkeypatch, exc):
        calls = self._fake(monkeypatch, [exc, self.GOOD])
        out, reason = explain_with_llm(self.PAYLOAD)
        assert out is None and "호출 실패" in reason and len(calls) == 1


class TestBuildDiagnosis:
    def test_no_holdings_raises_without_llm(self, monkeypatch):
        monkeypatch.setattr(d, "_chat", lambda m: pytest.fail("LLM을 부르면 안 됨"))
        with pytest.raises(d.NoHoldings, match="보유 종목이 없습니다"):
            build_diagnosis(10_000_000, [])

    def test_all_unpriced_skips_llm_and_uses_rules(self, monkeypatch):
        monkeypatch.setattr(d, "_chat", lambda m: pytest.fail("LLM을 부르면 안 됨"))
        out = build_diagnosis(1000, [H(cur=None)])
        assert out["source"] == "rule_based" and out["model"] is None
        assert any("현재가를 가져오지 못해" in n for n in out["notes"])
        assert out["disclaimer"] == DISCLAIMER

    def test_payload_has_no_user_identity(self, monkeypatch):
        seen = []
        monkeypatch.setattr(d, "_chat", lambda m: seen.append(m) or TestExplainWithLlm.GOOD)
        build_diagnosis(1000, [H()])
        sent = json.dumps(seen[0], ensure_ascii=False)
        assert "user" not in json.loads(seen[0][1]["content"])
        assert "username" not in sent and "user:" not in sent and "device" not in sent
