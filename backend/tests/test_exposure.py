"""app/exposure.py 순수 단위 테스트(DB 없음). 손으로 계산한 예제, 합산, 연결 안 됨, 합계 불변식, 비중 합계 경계, 분해하지 않는 ETF, 경계값, 금지 표현, stale."""

import random
from datetime import date

import pytest

from app.exposure import (
    CONCENTRATION_PCT, DEFAULT_LIMIT, MAX_LIMIT, MSG_NO_HOLDINGS, MSG_NO_PRICES, REASON_LEVERAGED, REASON_NO_DATA,
    ROUNDING_TOLERANCE_PER_ITEM, ConstituentIn, EtfIn, HoldingIn, compute_exposure,
)
from evals.metrics import forbidden_hits

TODAY = date(2026, 10, 30)  # 금요일
D = date(2026, 10, 29)


def stock(ticker, name, qty, price, d=D):
    return HoldingIn(ticker, name, "stock", qty, price, d)


def etf_h(ticker, name, qty, price, d=D):
    return HoldingIn(ticker, name, "etf", qty, price, d)


def con(name, weight, code=None, linked=None):
    """linked가 주어지면 company와 연결된 종목(코드 = 그 종목코드)."""
    return ConstituentIn(name, code if code is not None else linked, weight, linked, name if linked else None)


def by_name(res, name):
    return next(c for c in res["companies"] if c["name"] == name)


# 손계산 예제: 삼성전자 10주 x 70,000 = 700,000 + 가짜 ETF 100주 x 30,000 = 3,000,000 -> 전체 3,700,000
SAMPLE_ETF = EtfIn("111111", "TIGER 테스트200", D, [
    con("삼성전자", 30, linked="005930"), con("SK하이닉스", 20, linked="000660"), con("현대차", 10, linked="005380"),
    con("Apple Inc", 5, code="AAPL US EQUITY"),
])


def sample():
    return compute_exposure([stock("005930", "삼성전자", 10, 70_000), etf_h("111111", "TIGER 테스트200", 100, 30_000)],
                            {"111111": SAMPLE_ETF}, TODAY)


