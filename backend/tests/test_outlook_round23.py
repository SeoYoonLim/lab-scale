"""2026-11-04(23차): 실적 분기 변환·4분기·이상치, 국면 규칙, 같은 사실 중복 제거, 결론 먼저 답변 구조, 짧은 후속 답, 조회 기간 표시,
지수 날짜 안내. DB·LLM 없음."""

import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app import earnings, outlook
from app.briefing import KST
from app.disclaimer import DISCLAIMER
from evals.forecast_check import assertion_hits
from evals.metrics import forbidden_hits

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=KST)
COMPANY = NS(id=20, name="SK하이닉스", ticker="000660", market="KOSPI", asset_type="stock")
W1 = outlook.PERIODS["1주일"]


# ---------- 실적: 분기(3개월) 값, 4분기 = 연간 - 3분기 누적, 이상치 ----------

def _er(year, code, rev, op, net, cum=None, prev=(None, None, None), fs="CFS"):
    cum = cum or (None, None, None)
    return NS(bsns_year=year, reprt_code=code, fs_div=fs, revenue=rev, operating_income=op, net_income=net,
              revenue_prev=prev[0], operating_income_prev=prev[1], net_income_prev=prev[2],
              revenue_cum=cum[0], operating_income_cum=cum[1], net_income_cum=cum[2],
              rcept_no=f"{year}0101000001", disclosed_date=date(year, 1, 1))


def _year(y, q=(100, 110, 120), annual=460, q4_from_cum=True):
    """1~3분기 매출 q, 연간 annual. 3분기 보고서 누적 = 1~3분기 합."""
    rows = [_er(y, "11013", q[0], 10, 5, cum=(q[0], 10, 5)),
            _er(y, "11012", q[1], 11, 6, cum=(q[0] + q[1], 21, 11)),
            _er(y, "11014", q[2], 12, 7, cum=(sum(q), 33, 18) if q4_from_cum else None),
            _er(y, "11011", annual, 50, 30)]
    return rows


def test_q4_is_annual_minus_third_quarter_cumulative():
    s = earnings.quarterly_series(_year(2025))
    assert s[(2025, 4)]["revenue"] == 460 - 330 and s[(2025, 4)]["operating_income"] == 50 - 33
    assert s[(2025, 4)]["net_income"] == 30 - 18 and s[(2025, 4)]["derived"] is True
    assert [s[(2025, q)]["revenue"] for q in (1, 2, 3)] == [100, 110, 120]


def test_q4_falls_back_to_the_sum_of_quarters_without_cumulative():
    s = earnings.quarterly_series(_year(2025, q4_from_cum=False))
    assert s[(2025, 4)]["revenue"] == 460 - (100 + 110 + 120)


def test_q4_is_not_made_when_consolidated_and_separate_are_mixed():
    rows = _year(2025)
    rows[1] = _er(2025, "11012", 110, 11, 6, fs="OFS")
    assert (2025, 4) not in earnings.quarterly_series(rows)


def test_q4_yoy_compares_fourth_quarter_with_fourth_quarter():
    s = earnings.quarterly_series(_year(2024, annual=400) + _year(2025, annual=460))
    assert s[(2025, 4)]["revenue_prev"] == 400 - 330  # 전년 4분기(연간 - 3분기 누적)이지 전년 연간이 아니다
    assert s[(2025, 2)]["revenue_prev"] == 110  # 보고서에 전기 금액이 없으면 전년 같은 분기 행


