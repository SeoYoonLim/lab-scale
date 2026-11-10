"""ETF 실질 노출(app/look_through, app/look_through_i18n) 단위 테스트. DB 없이 합성 값으로 기대값을 손으로 계산해 대조한다.

DB 흐름(사용자 분리, 진단 저장본 스냅샷 보존, 진단 비교, 401)은 tests/test_look_through_devdb.py.
"""

import json
import re
from datetime import date, datetime

import pytest

from app import look_through as lt
from app import look_through_i18n as lti
from app.briefing import KST
from app.exposure import ConstituentIn, EtfIn, HoldingIn
from evals.metrics import forbidden_hits

D = date(2026, 10, 29)
NOW = datetime(2026, 10, 30, 20, 0, tzinfo=KST)
EXP = date(2026, 10, 30)
LOCALES = ("ko-KR", "en-US", "ja-JP", "zh-CN")


def stock(t, n, q, p, d=D):
    return HoldingIn(t, n, "stock", q, p, d)


def etf(t, n, q, p, d=D):
    return HoldingIn(t, n, "etf", q, p, d)


def c(name, w, ticker=None, code=None, kind="stock", sector=None):
    """ticker가 있으면 company와 연결된 구성종목(kind=etf면 중첩 ETF)."""
    return ConstituentIn(name, code if code is not None else ticker, w, ticker, name if ticker else None, kind if ticker else None, sector)


def run(holdings, etfs=None, cash=0.0, limit=50, now=NOW):
    return lt.compute(holdings, etfs or {}, cash, now, EXP, limit)


def co(r, name):
    return next(x for x in r["companies"] if x["name"] == name)


def invariant(r):
    s = r["summary"]
    parts = s["direct_amount"] + s["via_etf_amount"] + s["nested_etf_amount"] + s["residual_amount"] + s["undecomposed_etf_amount"]
    assert abs(parts - s["holdings_value"]) <= 0.05, (parts, s["holdings_value"])
    assert abs(s["cash_balance"] + s["holdings_value"] - s["total_asset"]) <= 0.01
    assert abs(s["stock_amount"] + s["etf_amount"] - s["holdings_value"]) <= 0.01


# ---------- 기본 공식 ----------


def test_spec_example_direct_plus_etf():
    """삼성전자 직접 100만 원 + 삼성전자 20%를 담은 ETF 200만 원 → 직접 100만, 간접 40만, 합계 140만. ETF 200만을 총자산에 다시 더하지 않는다."""
    e = EtfIn("111111", "TIGER 테스트", D, [c("삼성전자", 20, "005930"), c("SK하이닉스", 80, "000660")])
    r = run([stock("005930", "삼성전자", 10, 100_000), etf("111111", "TIGER 테스트", 100, 20_000)], {"111111": e}, cash=1_000_000)
    s = co(r, "삼성전자")
    assert (s["direct_amount"], s["via_etf_amount"], s["total_amount"]) == (1_000_000, 400_000, 1_400_000)
    assert r["summary"]["total_asset"] == 4_000_000  # 현금 100만 + 직접 100만 + ETF 200만
    assert s["total_weight_pct"] == 35.0 and s["direct_weight_pct"] == 25.0 and s["via_etf_weight_pct"] == 10.0
    assert s["exposure_type"] == "both" and [o["name"] for o in r["overlaps"]] == ["삼성전자"]
    assert co(r, "SK하이닉스")["total_amount"] == 1_600_000 and co(r, "SK하이닉스")["exposure_type"] == "etf"
    invariant(r)


def test_stock_only():
    r = run([stock("005930", "삼성전자", 10, 70_000), stock("000660", "SK하이닉스", 1, 300_000)], cash=0)
    assert [x["name"] for x in r["companies"]] == ["삼성전자", "SK하이닉스"]
    assert r["summary"]["via_etf_amount"] == 0 and r["summary"]["unidentified_amount"] == 0 and r["etfs"] == []
    assert co(r, "삼성전자")["total_weight_pct"] == 70.0
    invariant(r)