class TestHandComputed:
    def test_samsung_direct_plus_etf(self):
        r = sample()
        s = by_name(r, "삼성전자")
        assert s["direct_amount"] == 700_000 and s["via_etf_amount"] == 900_000 and s["total_amount"] == 1_600_000
        assert s["effective_weight_pct"] == 43.24 and s["direct_weight_pct"] == 18.92  # 1.6/3.7, 0.7/3.7
        assert s["via_etfs"] == [{"ticker": "111111", "name": "TIGER 테스트200", "amount": 900_000, "etf_weight_pct": 30.0}]
        assert s["linked"] is True and s["code"] == "005930" and s["rank"] == 1

    def test_other_companies_and_remainder(self):
        r = sample()
        assert [c["name"] for c in r["companies"]] == ["삼성전자", "SK하이닉스", "현대차", "Apple Inc"]
        assert by_name(r, "SK하이닉스")["total_amount"] == 600_000 and by_name(r, "현대차")["total_amount"] == 300_000
        apple = by_name(r, "Apple Inc")
        assert apple["total_amount"] == 150_000 and apple["linked"] is False and apple["code"] == "AAPL US EQUITY"
        assert apple["direct_amount"] == 0
        sm = r["summary"]
        assert sm["total_eval_amount"] == 3_700_000 and sm["other_amount"] == 1_050_000 and sm["other_weight_pct"] == 28.38
        assert sm["stock_amount"] == 700_000 and sm["etf_amount"] == 3_000_000 and sm["decomposed_etf_amount"] == 3_000_000
        assert sm["companies_amount"] == 2_650_000 and sm["company_count"] == 4

    def test_overlap_and_flags(self):
        r = sample()
        assert [o["name"] for o in r["overlaps"]] == ["삼성전자"]
        kinds = [(f["kind"], f["name"]) for f in r["flags"]]
        assert ("concentration", "삼성전자") in kinds                       # 43.24% >= 40%
        assert ("etf_inflates_weight", "삼성전자") in kinds                # via 24.32%p >= 10
        assert ("etf_inflates_weight", "SK하이닉스") in kinds              # 16.22%p
        assert not any(n == "현대차" for _, n in kinds)                    # 8.11%p, 8.11%
        gap = next(f for f in r["flags"] if f["kind"] == "etf_inflates_weight" and f["name"] == "삼성전자")
        assert gap["value_pct"] == 24.32 and gap["threshold_pct"] == 10.0 and "43.24%" in gap["message"] and "18.92%" in gap["message"]
        conc = next(f for f in r["flags"] if f["kind"] == "concentration")
        assert conc["value_pct"] == 43.24 and conc["threshold_pct"] == CONCENTRATION_PCT

    def test_decomposed_etf_info(self):
        d = sample()["decomposed_etfs"][0]
        assert d["ticker"] == "111111" and d["holdings_as_of"] == "2026-10-29" and d["stale"] is False and d["stale_notice"] is None
        assert d["constituent_count"] == 4 and d["weight_sum_pct"] == 65.0 and d["eval_amount"] == 3_000_000 and d["weight_pct"] == 81.08

    def test_concentration_boundary_is_inclusive(self):
        # 직접 40주 x 100 = 4,000 / 전체 10,000 = 정확히 40%
        r = compute_exposure([stock("005930", "삼성전자", 40, 100), stock("000660", "SK하이닉스", 60, 100)], {}, TODAY)
        names = {f["name"] for f in r["flags"] if f["kind"] == "concentration"}
        assert names == {"삼성전자", "SK하이닉스"}  # 40.00%와 60.00% 모두 기준 이상
        r = compute_exposure([stock("005930", "삼성전자", 399, 10), stock("000660", "SK하이닉스", 601, 10)], {}, TODAY)
        assert {f["name"] for f in r["flags"]} == {"SK하이닉스"}  # 39.9%는 기준 미만

    def test_etf_gap_boundary(self):
        # ETF 4,000원 x 25% = 삼성전자 경유 1,000원 / 전체 10,000원(= SK 3,000 + LG 3,000 + ETF 4,000) = 정확히 10%p
        etf = EtfIn("111111", "TIGER 테스트", D, [con("삼성전자", 25, linked="005930")])
        r = compute_exposure([stock("000660", "SK하이닉스", 30, 100), stock("003550", "LG", 30, 100), etf_h("111111", "TIGER 테스트", 40, 100)],
                             {"111111": etf}, TODAY)
        assert [f["kind"] for f in r["flags"]] == ["etf_inflates_weight"] and r["flags"][0]["value_pct"] == 10.0


class TestAggregation:
    def test_same_company_in_two_etfs_and_duplicate_rows(self):
        a = EtfIn("111111", "TIGER A", D, [con("삼성전자", 25, linked="005930"), con("삼성전자", 5, linked="005930")])  # 같은 ETF 안 중복 행
        b = EtfIn("222222", "TIGER B", D, [con("삼성전자", 10, linked="005930")])
        r = compute_exposure([etf_h("111111", "TIGER A", 10, 1000), etf_h("222222", "TIGER B", 5, 2000)], {"111111": a, "222222": b}, TODAY)
        s = by_name(r, "삼성전자")
        assert s["direct_amount"] == 0 and s["via_etf_amount"] == 3000 + 1000 and s["total_amount"] == 4000
        assert [(v["ticker"], v["amount"], v["etf_weight_pct"]) for v in s["via_etfs"]] == [("111111", 3000, 30.0), ("222222", 1000, 10.0)]
        assert len([c for c in r["companies"] if c["name"] == "삼성전자"]) == 1
        assert r["overlaps"] == []  # 직접 보유가 없으면 겹침이 아니다

    def test_unlinked_constituents_group_by_name_ignoring_case_and_spaces(self):
        a = EtfIn("111111", "TIGER A", D, [con("NVIDIA Corp", 8, code="NVDA US EQUITY")])
        b = EtfIn("222222", "TIGER B", D, [con("nvidia  corp", 4, code=None)])
        r = compute_exposure([etf_h("111111", "TIGER A", 1, 10_000), etf_h("222222", "TIGER B", 1, 10_000)], {"111111": a, "222222": b}, TODAY)
        n = r["companies"][0]
        assert len(r["companies"]) == 1 and n["linked"] is False and n["code"] == "NVDA US EQUITY"
        assert n["total_amount"] == 800 + 400 and len(n["via_etfs"]) == 2

    def test_unlinked_name_is_not_merged_with_a_linked_company(self):
        etf = EtfIn("111111", "TIGER A", D, [con("삼성전자", 10, code=None)])  # 코드가 없어 연결 안 됨
        r = compute_exposure([stock("005930", "삼성전자", 1, 1000), etf_h("111111", "TIGER A", 1, 1000)], {"111111": etf}, TODAY)
        assert sorted((c["linked"], c["total_amount"]) for c in r["companies"]) == [(False, 100), (True, 1000)]