def test_second_quarter_is_three_months_not_half_year_cumulative():
    """SK하이닉스 2026년 반기보고서: 당기 금액(3개월) 939,226억, 누적 1,342,685억 = 1분기 403,459억 + 2분기."""
    e = 1e8
    rows = [_er(2026, "11013", 525_763 * e, 376_103 * e, 403_459 * e, cum=(525_763 * e, 376_103 * e, 403_459 * e)),
            _er(2026, "11012", 793_187 * e, 605_426 * e, 939_226 * e, cum=(1_318_950 * e, 981_529 * e, 1_342_685 * e))]
    s = earnings.quarterly_series(rows)
    assert s[(2026, 2)]["net_income"] == 939_226 * e
    found = earnings.anomalies(rows)
    assert any("|당기순이익|" in x for x in found)  # 원본이 그런 값이라 목록에는 올라간다
    assert not any("합" in x or "분기 값" in x for x in found)  # 누적이 섞인 것은 아니다


def test_anomalies_flag_sum_mismatch_quarter_above_cumulative_and_big_yoy():
    rows = [_er(2025, "11013", 100, 10, 5, cum=(100, 10, 5)),
            _er(2025, "11012", 300, 11, 6, cum=(250, 21, 11), prev=(None, None, 0.5))]
    found = earnings.anomalies(rows)
    assert any("1~2분기 합 400 != 누적 250" in x for x in found)
    assert any("분기 값 300 > 누적 250" in x for x in found)
    assert any("net_income: 전년 동기 대비 +1100%" in x for x in found)


def test_quarter_above_cumulative_is_normal_after_a_loss():
    rows = [_er(2025, "11013", 100, -50, -40, cum=(100, -50, -40)), _er(2025, "11012", 100, 30, 20, cum=(200, -20, -20))]
    assert not any("분기 값" in x for x in earnings.anomalies(rows))


def test_operating_income_above_revenue_means_the_revenue_account_was_misread():
    rows = [_er(2026, "11013", 3_000, 80_000, 80_000, cum=(3_000, 80_000, 80_000))]
    assert any("매출 계정 인식 의심" in x for x in earnings.anomalies(rows))

    class Db:
        def query(self, *a):
            return self

        def filter(self, *a):
            return self

        def all(self):
            return rows

        def order_by(self, *a):  # 잠정실적 공시 조회(app/preliminary.py): 없음
            return NS(all=lambda: [])

    assert earnings.recent_for_answer(Db(), NS(id=1, asset_type="stock")) is None  # 틀린 값 대신 섹션 생략
    rows[:] = [_er(2026, "11013", None, None, 5, cum=(None, None, 5))]  # 금융사처럼 매출 계정을 못 찾은 경우
    assert earnings.recent_for_answer(Db(), NS(id=1, asset_type="stock")) is None


def test_earnings_lines_explain_net_income_above_revenue():
    rows = [{"revenue": 7.9e13, "revenue_prev": 2.2e13, "operating_income": 6.0e13, "operating_income_prev": 9.2e12,
             "net_income": 9.4e13, "net_income_prev": 7.0e12, "period_label": "2026년 2분기", "fs_div": "CFS",
             "rcept_no": "20260814003509", "disclosed_date": "2026-08-14"}]
    assert "DART 공시 값 그대로" in outlook.earnings_lines(rows)[-1]
    rows[0]["derived"] = True
    assert "(연결, 확정, 연간 - 3분기 누적)" in outlook.earnings_lines(rows)[1]


# ---------- 국면 규칙 ----------

SKHYNIX_20261103 = {"close": 1_414_000.0, "regime_inputs": {
    "r5": 2.91, "r20": -23.24, "r60": -10.34, "ma5": 1_461_800.0, "ma20": 1_421_300.0, "ma60": 1_604_583.33, "from_low20_pct": 18.82}}


def test_skhynix_on_2026_11_03_is_a_rebound_attempt():
    assert outlook.classify_regime(SKHYNIX_20261103, outlook.LABEL_UP) == "단기 반등 시도, 추세 전환은 미확인"


def _regime(close, r5, r20, r60, ma5, ma20, ma60, low=0.0):
    return {"close": close, "regime_inputs": {"r5": r5, "r20": r20, "r60": r60, "ma5": ma5, "ma20": ma20, "ma60": ma60,
                                               "from_low20_pct": low}}