def test_etf_only_exact_100():
    e = EtfIn("111111", "TIGER A", D, [c("삼성전자", 60, "005930"), c("SK하이닉스", 40, "000660")])
    r = run([etf("111111", "TIGER A", 10, 10_000)], {"111111": e})
    assert r["summary"]["direct_amount"] == 0 and r["summary"]["via_etf_amount"] == 100_000
    assert r["summary"]["residual_amount"] == 0 and r["summary"]["unidentified_amount"] == 0 and r["data_warnings"] == []
    row = r["etfs"][0]
    assert row["status"] == "decomposed" and row["positive_weight_sum_pct"] == 100 and row["allocated_amount"] == 100_000
    assert [x["weight_pct"] for x in row["constituents"]] == [60, 40] and row["constituents"][0]["amount"] == 60_000
    invariant(r)


def test_same_company_in_several_etfs_is_summed_with_contributions():
    a = EtfIn("111111", "TIGER A", D, [c("삼성전자", 30, "005930"), c("기타", 70, code="X")])
    b = EtfIn("222222", "KODEX B", D, [c("삼성전자", 10, "005930"), c("기타2", 90, code="Y")])
    r = run([etf("111111", "TIGER A", 10, 10_000), etf("222222", "KODEX B", 20, 10_000), stock("005930", "삼성전자", 1, 50_000)],
            {"111111": a, "222222": b}, cash=50_000)
    s = co(r, "삼성전자")
    assert s["via_etf_amount"] == 30_000 + 20_000 and s["direct_amount"] == 50_000 and s["total_amount"] == 100_000
    assert [(v["ticker"], v["amount"], v["constituent_weight_pct"]) for v in s["via_etfs"]] == [("111111", 30_000, 30), ("222222", 20_000, 10)]
    assert r["summary"]["total_asset"] == 400_000 and s["total_weight_pct"] == 25.0
    invariant(r)


# ---------- 비중 합계·품질 ----------


def test_under_100_is_unidentified_not_redistributed():
    e = EtfIn("111111", "TIGER A", D, [c("삼성전자", 50, "005930"), c("SK하이닉스", 30, "000660")])  # 80%
    r = run([etf("111111", "TIGER A", 1, 100_000)], {"111111": e})
    assert co(r, "삼성전자")["total_amount"] == 50_000 and co(r, "SK하이닉스")["total_amount"] == 30_000  # 나머지를 나눠 주지 않는다
    assert r["summary"]["residual_amount"] == 20_000 and r["summary"]["unidentified_amount"] == 20_000
    assert r["summary"]["unidentified_weight_pct"] == 20.0
    w = [x for x in r["data_warnings"] if x["kind"] == "etf_weight_under_100"][0]
    assert w["pct"] == 80.0 and w["rest"] == 20.0 and w["amount"] == 20_000
    invariant(r)


def test_over_100_is_not_normalized():
    e = EtfIn("111111", "TIGER A", D, [c("삼성전자", 70, "005930"), c("SK하이닉스", 40, "000660")])  # 110%
    r = run([etf("111111", "TIGER A", 1, 100_000)], {"111111": e})
    assert co(r, "삼성전자")["total_amount"] == 70_000 and co(r, "SK하이닉스")["total_amount"] == 40_000
    assert r["summary"]["residual_amount"] == -10_000 and r["summary"]["over_allocated_amount"] == 10_000
    assert r["summary"]["unidentified_amount"] == 0
    assert any(x["kind"] == "etf_weight_over_100" and x["pct"] == 110.0 for x in r["data_warnings"])
    invariant(r)


def test_negative_zero_null_weights():
    e = EtfIn("111111", "TIGER A", D, [c("삼성전자", 90, "005930"), c("숏", -2, code="S"), c("영", 0, code="Z"), c("빈", None, code="N")])
    r = run([etf("111111", "TIGER A", 1, 100_000)], {"111111": e})
    assert [x["name"] for x in r["companies"]] == ["삼성전자"]
    kinds = {x["kind"] for x in r["data_warnings"]}
    assert {"etf_negative_weights", "etf_null_weights"} <= kinds
    row = r["etfs"][0]
    assert (row["negative_weight_count"], row["zero_weight_count"], row["null_weight_count"]) == (1, 1, 1)
    assert r["summary"]["residual_amount"] == 10_000
    invariant(r)