class TestInvariant:
    @pytest.mark.parametrize("seed", range(40))
    def test_parts_sum_to_total(self, seed):
        rng = random.Random(seed)
        holdings, etfs = [], {}
        for i in range(rng.randint(1, 6)):
            holdings.append(stock(f"{i:06d}", f"종목{i}", rng.randint(1, 500), round(rng.uniform(100, 300_000), 2)))
        names = ["삼성전자", "SK하이닉스", "NVIDIA", "Apple", "현대차", "카카오"]
        for k in range(rng.randint(0, 4)):
            t = f"9{k}0000"
            cons = []
            for j in range(rng.randint(0, 12)):
                w = round(rng.uniform(-2, 30), 4)
                nm = rng.choice(names)
                cons.append(con(nm, w, linked=f"00{names.index(nm)}000") if rng.random() < 0.5 else con(nm, w, code=f"X{j}"))
            holdings.append(etf_h(t, f"TIGER 가짜{k}" if rng.random() < 0.8 else "KODEX 레버리지", rng.randint(1, 300), round(rng.uniform(1000, 50_000), 2)))
            etfs[t] = EtfIn(t, holdings[-1].name, D, cons)
        r = compute_exposure(holdings, etfs, TODAY, limit=MAX_LIMIT)
        if r["message"]:
            return
        # limit=50이 모든 기업을 담지 못하면 전체 합계 검증 대신 companies_amount로 대조(아래 별도 테스트가 전체를 검증)
        full = compute_exposure(holdings, etfs, TODAY, limit=10**6)
        sm = full["summary"]
        parts = [c["total_amount"] for c in full["companies"]] + [sm["other_amount"]] + [u["eval_amount"] for u in full["undecomposed_etfs"]]
        assert sm["company_count"] == len(full["companies"])
        tol = ROUNDING_TOLERANCE_PER_ITEM * (len(parts) + 1)
        assert abs(sum(parts) - sm["total_eval_amount"]) <= tol, (seed, sum(parts), sm["total_eval_amount"])
        assert abs(sm["companies_amount"] + sm["other_amount"] + sm["undecomposed_etf_amount"] - sm["total_eval_amount"]) <= 0.02
        assert abs(sm["stock_amount"] + sm["etf_amount"] - sm["total_eval_amount"]) <= 0.02

    def test_exact_in_the_hand_example(self):
        sm = sample()["summary"]
        assert sm["companies_amount"] + sm["other_amount"] + sm["undecomposed_etf_amount"] == sm["total_eval_amount"]