@pytest.mark.parametrize("ind,want", [
    (_regime(120, 1.0, 8.0, 20.0, 118, 115, 105), outlook.REGIME_UP),
    (_regime(110, -4.0, 1.0, 15.0, 113, 112, 100), outlook.REGIME_PULLBACK),
    (_regime(80, -2.0, -8.0, -12.0, 82, 85, 95), outlook.REGIME_DOWN),
    (_regime(80, 3.0, -8.0, -20.0, 79, 85, 95, 6.0), outlook.REGIME_REBOUND),  # 60거래일 큰 하락 뒤 반등
    (_regime(100, 0.5, 1.0, 2.0, 100, 99, 99), outlook.REGIME_FLAT),
])
def test_regime_rules(ind, want):
    assert outlook.classify_regime(ind, outlook.LABEL_NEUTRAL) == want


def test_regime_holds_without_sixty_days_or_on_hold_label():
    ind = _regime(100, 1, 1, None, 100, 100, None)
    assert outlook.classify_regime(ind, outlook.LABEL_UP) == outlook.REGIME_HOLD
    assert outlook.classify_regime(SKHYNIX_20261103, outlook.LABEL_HOLD) == outlook.REGIME_HOLD


def _rows(n=260, seed=7):
    import random

    rnd = random.Random(seed)
    d, price, out = date(2025, 1, 2), 10000.0, []
    while len(out) < n:
        if d.weekday() < 5:
            price = max(100.0, round(price * (1 + rnd.uniform(-0.04, 0.04))))
            out.append((d, price, rnd.randint(1000, 5000)))
        d += timedelta(days=1)
    return out


def test_regime_inputs_match_an_independent_calculation():
    rows = _rows()
    closes = [c for _, c, _ in rows]
    ri = outlook.compute_indicators(rows, W1)["regime_inputs"]
    for n in (5, 20, 60):
        assert ri[f"r{n}"] == round((closes[-1] / closes[-1 - n] - 1) * 100, 2)
        assert ri[f"ma{n}"] == round(sum(closes[-n:]) / n, 2)
        assert ri[f"r{n}_from"] == (rows[-1 - n][0].isoformat(), closes[-1 - n])
    assert ri["from_low20_pct"] == round((closes[-1] / min(closes[-21:]) - 1) * 100, 2)


# ---------- 같은 사실은 한 표 ----------

def _sig(kind, side, name=None):
    return {"kind": kind, "group": outlook.SIGNAL_GROUPS[kind], "side": side, "name": name or kind}


def test_golden_cross_and_short_above_long_are_not_two_votes():
    """골든크로스와 5일선 > 20일선은 같은 사실이다. 같은 묶음이고, 묶음 안에서도 한 번만 센다."""
    sig = [_sig("ma_alignment", "up", "5일선이 20일선 위"), _sig("cross", "up", "2026-11-02 골든크로스"),
           _sig("close_vs_ma_long", "down"), _sig("close_vs_ma_short", "down"), _sig("period_return", "up"), _sig("relative", "up"),
           _sig("rsi", "neutral"), _sig("macd", "up"), _sig("volume", "neutral")]
    assert outlook.SIGNAL_GROUPS["cross"] == outlook.SIGNAL_GROUPS["ma_alignment"]
    votes = outlook.group_votes(sig)
    assert votes["중기 추세"] == "neutral"  # 20일선 아래(하락) 1 : 배열·교차(상승) 1
    assert sum(v == "up" for v in votes.values()) == 2
    line = next(x for x in outlook.group_lines(sig) if x.startswith("  - 중기 추세"))
    assert "골든크로스(상승 쪽, '5일선이 20일선 위'와 같은 사실이라 세지 않음)" in line


def test_cross_alone_still_counts_when_alignment_is_missing():
    assert outlook.group_votes([_sig("cross", "down"), _sig("rsi", "neutral")])["중기 추세"] == "down"