def test_single_weight_over_100_is_invalid_and_not_decomposed():
    e = EtfIn("111111", "TIGER A", D, [c("삼성전자", 150, "005930")])
    r = run([etf("111111", "TIGER A", 1, 100_000)], {"111111": e})
    row = r["etfs"][0]
    assert row["status"] == "not_decomposed" and row["reason_code"] == "invalid_weights" and r["companies"] == []
    assert r["summary"]["undecomposed_etf_amount"] == 100_000 and r["summary"]["unidentified_amount"] == 100_000
    invariant(r)


def test_duplicate_rows_are_summed_and_warned():
    e = EtfIn("111111", "TIGER A", D, [c("삼성전자", 25, "005930"), c("삼성전자", 5, "005930"), c("기타", 70, code="X")])
    r = run([etf("111111", "TIGER A", 1, 100_000)], {"111111": e})
    assert co(r, "삼성전자")["via_etf_amount"] == 30_000
    assert any(x["kind"] == "etf_duplicate_constituents" and x["count"] == 1 for x in r["data_warnings"])


def test_unlinked_constituents_group_by_name():
    a = EtfIn("111111", "TIGER A", D, [c("NVIDIA Corp", 60, code="NVDA US EQUITY"), c("국고채", 40, code=None)])
    b = EtfIn("222222", "TIGER B", D, [c("nvidia  corp", 100, code=None)])
    r = run([etf("111111", "TIGER A", 1, 10_000), etf("222222", "TIGER B", 1, 10_000)], {"111111": a, "222222": b})
    n = co(r, "NVIDIA Corp")
    assert n["linked"] is False and n["ticker"] is None and n["total_amount"] == 16_000
    assert r["etfs"][0]["unlinked_count"] == 2 and any(x["kind"] == "etf_unlinked_constituents" for x in r["data_warnings"])
    assert r["summary"]["unlinked_via_amount"] == 20_000 and r["summary"]["linked_via_amount"] == 0


def test_same_issuer_different_codes_are_not_duplicates():
    """같은 발행사 이름의 서로 다른 증권(ISIN이 다른 CP)은 중복이 아니다(dev DB의 머니마켓 ETF). 노출은 이름으로 합산한다."""
    e = EtfIn("111111", "KODEX 머니마켓", D, [c("케이비제일차(CP)", 30, code="KRZF0001"), c("케이비제일차(CP)", 20, code="KRZF0002"),
                                              c("국고채", 50, code="KR103")])
    r = run([etf("111111", "KODEX 머니마켓", 1, 100_000)], {"111111": e})
    assert r["etfs"][0]["duplicate_count"] == 0 and not any(x["kind"] == "etf_duplicate_constituents" for x in r["data_warnings"])
    assert co(r, "케이비제일차(CP)")["total_amount"] == 50_000


def test_stale_constituents():
    e = EtfIn("111111", "TIGER A", date(2026, 10, 20), [c("삼성전자", 100, "005930")])
    r = run([etf("111111", "TIGER A", 1, 1000)], {"111111": e})
    row = r["etfs"][0]
    assert row["holdings_stale"] is True and row["holdings_lag_business_days"] == 8 and r["etf_holdings_stale"] is True
    assert any(x["kind"] == "etf_holdings_stale" and x["date"] == "2026-10-20" for x in r["data_warnings"])


# ---------- 분해하지 않는 경우 ----------


@pytest.mark.parametrize("name", ["KODEX 레버리지", "KODEX 인버스", "KODEX 200선물인버스2X"])
def test_leveraged_inverse_shows_disclosed_weights_without_exposure(name):
    e = EtfIn("122630", name, D, [c("삼성전자", 30, "005930"), c("KODEX 200", 22.69, "069500", kind="etf"), c("선물", 150, code="F")])
    r = run([etf("122630", name, 10, 10_000), stock("000660", "SK하이닉스", 1, 100_000)], {"122630": e})
    row = r["etfs"][0]
    assert row["status"] == "not_decomposed" and row["reason_code"] == "leveraged_inverse" and row["leverage_kind"]
    assert [x["name"] for x in r["companies"]] == ["SK하이닉스"]  # 배수를 곱하거나 구성 비중으로 기업 노출을 만들지 않는다
    assert row["constituents"] and all(x["amount"] is None for x in row["constituents"])
    assert r["summary"]["undecomposed_etf_amount"] == 100_000 and r["summary"]["unidentified_amount"] == 100_000
    out = lti.localize(r, "ko-KR")
    assert out["etfs"][0]["notice"] == "공시 구성 비중이며 경제적 노출이 아닙니다(배수를 곱하지 않음)."
    assert "배수를 곱해 노출을 확정하지 않습니다" in next(w["message"] for w in out["data_warnings"] if w["kind"] == "etf_leveraged_inverse")
    invariant(r)


