"""app/diagnosis.py 순수 로직(DB/LLM 없음): 지표·플래그 계산, 숫자 검증, LLM 출력 검증(종목명-숫자 짝 포함),
폴백 템플릿, 강점·위험 사실 문장 템플릿, 재시도 흐름."""

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
    fact_sentences,
    find_disallowed_numbers,
    find_mismatched_numbers,
    find_unknown_companies,
    find_wrong_top_claims,
    holding_fact,
    holding_numbers,
    normalize_market,
    rule_based_text,
    top_weight_names,
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
    """LLM은 summary와 suggestions만 쓴다. strengths/risks는 코드가 만들어서(fact_sentences) 출력에 있어도 무시한다."""

    ALLOWED = allowed_numbers({"x": 43.47})
    GOOD = {"summary": "현금 비중은 43.47%입니다.", "suggestions": ["비중 조정을 검토해볼 수 있습니다."]}

    def test_ok(self):
        out, reason = validate_llm_output(json.dumps(self.GOOD, ensure_ascii=False), self.ALLOWED)
        assert reason is None and out == self.GOOD

    def test_strengths_and_risks_in_the_output_are_ignored(self):
        """예전 4개 필드 형식으로 답해도 summary/suggestions만 쓴다. 형식이 틀린 strengths/risks도 실패 사유가 아니다."""
        content = json.dumps({**self.GOOD, "strengths": "문자열", "risks": [1]}, ensure_ascii=False)
        out, reason = validate_llm_output(content, self.ALLOWED)
        assert reason is None and out == self.GOOD

    @pytest.mark.parametrize(
        "content, why",
        [
            ("not json", "JSON 파싱"),
            ("[1, 2]", "JSON 객체"),
            (json.dumps({"suggestions": ["x"]}), "summary"),
            (json.dumps({"summary": " ", "suggestions": ["x"]}), "summary"),
            (json.dumps({"summary": "s"}), "suggestions"),
            (json.dumps({"summary": "s", "suggestions": "문자열"}), "suggestions"),
            (json.dumps({"summary": "s", "suggestions": [1]}), "suggestions"),
            (json.dumps({"summary": "s", "suggestions": []}), "suggestions가 비어"),
            (json.dumps({"summary": "s", "suggestions": [" "]}), "suggestions가 비어"),
            (json.dumps({"summary": "현금 99.9%", "suggestions": ["x"]}), "숫자"),
            (json.dumps({"summary": "삼성전자를 매도하세요.", "suggestions": ["x"]}), "직접 매매"),
            (json.dumps({"summary": "이동평균 등락률이 높습니다.", "suggestions": ["x"]}), "입력에 없는 지표"),
            (json.dumps({"summary": "s", "suggestions": ["이동 평균을 점검해볼 수 있습니다."]}), "입력에 없는 지표"),
        ],
    )
    def test_rejects(self, content, why):
        out, reason = validate_llm_output(content, self.ALLOWED)
        assert out is None and why in reason

    def test_duplicate_suggestions_are_removed(self):
        content = json.dumps({"summary": "s", "suggestions": ["b", "c", "b ", "c"]})
        out, reason = validate_llm_output(content, self.ALLOWED)
        assert reason is None and out["suggestions"] == ["b", "c"]

    def test_suggestions_with_numbers_are_dropped_not_rejected(self):
        """숫자가 든 제안(지표를 다시 읊는 문장)은 버리고 나머지만 쓴다. 입력에 없는 숫자여도 버려지므로 실패가 아니다."""
        content = json.dumps(
            {"summary": "s", "suggestions": ["상위 3개 종목 합계 비중은 12.56%입니다.", "현금 비중을 점검해볼 수 있습니다."]},
            ensure_ascii=False,
        )
        out, reason = validate_llm_output(content, self.ALLOWED)
        assert reason is None and out["suggestions"] == ["현금 비중을 점검해볼 수 있습니다."]

    def test_all_numbered_suggestions_leave_an_empty_list(self):
        content = json.dumps({"summary": "s", "suggestions": ["집중도는 0.4352로 기준 이상입니다."]}, ensure_ascii=False)
        out, reason = validate_llm_output(content, self.ALLOWED)
        assert reason is None and out == {"summary": "s", "suggestions": []}

    def test_forbidden_phrase_in_a_dropped_suggestion_does_not_matter_but_in_a_kept_one_does(self):
        dropped = json.dumps({"summary": "s", "suggestions": ["3주를 매도하세요.", "점검해볼 수 있습니다."]}, ensure_ascii=False)
        assert validate_llm_output(dropped, self.ALLOWED)[0]["suggestions"] == ["점검해볼 수 있습니다."]
        kept = json.dumps({"summary": "s", "suggestions": ["카카오를 매도하세요."]}, ensure_ascii=False)
        assert "직접 매매" in validate_llm_output(kept, self.ALLOWED)[1]