# ---------- 결론 먼저 답변 ----------

def _result(rows=None, period="1주일", hold=False, index_rows=None):
    p = outlook.PERIODS[period]
    ind = outlook.compute_indicators(rows or _rows(), p, index_rows)
    sig = outlook.classify_signals(ind, p)
    data = outlook.data_check(date.fromisoformat(ind["base_date"]), NOW, [date(2026, 1, 5)] if hold else [])
    return {"indicators": ind, "signals": sig, "label": outlook.judge(sig, data), "data": data}


def _now_for(rows):
    last = rows[-1][0]
    return datetime(last.year, last.month, last.day, 18, 0, tzinfo=KST)


def test_answer_order_title_state_summary_then_details():
    rows = _rows()
    text = outlook.forecast_answer(COMPANY, W1, _result(rows), [], [], None, _now_for(rows))
    lines = text.splitlines()
    assert lines[0].startswith("SK하이닉스(000660) · 향후 5거래일 · 방향: ")  # 25차: 제목 줄에 방향
    assert lines[2].startswith("현재는 ") and any(r in lines[2] for r in outlook.REGIMES)
    titles = [line[3:] for line in lines if line.startswith("## ")]
    assert titles[:5] == ["종합 판단", "상승을 뒷받침하는 근거", "하락 위험 근거", "앞으로 확인할 사항", "과거 검증"]
    assert titles[5:] == ["기술적 지표", "뉴스·공시(직접 관련)", "시장 상황", "신호 판정 상세", "불확실성과 한계", "데이터 기준일·출처"]
    first_screen = text.split("## 상승을 뒷받침하는 근거")[0]
    assert "규칙" not in first_screen and "상승 우위" not in lines[0] + lines[2]
    rules_at = text.index("## 신호 판정 상세")
    assert all(m.start() > rules_at for m in re.finditer(r"판정 규칙|국면 규칙|규칙:", text))
    assert text.count(DISCLAIMER) == 1
    assert assertion_hits(text) == [] and forbidden_hits(text) == []
    assert outlook.marker_of(text) == {"name": "SK하이닉스", "ticker": "000660", "period": "1주일"}


@pytest.mark.parametrize("period,days", [("1개월", 20), ("3개월", 60)])
def test_title_line_follows_the_period(period, days):
    rows = _rows()
    text = outlook.forecast_answer(COMPANY, outlook.PERIODS[period], _result(rows, period), [], [], None, _now_for(rows))
    assert text.splitlines()[0].startswith(f"SK하이닉스(000660) · 향후 {days}거래일 · 방향: ")
    assert outlook.marker_of(text)["period"] == period


def _skhynix_like():
    ri = dict(SKHYNIX_20261103["regime_inputs"], r5_from=("2026-10-27", 1_374_000.0), r20_from=("2026-10-02", 1_842_000.0),
              r60_from=("2026-08-04", 1_577_000.0))
    ind = {"close": 1_414_000.0, "base_date": "2026-11-03", "regime_inputs": ri,
           "ma5": {"value": 1_461_800.0, "gap_pct": -3.27}, "ma20": {"value": 1_421_300.0, "gap_pct": -0.51},
           "returns": {5: {"pct": 2.91, "from_date": "2026-10-27", "from_close": 1_374_000.0}}, "day_change_pct": -4.46,
           "volume": {"value": 2_291_781, "avg": 3_635_655, "ratio": 0.63}, "macd": {"macd": -64_873.44, "signal": -88_478.87},
           "rsi": 49.49, "relative": {"period_days": 5, "index_name": "코스피", "diff_pct_point": 2.92},
           "range": {"days": 21, "low": 1_190_000.0, "low_date": "2026-10-20", "high": 1_842_000.0, "high_date": "2026-10-02"},
           "range52": {"days": 251, "low": 510_000.0, "low_date": "2025-10-24", "high": 2_919_000.0, "high_date": "2026-06-22"}}
    sig = [_sig("close_vs_ma_short", "down", "종가가 5일 이동평균 아래"), _sig("close_vs_ma_long", "down", "종가가 20일 이동평균 아래"),
           _sig("ma_alignment", "up", "5일선이 20일선 위"), _sig("cross", "up", "2026-11-02 골든크로스"),
           _sig("period_return", "up", "5거래일 수익률 +2.91%"), _sig("rsi", "neutral"), _sig("macd", "up", "MACD가 시그널 위"),
           _sig("volume", "neutral"), _sig("relative", "up", "지수보다 강한 흐름")]
    data = {"hold": False, "warnings": []}
    return {"indicators": ind, "signals": sig, "label": outlook.judge(sig, data), "data": data}