def test_nested_etf_is_not_recursed_or_counted_as_company():
    inner = EtfIn("069500", "KODEX 200", D, [c("삼성전자", 100, "005930")])
    outer = EtfIn("472150", "TIGER 배당커버드콜액티브", D, [c("KT&G", 50, "033780"), c("TIGER 200", 30, "102110", kind="etf"),
                                                            c("자기자신", 5, "472150", kind="etf")])
    r = run([etf("472150", "TIGER 배당커버드콜액티브", 1, 100_000)], {"472150": outer, "069500": inner})
    assert [x["name"] for x in r["companies"]] == ["KT&G"]  # 하위 ETF·자기 참조는 기업이 아니다
    row = r["etfs"][0]
    assert row["nested_etf_amount"] == 35_000 and row["nested_etf_count"] == 2 and [n["linked_ticker"] for n in row["nested_etfs"]] == ["102110", "472150"]
    assert r["summary"]["nested_etf_amount"] == 35_000 and r["summary"]["unidentified_amount"] == 35_000 + 15_000
    assert any(x["kind"] == "etf_nested_etf" and x["count"] == 2 for x in r["data_warnings"])
    invariant(r)


def test_etf_without_data():
    r = run([etf("148020", "RISE 200", 1, 10_000), stock("005930", "삼성전자", 1, 10_000)], {})
    row = r["etfs"][0]
    assert row["status"] == "not_decomposed" and row["reason_code"] == "no_holdings_data" and row["constituents"] == []
    assert r["summary"]["unidentified_amount"] == 10_000 and co(r, "삼성전자")["total_weight_pct"] == 50.0


# ---------- 가격 누락·계좌 ----------


def test_missing_etf_price_is_not_zero():
    e = EtfIn("111111", "TIGER A", D, [c("삼성전자", 100, "005930")])
    r = run([etf("111111", "TIGER A", 10, None, None), stock("000660", "SK하이닉스", 1, 100_000)], {"111111": e}, cash=100_000)
    assert r["summary"]["total_asset"] == 200_000 and r["summary"]["unpriced_count"] == 1
    assert r["unpriced"] == [{"ticker": "111111", "name": "TIGER A", "asset_type": "etf", "quantity": 10}]
    assert "삼성전자" not in [x["name"] for x in r["companies"]] and r["etfs"] == []
    assert [w["kind"] for w in r["data_warnings"]] == ["no_price"]


def test_missing_direct_price_and_mixed_dates():
    r = run([stock("005930", "삼성전자", 1, None, None), stock("000660", "SK하이닉스", 1, 1000, D), stock("035420", "NAVER", 1, 1000, date(2026, 10, 27))])
    assert r["summary"]["priced_holding_count"] == 2 and r["valuation_date"] == "2026-10-29"
    assert {w["kind"] for w in r["data_warnings"]} == {"no_price", "older_price"}
    assert r["price_lag_business_days"] == 1  # 기대 거래일 10-30


def test_constituent_prices_are_not_needed():
    """간접 노출은 ETF 평가액 x 공시 비중이라 구성종목의 시세가 없어도 계산된다(구성종목 가격은 입력에 없다)."""
    e = EtfIn("111111", "TIGER A", D, [c("가격없는기업", 100, "999999")])
    assert run([etf("111111", "TIGER A", 1, 5000)], {"111111": e})["companies"][0]["total_amount"] == 5000


def test_no_account_and_empty_portfolio():
    r = run([], {}, cash=None)
    assert r["status"] == "empty" and r["has_account"] is False and r["summary"]["total_asset"] == 0 and r["companies"] == []
    assert lti.localize(r, "ko-KR")["message"].startswith("보유 중인 종목이 없습니다")
    r = run([], {}, cash=5_000_000)
    assert r["status"] == "empty" and r["has_account"] is True and r["summary"]["cash_weight_pct"] == 100.0
    r = run([stock("005930", "삼성전자", 1, None, None)], {}, cash=1000)
    assert r["status"] == "no_prices" and lti.localize(r, "en-US")["message"].startswith("Look-through exposure can't")