class TestWeightSums:
    def _run(self, weights, qty=10, price=1000):
        etf = EtfIn("111111", "TIGER 테스트", D, [con(f"종목{i}", w, code=f"C{i}") for i, w in enumerate(weights)])
        return compute_exposure([etf_h("111111", "TIGER 테스트", qty, price)], {"111111": etf}, TODAY)

    def test_under_100_goes_to_other_and_warns_below_95(self):
        r = self._run([40, 30, 20])  # 90%
        assert r["summary"]["other_amount"] == 1000 and r["summary"]["other_weight_pct"] == 10.0
        assert [w["kind"] for w in r["data_warnings"]] == ["etf_weight_under_100"]
        r = self._run([50, 46])  # 96%: 경고 없음, 기타 4%
        assert r["summary"]["other_amount"] == 400 and r["data_warnings"] == []

    def test_over_100_warns_beyond_tolerance_and_other_is_negative(self):
        r = self._run([60, 45])  # 105%
        assert r["summary"]["other_amount"] == -500 and r["summary"]["other_weight_pct"] == -5.0
        assert [w["kind"] for w in r["data_warnings"]] == ["etf_weight_over_100"] and "100%를 넘" in r["data_warnings"][0]["message"]
        assert r["summary"]["companies_amount"] + r["summary"]["other_amount"] == r["summary"]["total_eval_amount"] == 10_000

    def test_rounding_sized_excess_does_not_warn(self):
        r = self._run([60, 40.09])  # 100.09%: 공시 반올림 수준
        assert r["data_warnings"] == []

    def test_negative_weights_are_not_company_exposure(self):
        r = self._run([70, 31, -1.5])  # 매도 옵션 같은 음수 포지션
        assert {c["name"] for c in r["companies"]} == {"종목0", "종목1"}
        assert r["summary"]["other_amount"] == -100  # 1 - 1.01 = -1% of 10,000
        assert r["decomposed_etfs"][0]["weight_sum_pct"] == 101.0


class TestUndecomposed:
    def test_no_data_etf(self):
        r = compute_exposure([stock("005930", "삼성전자", 10, 1000), etf_h("148020", "RISE 200", 10, 1000)], {}, TODAY)
        u = r["undecomposed_etfs"][0]
        assert u["ticker"] == "148020" and u["eval_amount"] == 10_000 and u["weight_pct"] == 50.0 and u["reason_code"] == REASON_NO_DATA
        assert "구성종목 데이터가 없" in u["reason"]
        assert r["summary"]["undecomposed_etf_amount"] == 10_000 and r["summary"]["companies_amount"] == 10_000
        assert [c["name"] for c in r["companies"]] == ["삼성전자"]

    def test_empty_constituent_list_counts_as_no_data(self):
        etf = EtfIn("111111", "TIGER 테스트", D, [])
        r = compute_exposure([etf_h("111111", "TIGER 테스트", 1, 1000)], {"111111": etf}, TODAY)
        assert r["undecomposed_etfs"][0]["reason_code"] == REASON_NO_DATA and r["decomposed_etfs"] == []

    @pytest.mark.parametrize("name", ["KODEX 레버리지", "KODEX 인버스", "KODEX 200선물인버스2X", "TIGER 코스닥150레버리지"])
    def test_leveraged_and_inverse_are_never_decomposed_even_with_data(self, name):
        etf = EtfIn("122630", name, D, [con("삼성전자", 50, linked="005930")])
        r = compute_exposure([etf_h("122630", name, 10, 1000), stock("000660", "SK하이닉스", 10, 1000)], {"122630": etf}, TODAY)
        u = r["undecomposed_etfs"][0]
        assert u["reason_code"] == REASON_LEVERAGED and "배수" in u["reason"] and u["eval_amount"] == 10_000
        assert [c["name"] for c in r["companies"]] == ["SK하이닉스"] and r["decomposed_etfs"] == []
        assert r["summary"]["other_amount"] == 0

    def test_only_undecomposed_gives_empty_companies_but_amounts(self):
        r = compute_exposure([etf_h("148020", "RISE 200", 10, 1000)], {}, TODAY)
        assert r["companies"] == [] and r["summary"]["total_eval_amount"] == 10_000 and r["message"] is None