@pytest.fixture
def backtest(monkeypatch):
    bt = {"periods": {"1주일": {"valid": {"first_date": "2026-01-02", "last_date": "2026-10-27", "horizon": 5, "labels": {
        "상승 우위": {"n": 22176, "up_rate": 0.518, "base_rate": 0.499, "diff": 0.019, "diff_ci95": [-0.014, 0.053], "significant": False}}}}}}
    monkeypatch.setattr(outlook, "backtest_summary", lambda: bt)
    return bt


def test_summary_does_not_claim_dominance_when_regime_disagrees(backtest):
    res = _skhynix_like()
    regime = outlook.classify_regime(res["indicators"], res["label"])
    text = outlook.summary_lines(res, regime, W1)[0]
    assert res["label"] == "상승 우위" and "우세" not in text
    assert "20거래일 -23.24% 급락 뒤의 반등" in text and "5일선·20일선·60일선 아래" in text and "추세 전환은 확인되지 않았습니다" in text
    assert "1주일 뒤 방향을 전체 평균보다 잘 맞히지 못했습니다(상승 비율 51.8% vs 전체 49.9%, 표본 22,176, 차이 +1.9%p, 95% 구간 -1.4~+5.3%p)" in text
    assert len(re.findall(r"다\.", text)) <= 3


def test_summary_says_dominant_only_when_signals_and_regime_agree(backtest):
    res = _skhynix_like()
    res["indicators"]["regime_inputs"].update(r20=8.0, r60=20.0, ma20=1_300_000.0, ma60=1_200_000.0, ma5=1_400_000.0)
    regime = outlook.classify_regime(res["indicators"], res["label"])
    assert regime == outlook.REGIME_UP
    assert outlook.signal_sentence(res, regime, W1).startswith("현재 신호는 상승 쪽이 우세합니다(5개 묶음 중 상승 2·하락 0·중립 3).")


def test_evidence_items_merge_the_same_fact(backtest):
    res = _skhynix_like()
    up, down = outlook.evidence_items(res["indicators"], res["signals"], W1)
    assert up[0] == "최근 5거래일 수익률 +2.91%(2026-10-27 종가 대비)"
    assert sum("골든크로스" in x or "5일선 1,461,800원이 20일선" in x for x in up) == 1
    assert "MACD가 시그널 위(MACD -64,873.44, 시그널 -88,478.87, 단 MACD는 0 아래)" in up
    assert down[0] == "20거래일 수익률 -23.24%(2026-10-02 종가 1,842,000원 대비)"
    assert "종가 1,414,000원이 5일선(1,461,800원)·20일선(1,421,300원)·60일선(1,604,583원) 아래" in down
    assert "기준일(2026-11-03) 하루 -4.46%, 거래량은 직전 20거래일 평균의 0.63배" in down