# ---------- 이중 계산·불변식 ----------


@pytest.mark.parametrize("seed", range(30))
def test_no_double_counting_random(seed):
    import random

    rng = random.Random(seed)
    hs, etfs = [], {}
    names = [("삼성전자", "005930"), ("SK하이닉스", "000660"), ("현대차", "005380"), ("NAVER", "035420")]
    for i in range(rng.randint(0, 4)):
        n, t = names[i]
        hs.append(stock(t, n, rng.randint(1, 50), rng.choice([None, round(rng.uniform(1000, 300_000), 2)])))
    for k in range(rng.randint(0, 4)):
        t = f"9{k}0000"
        cons = []
        for j in range(rng.randint(0, 10)):
            n, tk = rng.choice(names)
            kind = rng.random()
            w = rng.choice([None, 0, round(rng.uniform(-3, 40), 4)])
            if kind < 0.15:
                cons.append(c("하위ETF", w, "88" + str(j) * 4, kind="etf"))
            elif kind < 0.5:
                cons.append(c(n, w, tk))
            else:
                cons.append(c(f"해외{j}", w, code=f"X{j}"))
        nm = rng.choice(["TIGER 가짜", "KODEX 레버리지", "TIGER 가짜B"])
        hs.append(etf(t, f"{nm}{k}", rng.randint(1, 100), rng.choice([None, round(rng.uniform(1000, 50_000), 2)])))
        etfs[t] = EtfIn(t, hs[-1].name, D, cons)
    r = run(hs, etfs, cash=rng.choice([None, 0, 1_000_000]))
    invariant(r)
    s = r["summary"]
    assert abs(sum(x["total_amount"] for x in r["companies"]) - s["companies_amount"]) <= 0.01 * (len(r["companies"]) + 1) or s["company_count"] > 50


# ---------- 업종 ----------


def test_sector_status():
    none = run([stock("005930", "삼성전자", 1, 1000)])
    assert none["sector"]["status"] == "unavailable" and none["sector"]["items"] == []
    e = EtfIn("111111", "TIGER A", D, [c("삼성전자", 50, "005930", sector="반도체"), c("현대차", 50, "005380")])
    part = run([etf("111111", "TIGER A", 1, 1000)], {"111111": e})
    assert part["sector"]["status"] == "partial" and part["sector"]["covered_weight_pct"] == 50.0
    e2 = EtfIn("111111", "TIGER A", D, [c("삼성전자", 100, "005930", sector="반도체")])
    full = run([etf("111111", "TIGER A", 1, 1000)], {"111111": e2})
    assert full["sector"]["status"] == "available" and full["sector"]["items"] == [{"sector": "반도체", "amount": 1000}]


# ---------- 플래그 ----------


def test_flags_use_total_assets():
    e = EtfIn("111111", "TIGER A", D, [c("삼성전자", 50, "005930"), c("기타", 50, code="X")])
    r = run([stock("005930", "삼성전자", 1, 300_000), etf("111111", "TIGER A", 1, 400_000)], {"111111": e}, cash=300_000)
    kinds = {(f["kind"], f["name"]) for f in r["flags"]}
    # 총자산 100만: 삼성전자 직접 30만 + 간접 20만 = 50% (기준 40%), 간접 20% (기준 10%p), 한 ETF 20% (기준 10%)
    assert {("concentration", "삼성전자"), ("etf_inflates_weight", "삼성전자"), ("single_etf_exposure", "삼성전자")} <= kinds
    big_cash = run([stock("005930", "삼성전자", 1, 300_000), etf("111111", "TIGER A", 1, 400_000)], {"111111": e}, cash=9_000_000)
    assert not any(f["kind"] == "concentration" for f in big_cash["flags"])  # 현금이 분모에 들어간다


# ---------- 언어 ----------


def _sample():
    a = EtfIn("111111", "TIGER A", date(2026, 10, 20), [c("삼성전자", 40, "005930"), c("TIGER 200", 10, "102110", kind="etf"),
                                                       c("NVIDIA", 30, code="NVDA US"), c("숏", -1, code="S")])
    lev = EtfIn("122630", "KODEX 레버리지", D, [c("삼성전자", 60, "005930")])
    return run([stock("005930", "삼성전자", 3, 100_000), etf("111111", "TIGER A", 10, 50_000), etf("122630", "KODEX 레버리지", 5, 20_000),
                stock("000660", "SK하이닉스", 1, None, None)], {"111111": a, "122630": lev}, cash=200_000)