class TestEmptyAndEdges:
    def test_no_holdings(self):
        r = compute_exposure([], {}, TODAY)
        assert r["message"] == MSG_NO_HOLDINGS and r["companies"] == [] and r["summary"]["total_eval_amount"] == 0
        assert r["flags"] == [] and r["valuation_date"] is None and r["limit"] == DEFAULT_LIMIT

    def test_no_prices_at_all(self):
        r = compute_exposure([stock("005930", "삼성전자", 10, None, None)], {}, TODAY)
        assert r["message"] == MSG_NO_PRICES and r["data_warnings"][0]["kind"] == "no_price" and r["summary"]["holding_count"] == 1

    def test_unpriced_holding_is_excluded_with_warning(self):
        r = compute_exposure([stock("005930", "삼성전자", 10, 1000), stock("000660", "SK하이닉스", 5, None, None)], {}, TODAY)
        assert r["summary"]["total_eval_amount"] == 10_000 and r["summary"]["holding_count"] == 1
        assert [w["kind"] for w in r["data_warnings"]] == ["no_price"] and by_name(r, "삼성전자")["effective_weight_pct"] == 100.0

    def test_older_price_warning_and_valuation_date_is_the_latest(self):
        r = compute_exposure([stock("005930", "삼성전자", 1, 1000, D), stock("000660", "SK하이닉스", 1, 1000, date(2026, 10, 27))], {}, TODAY)
        assert r["valuation_date"] == "2026-10-29" and [w["kind"] for w in r["data_warnings"]] == ["older_price"]

    def test_limit_boundaries(self):
        hs = [stock(f"{i:06d}", f"종목{i:02d}", 1, 1000 + i) for i in range(60)]
        for lim, want in [(1, 1), (10, 10), (50, 50), (51, 50), (0, 1), (10**6, 50)]:
            r = compute_exposure(hs, {}, TODAY, limit=lim)
            assert len(r["companies"]) == want and r["limit"] == want
            assert r["summary"]["company_count"] == 60
        r = compute_exposure(hs, {}, TODAY, limit=1)
        assert r["companies"][0]["name"] == "종목59" and r["companies"][0]["rank"] == 1

    def test_overlaps_are_not_limited_by_limit(self):
        etf = EtfIn("111111", "TIGER A", D, [con(f"종목{i}", 1, linked=f"{i:06d}") for i in range(20)])
        hs = [stock(f"{i:06d}", f"종목{i}", 1, 1000) for i in range(20)] + [etf_h("111111", "TIGER A", 1, 1000)]
        r = compute_exposure(hs, {"111111": etf}, TODAY, limit=3)
        assert len(r["companies"]) == 3 and len(r["overlaps"]) == 20

    def test_ranking_ties_are_stable_by_name(self):
        r = compute_exposure([stock("000002", "나", 1, 1000), stock("000001", "가", 1, 1000)], {}, TODAY)
        assert [c["name"] for c in r["companies"]] == ["가", "나"]


class TestStale:
    def test_old_constituent_date_is_stale(self):
        etf = EtfIn("111111", "TIGER 테스트", date(2026, 10, 20), [con("삼성전자", 100, linked="005930")])
        r = compute_exposure([etf_h("111111", "TIGER 테스트", 1, 1000)], {"111111": etf}, TODAY)
        d = r["decomposed_etfs"][0]
        assert d["stale"] is True and "2026-10-20" in d["stale_notice"] and r["etf_holdings_stale"] is True

    def test_fresh_constituent_date_is_not_stale(self):
        assert sample()["etf_holdings_stale"] is False


class TestWording:
    def test_no_forbidden_phrases_anywhere(self):
        etf2 = EtfIn("122630", "KODEX 레버리지", D, [])
        r = compute_exposure([stock("005930", "삼성전자", 10, 70_000), etf_h("111111", "TIGER 테스트200", 100, 30_000),
                              etf_h("122630", "KODEX 레버리지", 1, 1000), etf_h("148020", "RISE 200", 1, 1000)],
                             {"111111": SAMPLE_ETF, "122630": etf2}, TODAY)
        texts = [r["disclaimer"]] + [f["message"] for f in r["flags"]] + [w["message"] for w in r["data_warnings"]]
        texts += [u["reason"] for u in r["undecomposed_etfs"]] + [MSG_NO_HOLDINGS, MSG_NO_PRICES]
        assert r["flags"] and r["undecomposed_etfs"]
        for t in texts:
            assert forbidden_hits(t) == [], t

    def test_disclaimer_mentions_reference_and_disclosure_date(self):
        d = sample()["disclaimer"]
        assert "투자 판단 보조용" in d and "기준일" in d and "현재와 다를 수 있" in d and "투자 권유나 자문이 아니며" in d