def test_fact_lines_use_news_disclosures_and_earnings_without_voting():
    news = [{"title": "SK하이닉스 \"HBM4 수율 초과 달성\" [컨콜]", "published_at": "2026-10-28T10:00:00"},
            {"title": "코스피, 3.59% 하락…하이닉스 5.4%↓", "published_at": "2026-11-03T15:00:00"}]
    disc = [{"title": "풍문또는보도에대한해명(미확정)", "disclosed_at": "2026-10-28"},
            {"title": "연결재무제표기준영업(잠정)실적(공정공시)", "disclosed_at": "2026-10-27"}]
    rows = [{"operating_income": 6.05426e13, "operating_income_prev": 9.2128e12, "period_label": "2026년 2분기", "fs_div": "CFS"}]
    lines = outlook.fact_lines(news, disc, rows)
    assert lines[0] == "- 신호 판정에 넣지 않은 최근 사실:"
    assert '  - 2026-10-27 "연결재무제표기준영업(잠정)실적(공정공시)" 공시가 있었습니다.' in lines
    assert any("HBM4 수율 초과 달성" in x and x.endswith("라는 기사가 있었습니다.") for x in lines)
    assert not any("3.59%" in x for x in lines)
    assert "  - 실적: 2026년 2분기(연결, 확정) 영업이익 605,426억 원, 전년 동기 대비 +557.2%." in lines


def test_high_and_low_lines_name_their_window():
    rows = _rows()
    lines = outlook.indicator_lines(outlook.compute_indicators(rows, W1), W1)
    hl = [x for x in lines if "종가 대비:" in x and ("최고" in x or "최저" in x)]
    assert len(hl) == 4
    assert all("조회 기간 " in x for x in hl)
    assert sum("분석 기간 기준 최근 20거래일" in x for x in hl) == 2 and sum("52주 기준 최근 250거래일" in x for x in hl) == 2
    assert f"조회 기간 {rows[-251][0].isoformat()}~{rows[-1][0].isoformat()}" in [x for x in hl if "52주" in x][0]


def test_index_lag_names_the_last_index_date():
    rows = _rows()
    index_rows = [(d, 2500.0 + i) for i, (d, _, _) in enumerate(rows[:-2])]  # 지수는 이틀 늦다
    ind = outlook.compute_indicators(rows, W1, index_rows)
    assert "relative" not in ind and ind["relative_missing"]["index_last"] == rows[-3][0].isoformat()
    ind["relative_missing"]["index_name"] = "코스피"
    line = outlook.market_lines(COMPANY, ind, None)[0]
    assert line == (f"- 지수 대비: 코스피 데이터가 {rows[-3][0].isoformat()}까지만 있어 기준일({rows[-1][0].isoformat()}) "
                    "기준으로 비교하지 못했습니다.")


def test_index_comparison_is_used_when_dates_match():
    rows = _rows()
    ind = outlook.compute_indicators(rows, W1, [(d, 2500.0 + i) for i, (d, _, _) in enumerate(rows)])
    assert ind["relative"]["end_date"] == rows[-1][0].isoformat() and "relative_missing" not in ind


# ---------- 짧은 후속 답 ----------

def _nonblank(text):
    return [x for x in text.splitlines() if x.strip()]


def test_verdict_followup_answers_directly_in_six_lines(backtest):
    text = outlook.followup_answer(COMPANY, W1, _skhynix_like(), "verdict", NOW)
    lines = _nonblank(text)
    assert len(lines) <= 5
    # 25차(2026-11-04): 제목 줄에 방향과 신뢰도, 국면은 "현재는 …"로 따로. 둘째 줄 첫 문장이 방향이다(국면이 방향을 덮어쓰지 않는다).
    assert lines[0] == ("SK하이닉스(000660) · 향후 5거래일 · 방향: 상승 쪽(신뢰도 낮음) · 현재는 단기 반등 시도, 추세 전환은 미확인 "
                        "(기준일 2026-11-03 종가)")
    assert lines[1] == ("상승 쪽입니다. 다만 신호가 약하고(5개 묶음 중 상승 2·하락 0·중립 3), 추세는 아직 하락 후 반등 시도(추세 전환 미확인) "
                        "단계이며, 과거 검증에서 이런 신호의 방향 예측력이 확인되지 않아 신뢰도는 낮습니다.")
    assert lines[2].startswith("- 근거: ") and "위험: 20거래일 수익률 -23.24%" in lines[2]
    assert lines[3].startswith("- 과거 검증: 과거 검증에서 이 신호 상태(상승 우위)의 1주일 뒤 상승 비율은 51.8%")
    assert "95% 구간 -1.4~+5.3%p" in lines[3] and lines[3].endswith("방향 예측력은 확인되지 않았습니다.")
    assert "규칙" not in text and "## " not in text and DISCLAIMER not in text
    assert outlook.marker_of(text) == {"name": "SK하이닉스", "ticker": "000660", "period": "1주일"}