NUMERIC_KEYS = ("summary", "companies", "overlaps", "unpriced", "valuation_date", "expected_date", "price_lag_business_days", "calc_version", "status")


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in ("message", "notice")}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def test_numbers_identical_across_locales_and_texts_in_script():
    r = _sample()
    outs = {L: lti.localize(r, L) for L in LOCALES}
    ko = outs["ko-KR"]
    for L, o in outs.items():
        for k in NUMERIC_KEYS:
            assert o[k] == ko[k], (L, k)
        assert _strip(o["etfs"]) == _strip(ko["etfs"]) and _strip(o["flags"]) == _strip(ko["flags"])
        assert _strip(o["data_warnings"]) == _strip(ko["data_warnings"]) and o["sector"]["status"] == ko["sector"]["status"]
        texts = [w["message"] for w in o["data_warnings"]] + [f["message"] for f in o["flags"]] + [o["sector"]["message"], o["disclaimer"]]
        texts += [e["notice"] for e in o["etfs"] if e["notice"]] + ([o["stale_notice"]] if o["stale_notice"] else [])
        assert all(texts) and not any("{" in t for t in texts)
        nums = {n.replace(",", "") for t in texts for n in re.findall(r"\d[\d,]*(?:\.\d+)?", t)}
        ko_nums = {n.replace(",", "") for t in [w["message"] for w in ko["data_warnings"]] + [f["message"] for f in ko["flags"]]
                   + [ko["sector"]["message"], ko["disclaimer"]] + [e["notice"] for e in ko["etfs"] if e["notice"]]
                   + ([ko["stale_notice"]] if ko["stale_notice"] else []) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", t)}
        assert nums <= ko_nums | {"100"}, (L, nums - ko_nums)
        if L != "ko-KR":
            rest = " ".join(texts)
            for n in ("삼성전자", "SK하이닉스", "TIGER A", "KODEX 레버리지", "TIGER 200"):
                rest = rest.replace(n, "")
            assert not re.search(r"[가-힣]", rest), (L, re.findall(r"[가-힣]+", rest)[:5])  # 종목·ETF 이름 말고 한글이 없다
    assert outs["ja-JP"]["data_warnings"][0]["message"].endswith("。")


def test_no_advice_in_any_locale():
    r = _sample()
    for L in LOCALES:
        assert forbidden_hits(json.dumps(lti.localize(r, L), ensure_ascii=False)) == []


# ---------- 스냅샷·비교 ----------


def test_snapshot_keeps_constituents_and_dates():
    snap = lt.snapshot(_sample())
    a = next(e for e in snap["etfs"] if e["ticker"] == "111111")
    assert a["holdings_as_of"] == "2026-10-20" and a["constituents"][0] == {
        "name": "삼성전자", "code": "005930", "linked_ticker": "005930", "weight_pct": 40.0, "amount": 200_000.0, "kind": "company"}
    assert snap["calc_version"] == lt.CALC_VERSION and json.dumps(snap)


def test_compare_changes_and_none_without_snapshots():
    prev = lt.snapshot(run([stock("005930", "삼성전자", 10, 100_000)], cash=1_000_000))
    e = EtfIn("111111", "TIGER A", D, [c("삼성전자", 50, "005930"), c("SK하이닉스", 50, "000660")])
    cur = lt.snapshot(run([stock("005930", "삼성전자", 10, 100_000), etf("111111", "TIGER A", 10, 100_000)], {"111111": e}, cash=0))
    out = lt.compare(cur, prev)
    s = {r["name"]: r for r in out["companies"]}
    assert s["삼성전자"]["previous_total_weight_pct"] == 50.0 and s["삼성전자"]["current_total_weight_pct"] == 75.0 and s["삼성전자"]["change_pct"] == 25.0
    assert s["SK하이닉스"]["status"] == "added" and s["SK하이닉스"]["change_pct"] == 25.0
    assert out["summary"]["via_etf_amount"] == {"previous": 0.0, "current": 1_000_000.0, "change": 1_000_000.0}
    assert lt.compare(cur, None) is None and lt.compare(None, prev) is None
    assert lt.compare(cur, {"status": "unavailable"}) is None