def three_holdings():
    """재현 조건: 삼성전자 2주, 카카오 3주, 에코프로비엠 5주(매수 순서). 비중 1위는 마지막에 산 에코프로비엠이다."""
    items, m = compute_portfolio(8_744_300, [
        H("005930", "삼성전자", "KOSPI", 2, 263000, 263000, False, -2.59),
        H("035720", "카카오", "KOSPI", 3, 32400, 32400, False, -11.35),
        H("247540", "에코프로비엠", "KOSDAQ GLOBAL", 5, 126500, 126500, False, 18.56),
    ])
    flags = compute_flags(items, m)
    return items, m, flags


class TestHoldingChecks:
    """종목명과 숫자의 '짝'을 보는 검증. 숫자도 종목명도 입력에 있는 값이라 allowed_numbers로는 못 잡는 오류들이다."""

    @pytest.fixture
    def payload(self):
        return build_llm_payload(*three_holdings())

    def test_fixture_matches_the_reported_case(self, payload):
        by_name = {h["company_name"]: h for h in payload["holdings"]}
        assert [by_name[n]["weight_pct"] for n in ("삼성전자", "카카오", "에코프로비엠")] == [5.26, 0.97, 6.33]
        assert payload["metrics"]["top1_weight_pct"] == 6.33 and payload["metrics"]["top3_weight_pct"] == 12.56

    def test_holding_numbers_has_only_that_holdings_numbers(self, payload):
        per = holding_numbers(payload)
        assert set(per) == {"삼성전자", "카카오", "에코프로비엠"}
        samsung = per["삼성전자"]
        assert {Decimal("5.26"), Decimal("2.59"), Decimal("263000"), Decimal("526000"), Decimal("20")} <= samsung
        # 다른 종목의 비중, 상위 3종목 합계, 현금 비중은 삼성전자의 숫자가 아니다
        assert not {Decimal("6.33"), Decimal("0.97"), Decimal("12.56"), Decimal("87.44"), Decimal("18.56")} & samsung

    def test_holding_numbers_includes_the_flag_attached_to_that_holding(self):
        items, m = compute_portfolio(4_000_000, [H("005930", "삼성전자", "KOSPI", 10, 280000, 271250),
                                                 H("035720", "카카오", "KOSPI", 20, 45000, 38000)])
        per = holding_numbers(build_llm_payload(items, m, compute_flags(items, m)))
        assert Decimal("15.56") in per["카카오"] and Decimal("15.56") not in per["삼성전자"]
        assert find_mismatched_numbers("카카오의 손익률이 -15.56%로 기준(-10%) 이하입니다.", per) == []

    @pytest.mark.parametrize(
        "text, bad",
        [
            # 실제로 나온 문장들
            ("삼성전자와 카카오의 비중이 총자산의 12.56%를 차지합니다.", ["12.56"]),
            ("비중 상위 1개 종목은 삼성전자로 6.33%입니다.", ["6.33"]),
            ("카카오의 최근 20거래일 등락률은 18.56%입니다.", ["18.56"]),
            ("삼성전자 등을 보유하고 현금 비중은 87.44%입니다.", ["87.44"]),
            # 맞는 문장
            ("삼성전자는 총자산의 5.26%이고, 최근 20거래일 주가 등락률은 -2.59%입니다.", []),
            ("삼성전자(5.26%)와 카카오(0.97%)를 보유하고 있습니다.", []),
            ("에코프로비엠 5주의 평가금액은 632,500원입니다.", []),
            # 종목명이 없는 문장은 이 검사의 대상이 아니다
            ("현금 비중이 87.44%로 기준(70%) 이상입니다.", []),
            # 문장 단위로 본다: 앞 문장의 포트폴리오 숫자는 뒤 문장의 종목과 상관없다
            ("현금 비중은 87.44%입니다. 카카오의 비중은 0.97%입니다.", []),
            ("현금 비중은 87.44%입니다.\n카카오의 비중은 5.26%입니다.", ["5.26"]),
        ],
    )
    def test_find_mismatched_numbers(self, payload, text, bad):
        assert find_mismatched_numbers(text, holding_numbers(payload)) == bad

    def test_overlapping_names_use_the_longest_match(self):
        per = {"카카오": allowed_numbers([1.11]), "카카오뱅크": allowed_numbers([22.22])}
        assert find_mismatched_numbers("카카오뱅크의 비중은 22.22%입니다.", per) == []
        assert find_mismatched_numbers("카카오뱅크의 비중은 1.11%입니다.", per) == ["1.11"]
        assert find_mismatched_numbers("카카오의 비중은 22.22%입니다.", per) == ["22.22"]

    @pytest.mark.parametrize(
        "text, held, unknown",
        [
            ("SK하이닉스도 함께 살펴볼 수 있습니다.", ["삼성전자"], ["SK하이닉스"]),
            ("네이버나 현대차를 검토해볼 수 있습니다.", ["삼성전자"], ["네이버", "현대차"]),
            ("삼성전자와 카카오를 점검해볼 수 있습니다.", ["삼성전자", "카카오"], []),
            # 보유 종목의 통칭은 보유 종목이다
            ("삼전 비중을 점검해볼 수 있습니다.", ["삼성전자"], []),
            ("네이버 비중을 점검해볼 수 있습니다.", ["NAVER"], []),
            # 보유 종목 이름에 들어 있는 다른 종목명("현대차증권" 안의 "현대차")은 지운 뒤에 찾는다
            ("현대차증권을 점검해볼 수 있습니다.", ["현대차증권"], []),
            # 짧은 영문 이름(NC, SM, KAI)은 다른 낱말의 일부로 걸려서 사전에 없다
            ("CONCENTRATION_HIGH 플래그와 SMALL CAP을 확인했습니다.", ["삼성전자"], []),
            ("", ["삼성전자"], []),
        ],
    )
    def test_find_unknown_companies(self, text, held, unknown):
        assert find_unknown_companies(text, held) == unknown

    def test_top_weight_names(self, payload):
        assert top_weight_names(payload) == {"에코프로비엠"}
        assert top_weight_names({"holdings": []}) == set()
        tie = {"holdings": [{"company_name": "A", "weight_pct": 10.0}, {"company_name": "B", "weight_pct": 10.0},
                            {"company_name": "C", "weight_pct": None}]}
        assert top_weight_names(tie) == {"A", "B"}

    @pytest.mark.parametrize(
        "text, wrong",
        [
            ("비중 상위 1개 종목인 삼성전자에 대한 투자 전략을 검토해 볼 수 있습니다.", 1),
            ("비중이 가장 큰 종목은 카카오입니다.", 1),
            ("가장 큰 비중을 차지하는 삼성전자를 점검해볼 수 있습니다.", 1),
            ("비중 상위 1개 종목인 에코프로비엠을 점검해볼 수 있습니다.", 0),
            # 1위 종목이 같이 거론되면 통과(상위 3개를 나열한 문장)
            ("비중 상위 1개/3개 종목은 삼성전자, 카카오, 에코프로비엠입니다.", 0),
            # 순위 표현이 없거나 종목명이 없는 문장은 대상이 아니다
            ("삼성전자의 주가 하락을 점검해볼 수 있습니다.", 0),
            ("상위 1종목 비중이 6.33%로 한 종목에 크게 쏠려 있지 않습니다.", 0),
        ],
    )
    def test_find_wrong_top_claims(self, payload, text, wrong):
        names = [h["company_name"] for h in payload["holdings"]]
        assert len(find_wrong_top_claims(text, names, top_weight_names(payload))) == wrong

    @pytest.mark.parametrize(
        "summary, suggestion, why",
        [
            ("삼성전자와 카카오의 비중이 총자산의 12.56%를 차지합니다.", "점검해볼 수 있습니다.", "종목명과 맞지 않는 숫자"),
            ("현금 비중은 87.44%입니다.", "SK하이닉스를 함께 살펴볼 수 있습니다.", "보유하지 않은 종목명"),
            # 숫자가 없어서 숫자 검사로는 못 잡는 순위 오류(실제 1위는 에코프로비엠)
            ("비중 상위 1개 종목인 삼성전자의 비중은 5.26%입니다.", "점검해볼 수 있습니다.", "비중 1위 종목이 틀림"),
            ("현금 비중은 87.44%입니다.", "비중이 가장 큰 종목인 카카오를 점검해볼 수 있습니다.", "비중 1위 종목이 틀림"),
        ],
    )
    def test_validate_rejects_with_payload(self, payload, summary, suggestion, why):
        content = json.dumps({"summary": summary, "suggestions": [suggestion]}, ensure_ascii=False)
        # payload 없이는(예전 방식) 통과하던 출력이다: 숫자도 종목명도 입력에 있는 값이라서
        assert validate_llm_output(content, allowed_numbers(payload))[1] is None
        out, reason = validate_llm_output(content, allowed_numbers(payload), payload)
        assert out is None and why in reason

    def test_validate_reason_names_the_real_top_holding_for_the_retry(self, payload):
        content = json.dumps({"summary": "s", "suggestions": ["비중이 가장 큰 삼성전자를 점검해볼 수 있습니다."]},
                             ensure_ascii=False)
        assert "에코프로비엠" in validate_llm_output(content, allowed_numbers(payload), payload)[1]

    def test_numbered_suggestion_with_a_wrong_top_claim_is_simply_dropped(self, payload):
        """숫자가 든 제안은 검사 전에 버려지므로, 그 안의 틀린 순위 문장도 응답에 남지 않는다."""
        content = json.dumps({"summary": "s", "suggestions": ["비중 상위 1개 종목인 삼성전자를 점검해볼 수 있습니다."]},
                             ensure_ascii=False)
        assert validate_llm_output(content, allowed_numbers(payload), payload) == ({"summary": "s", "suggestions": []}, None)

    def test_validate_accepts_correct_holding_sentences(self, payload):
        good = {"summary": "현금 비중이 87.44%로 기준(70%) 이상입니다. 에코프로비엠의 비중은 6.33%입니다.",
                "suggestions": ["카카오의 주가 하락을 점검해볼 수 있습니다."]}
        out, reason = validate_llm_output(json.dumps(good, ensure_ascii=False), allowed_numbers(payload), payload)
        assert reason is None and out == good