def test_risk_followup_shows_risks_and_price_levels_only(backtest):
    text = outlook.followup_answer(COMPANY, W1, _skhynix_like(), "risk", NOW)
    lines = _nonblank(text)
    assert len(lines) <= 6 and "규칙" not in text
    assert sum(x.startswith("- 하락 위험: ") for x in lines) == 3
    levels = next(x for x in lines if x.startswith("- 확인할 가격대: "))
    assert "20일선 1,421,300원(종가가 아래)" in levels
    assert "분석 기간 최저 종가 1,190,000원(2026-10-20, 최근 20거래일 기준)" in levels
    assert "52주 최저 종가 510,000원(2025-10-24, 최근 250거래일 기준)" in levels


def test_probability_followup_states_no_verified_probability(backtest):
    text = outlook.followup_answer(COMPANY, W1, _skhynix_like(), "probability", NOW)
    lines = _nonblank(text)
    assert len(lines) <= 6 and lines[2] == "- 검증된 상승·하락 확률은 없어 가능성을 숫자로 말하지 않습니다."
    assert lines[1].startswith("상승 쪽입니다. 다만 ")  # 25차: 확률 질문에도 방향을 먼저
    assert "과거 빈도: 검증 구간(2026-01-02~2026-10-27)" in text and "51.8%" in text and "미래 확률이 아닙니다" in text
    assert assertion_hits(text) == [] and forbidden_hits(text) == []


def test_followups_without_backtest_say_so(monkeypatch):
    monkeypatch.setattr(outlook, "backtest_summary", lambda: None)
    verdict = outlook.followup_answer(COMPANY, W1, _skhynix_like(), "verdict", NOW)
    assert "과거 검증 결과가 아직 없어 예측력을 말할 수 없습니다" in verdict
    prob = outlook.followup_answer(COMPANY, W1, _skhynix_like(), "probability", NOW)
    assert "과거 검증 결과가 없습니다" in prob


def test_title_marker_carries_period_for_the_next_followup():
    assert outlook.marker_of("KB금융(105560) · 향후 60거래일 · 현재는 횡보, 방향 불분명 (기준일 2026-11-03 종가)") == \
        {"name": "KB금융", "ticker": "105560", "period": "3개월"}
    assert outlook.marker_of("향후 5거래일") is None


@pytest.mark.parametrize("q", ["그래서 오르냐 내리냐고", "오르냐 내리냐", "내일 떨어지냐?", "그럼 올라가냐"])
def test_oreunya_naerinya_is_a_followup_verdict(q):
    """b18_16(holdout에서 발견): "그래서 오르냐 내리냐고"가 후속 결론으로 인식되지 않아 LLM 경로로 갔다."""
    assert outlook.is_followup_verdict_question(q) and outlook.followup_kind(q) == "verdict"


def test_oreunya_with_a_company_is_a_forecast_question():
    assert outlook.is_forecast_question("삼성전자 다음 주 오르냐 내리냐")
    assert not outlook.is_forecast_question("삼성전자 왜 떨어지냐") and not outlook.is_followup_verdict_question("삼성전자 왜 떨어지냐")