class TestLlmPayload:
    def test_holdings_are_sorted_by_weight_so_the_first_one_is_the_top_holding(self):
        """모델은 목록의 첫 종목을 비중 1위로 읽는다. 매수 순서(삼성전자 먼저)가 아니라 비중 순서로 넘긴다."""
        items, m, flags = three_holdings()
        assert [i["company_name"] for i in items] == ["삼성전자", "카카오", "에코프로비엠"]  # 응답 순서는 그대로
        payload = build_llm_payload(items, m, flags)
        assert [h["company_name"] for h in payload["holdings"]] == ["에코프로비엠", "삼성전자", "카카오"]
        assert set(payload) == {"metrics", "flags", "holdings", "return_window_days"}

    def test_unpriced_holdings_go_last(self):
        items, m = compute_portfolio(1000, [H("A", "가", cur=None), H("B", "나", qty=1), H("C", "다", qty=5)])
        payload = build_llm_payload(items, m, compute_flags(items, m))
        assert [h["company_name"] for h in payload["holdings"]] == ["다", "나", "가"]


class TestFactSentences:
    def test_holding_fact_wording_and_particle(self):
        items, m, flags = three_holdings()
        by_name = {i["company_name"]: i for i in items}
        assert holding_fact(by_name["삼성전자"]) == "삼성전자는 총자산의 5.26%이고, 최근 20거래일 주가 등락률은 -2.59%입니다."
        assert holding_fact(by_name["에코프로비엠"]) == "에코프로비엠은 총자산의 6.33%이고, 최근 20거래일 주가 등락률은 +18.56%입니다."

    @pytest.mark.parametrize("name, particle", [("삼성전자", "는"), ("에코프로비엠", "은"), ("KB금융", "은"), ("NAVER", "는"),
                                                 ("LG", "는"), ("기아", "는"), ("S-Oil", "는")])
    def test_topic_particle(self, name, particle):
        assert d._topic_particle(name) == particle

    def test_unpriced_holding_has_no_weight_clause(self):
        items, m = compute_portfolio(1000, [H(cur=None, r20=3.5)])
        assert holding_fact(items[0]) == "삼성전자는 최근 20거래일 주가 등락률은 +3.5%입니다."

    def test_three_holdings(self):
        items, m, flags = three_holdings()
        facts = fact_sentences(items, m, flags)
        base = rule_based_text(items, m, flags)
        assert set(facts) == {"strengths", "risks"}
        # 포트폴리오 문장은 폴백과 같은 문장, 그 뒤에 종목 문장(오른 종목은 강점, 내린 종목은 위험 요인)
        assert facts["strengths"] == base["strengths"] + [
            "에코프로비엠은 총자산의 6.33%이고, 최근 20거래일 주가 등락률은 +18.56%입니다."
        ]
        assert facts["risks"] == base["risks"] + [
            "카카오는 총자산의 0.97%이고, 최근 20거래일 주가 등락률은 -11.35%입니다.",  # 하락 폭이 큰 순
            "삼성전자는 총자산의 5.26%이고, 최근 20거래일 주가 등락률은 -2.59%입니다.",
        ]
        assert base["risks"] == [f["message"] for f in flags] and [f["code"] for f in flags] == ["CASH_HIGH"]

    def test_every_sentence_passes_the_same_checks_the_llm_output_must_pass(self):
        """템플릿 문장은 숫자 검사와 종목명-숫자 짝 검사를 그대로 통과해야 한다(종목명과 숫자가 틀리게 짝지어질 수 없다)."""
        items, m, flags = three_holdings()
        payload = build_llm_payload(items, m, flags)
        facts = fact_sentences(items, m, flags)
        joined = "\n".join(facts["strengths"] + facts["risks"])
        assert find_disallowed_numbers(joined, allowed_numbers(payload)) == []
        assert find_mismatched_numbers(joined, holding_numbers(payload)) == []
        assert find_unknown_companies(joined, holding_numbers(payload)) == []

    def test_holdings_without_a_return_or_with_zero_return_are_not_listed(self):
        items, m = compute_portfolio(100, [H("A", "가", r20=None), H("B", "나", r20=0.0), H("C", "다", r20=-1.0),
                                           H("D", "라", r20=2.0)])
        flags = compute_flags(items, m)
        facts = fact_sentences(items, m, flags)
        base = rule_based_text(items, m, flags)
        assert facts["strengths"][len(base["strengths"]):] == [holding_fact(items[3])]
        assert facts["risks"][len(base["risks"]):] == [holding_fact(items[2])]

    def test_at_most_max_holding_facts_each_largest_move_first(self):
        hs = [H(str(k), f"종목{k}", r20=float(k)) for k in range(1, 6)] + [H(f"m{k}", f"하락{k}", r20=-float(k)) for k in range(1, 6)]
        items, m = compute_portfolio(100, hs)
        flags = compute_flags(items, m)
        facts = fact_sentences(items, m, flags)
        base = rule_based_text(items, m, flags)
        up, down = facts["strengths"][len(base["strengths"]):], facts["risks"][len(base["risks"]):]
        assert len(up) == len(down) == d.MAX_HOLDING_FACTS == 3
        assert [s.split("은 ")[0].split("는 ")[0] for s in up] == ["종목5", "종목4", "종목3"]
        assert [s.split("은 ")[0].split("는 ")[0] for s in down] == ["하락5", "하락4", "하락3"]

    def test_no_flags_and_no_moves_keeps_the_fixed_no_risk_sentence(self):
        hs = [H(t, t, "KOSPI" if k % 2 else "KOSDAQ", 1, 100, 110) for k, t in enumerate("ABCD")]
        items, m = compute_portfolio(100, hs)
        assert fact_sentences(items, m, [])["risks"] == [d.NO_RISK_SENTENCE]


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

    def test_result_has_only_summary_and_suggestions(self, monkeypatch):
        self._fake(monkeypatch, [self.GOOD])  # GOOD은 예전 4개 필드 형식이다
        out, _ = explain_with_llm(self.PAYLOAD)
        assert out == {"summary": "현금 비중은 43.47%입니다.", "suggestions": ["c"]}

    def test_wrong_holding_number_pair_is_retried_with_the_reason(self, monkeypatch):
        payload = build_llm_payload(*three_holdings())
        bad = json.dumps({"summary": "비중 상위 1개 종목은 삼성전자로 6.33%입니다.", "suggestions": ["점검"]}, ensure_ascii=False)
        good = json.dumps({"summary": "현금 비중은 87.44%입니다.", "suggestions": ["점검"]}, ensure_ascii=False)
        calls = self._fake(monkeypatch, [bad, good])
        out, reason = explain_with_llm(payload)
        assert out == {"summary": "현금 비중은 87.44%입니다.", "suggestions": ["점검"]} and len(calls) == 2
        assert "종목명과 맞지 않는 숫자" in calls[1][-1]["content"] and "6.33" in calls[1][-1]["content"]

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

    def test_llm_path_uses_code_facts_for_strengths_and_risks(self, monkeypatch):
        """LLM이 strengths/risks를 써 보내도 쓰지 않는다. LLM 글은 summary와 suggestions에만 들어간다."""
        llm_out = {"summary": "현금 비중은 87.44%입니다.", "strengths": ["비중 상위 1개 종목은 삼성전자로 6.33%입니다."],
                   "risks": ["LLM이 쓴 위험"], "suggestions": ["현금 비중을 점검해볼 수 있습니다."]}
        monkeypatch.setattr(d, "_chat", lambda m: json.dumps(llm_out, ensure_ascii=False))
        cash, inputs = 8_744_300, [
            H("005930", "삼성전자", "KOSPI", 2, 263000, 263000, False, -2.59),
            H("035720", "카카오", "KOSPI", 3, 32400, 32400, False, -11.35),
            H("247540", "에코프로비엠", "KOSDAQ GLOBAL", 5, 126500, 126500, False, 18.56),
        ]
        out = build_diagnosis(cash, inputs)
        facts = fact_sentences(out["holdings"], out["metrics"], out["flags"])
        assert out["source"] == "llm"
        assert out["summary"] == llm_out["summary"] and out["suggestions"] == llm_out["suggestions"]
        assert out["strengths"] == facts["strengths"] and out["risks"] == facts["risks"]
        assert "삼성전자로 6.33%" not in json.dumps(out, ensure_ascii=False)
        assert list(out)[6:10] == ["summary", "strengths", "risks", "suggestions"]  # 필드 순서 유지

    def test_llm_path_fills_suggestions_from_rules_when_all_were_dropped(self, monkeypatch):
        llm_out = {"summary": "현금 비중은 87.44%입니다.", "suggestions": ["상위 3개 종목 합계 비중은 12.56%입니다."]}
        monkeypatch.setattr(d, "_chat", lambda m: json.dumps(llm_out, ensure_ascii=False))
        items_in = [H("005930", "삼성전자", "KOSPI", 2, 263000, 263000), H("035720", "카카오", "KOSPI", 3, 32400, 32400),
                    H("247540", "에코프로비엠", "KOSDAQ", 5, 126500, 126500)]
        out = build_diagnosis(8_744_300, items_in)
        assert out["source"] == "llm" and out["summary"] == llm_out["summary"]
        assert out["suggestions"] == rule_based_text(out["holdings"], out["metrics"], out["flags"])["suggestions"]
        assert not any("규칙 기반 문장" in n for n in out["notes"])

    def test_fallback_is_unchanged_rule_based_text(self, monkeypatch):
        """폴백(source=rule_based)은 예전과 같은 rule_based_text 그대로다(종목 사실 문장을 덧붙이지 않는다)."""
        monkeypatch.setattr(d, "_chat", lambda m: "{broken")
        out = build_diagnosis(1000, [H(r20=-5.0)])
        expected = rule_based_text(out["holdings"], out["metrics"], out["flags"])
        assert out["source"] == "rule_based"
        assert {k: out[k] for k in expected} == expected
        assert out["risks"] == [f["message"] for f in out["flags"]]

    def test_payload_has_no_user_identity(self, monkeypatch):
        seen = []
        monkeypatch.setattr(d, "_chat", lambda m: seen.append(m) or TestExplainWithLlm.GOOD)
        build_diagnosis(1000, [H()])
        sent = json.dumps(seen[0], ensure_ascii=False)
        assert "user" not in json.loads(seen[0][1]["content"])
        assert "username" not in sent and "user:" not in sent and "device" not in sent
