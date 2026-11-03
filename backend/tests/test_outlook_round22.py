"""2026-11-03 2차: 신호 백테스트(누출 방지·독립 검산), 주문 문장 경로, 간접 뉴스, 실적 파싱. DB·LLM 없음."""

import itertools
import random
from datetime import date, timedelta
from types import SimpleNamespace as NS

import numpy as np
import pytest

from app import agent, earnings, outlook, related_news
from scripts import backtest_outlook as BT


def _rows(n, seed=3):
    rnd = random.Random(seed)
    d, price, out = date(2024, 1, 2), 10000.0, []
    while len(out) < n:
        if d.weekday() < 5:
            price = max(100.0, round(price * (1 + rnd.uniform(-0.03, 0.03))))
            out.append((d, price, rnd.randint(1000, 5000)))
        d += timedelta(days=1)
    return out


# ---------- 백테스트 ----------

def test_signals_do_not_see_future_prices():
    """t일 신호는 t일까지의 시세만 본다: t 이후 가격을 바꿔도 t일 이전 신호는 그대로다."""
    rows = _rows(300)
    period = outlook.PERIODS["1주일"]
    base = BT.signals_for_stock(rows, None, period, 5)
    cut = 280
    changed = rows[:cut + 1] + [(d, c * 3, v) for d, c, v in rows[cut + 1:]]
    other = BT.signals_for_stock(changed, None, period, 5)
    first = outlook.PRICE_ROWS - 1
    for t in range(first, cut + 1):
        assert base[t - first][1] == other[t - first][1], t


def test_forward_return_is_independent_of_signal_window():
    rows = _rows(300)
    out = BT.signals_for_stock(rows, None, outlook.PERIODS["1주일"], 5)
    first = outlook.PRICE_ROWS - 1
    d, _, fwd = out[0]
    assert d == rows[first][0] and fwd == pytest.approx(rows[first + 5][1] / rows[first][1] - 1)
    assert np.isnan(out[-1][2])  # 마지막 날은 5거래일 뒤가 없다


def test_signal_window_matches_live_answer():
    """백테스트의 t일 신호 = 실제 답변이 t일 기준으로 계산하는 신호(같은 260거래일 창)."""
    rows = _rows(300)
    period = outlook.PERIODS["1개월"]
    out = BT.signals_for_stock(rows, None, period, 20)
    t = 270
    live = outlook.classify_signals(outlook.compute_indicators(rows[t - 259:t + 1], period), period)
    want = [{s["kind"]: BT.SIDE_NUM[s["side"]] for s in live}.get(k, BT.MISSING) for k in BT.KINDS]
    assert out[t - (outlook.PRICE_ROWS - 1)][1] == want


def test_vectorized_labels_match_outlook_judge():
    """numpy 판정(labels_from_sides)이 app.outlook.judge와 모든 신호 조합에서 같다(지표 몇 개는 빠진 경우 포함)."""
    rnd = random.Random(1)
    sides = []
    for _ in range(3000):
        sides.append([rnd.choice([1, -1, 0, BT.MISSING]) for _ in BT.KINDS])
    arr = np.array(sides, dtype=np.int8)
    got = BT.labels_from_sides(arr, outlook.SIGNAL_GROUPS, outlook.GROUP_MARGIN, outlook.MIN_GROUPS)
    names = [outlook.LABEL_UP, outlook.LABEL_DOWN, outlook.LABEL_NEUTRAL, outlook.LABEL_HOLD]
    num = {1: "up", -1: "down", 0: "neutral"}
    for row, lab in zip(sides, got):
        sig = [{"kind": k, "group": outlook.SIGNAL_GROUPS[k], "side": num[v]} for k, v in zip(BT.KINDS, row) if v != BT.MISSING]
        assert outlook.judge(sig, {"hold": False}) == names[lab]


def test_compare_matches_hand_calculation():
    up = np.array([1, 0, 1, 1, 0, 1, 0, 0], dtype=float)
    in_label = np.array([1, 1, 1, 0, 0, 0, 0, 1], dtype=bool)
    clusters = np.array([0, 0, 1, 1, 2, 2, 3, 3])
    r = BT.compare(up, in_label, clusters)
    assert r["n"] == 4 and r["up_rate"] == 0.5 and r["base_rate"] == 0.5 and r["diff"] == 0.0
    # 군집 강건 분산: D_g = a_g/n_l - b_g/n_a, var = G/(G-1) * sum(D_g^2)
    a = [(1 - .5) + (0 - .5), (1 - .5), 0, (0 - .5)]
    b = [(1 - .5) + (0 - .5), (1 - .5) + (1 - .5), (0 - .5) + (1 - .5), (0 - .5) + (0 - .5)]
    d = [x / 4 - y / 8 for x, y in zip(a, b)]
    se = (4 / 3 * sum(v * v for v in d)) ** 0.5
    assert r["diff_ci95"] == [round(-1.96 * se, 4), round(1.96 * se, 4)]


def test_split_keeps_training_returns_before_the_boundary():
    cal = np.array([date(2025, 12, 1).toordinal() + i for i in range(60)], dtype=np.int32)
    dates = cal[:50]
    train = BT.split_mask(dates, cal, 5, "train")
    for d, ok in zip(dates, train):
        end = cal[min(int(np.searchsorted(cal, d)) + 5, cal.size - 1)]
        assert ok == (d <= BT.TRAIN_END.toordinal() and end <= BT.TRAIN_END.toordinal())


def test_backtest_line_reads_the_summary(monkeypatch):
    summary = {"periods": {"1주일": {"valid": {"first_date": "2026-01-02", "last_date": "2026-10-27", "horizon": 5, "labels": {
        "상승 우위": {"n": 1234, "up_rate": 0.55, "base_rate": 0.5, "diff": 0.05, "diff_ci95": [0.01, 0.09], "significant": True},
        "중립": {"n": 99, "up_rate": 0.5, "base_rate": 0.5, "diff": 0.0, "diff_ci95": [-0.02, 0.02], "significant": False}}}}}}
    monkeypatch.setattr(outlook, "backtest_summary", lambda: summary)
    p = outlook.PERIODS["1주일"]
    line = outlook.backtest_line(p, "상승 우위")
    assert "상승 비율 55.0%(같은 기간 전체 50.0%, 표본 1,234, 차이 +5.0%p, 95% 구간 +1.0~+9.0%p)" in line
    assert "확률이나 예측이 아닙니다" in line and "뚜렷한 차이가 없었습니다" not in line
    assert "뚜렷한 차이가 없었습니다" in outlook.backtest_line(p, "중립")
    assert "검증 대상이 아닙니다" in outlook.backtest_line(p, "판단 유보")


# ---------- 주문 문장 ----------

@pytest.mark.parametrize("q", ["삼성전자 10주 사줘", "하이닉스 팔아줘", "카카오 20주 매수해줘", "NAVER 3주 시장가로 사줘",
                               "현대차 보유분 전부 매도해줘", "SK하이닉스 지금 바로 매수 주문 넣어줘"])
def test_order_commands(q):
    assert outlook.is_order_command(q)


@pytest.mark.parametrize("q", ["삼성전자 사도 될까?", "삼성전자 10주 사면 얼마야?", "주문 방법 알려줘", "모의투자 주문은 어떻게 해?",
                               "체결강도가 뭐야", "삼성전자 주가 알려줘", "에코프로 지금 사도 될까?"])
def test_not_order_commands(q):
    assert not outlook.is_order_command(q)


def test_order_path_answers_without_llm_and_never_executes(monkeypatch):
    monkeypatch.setattr(agent, "_llm_chat", lambda **kw: (_ for _ in ()).throw(AssertionError("LLM을 부르면 안 된다")))
    monkeypatch.setattr(agent, "_question_companies", lambda q: [NS(name="삼성전자", ticker="005930")])
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: {"found": True, "company_name": "삼성전자", "prices": [
        {"price_date": "2026-11-03", "close_price": 222000.0, "volume": 1, "change_pct": -3.48}]})
    monkeypatch.setattr(agent.code_answers, "latest_of", lambda result, now: result)
    monkeypatch.setattr(agent, "quote_answer", lambda result: "삼성전자의 2026-11-03 종가는 222,000원입니다.")
    resp, records = agent._answer("삼성전자 10주 사줘", "m")
    assert resp["answer"].startswith(outlook.ORDER_GUIDE_ANSWER) and "222,000원" in resp["answer"]
    assert resp["used_tools"] == ["stock_tool"]
    for bad in ("주문이 완료", "주문이 체결", "매수했습니다", "체결되었습니다"):
        assert bad not in resp["answer"]


def test_forecast_and_quote_questions_do_not_take_the_order_path(monkeypatch):
    seen = []
    monkeypatch.setattr(agent, "_answer_order_command", lambda q: seen.append(q))
    assert agent._answer_order_command("x") is None  # 패치 확인
    assert not outlook.is_order_command("삼성전자 사도 될까?") and outlook.is_forecast_question("삼성전자 사도 될까?")
    assert not outlook.is_order_command("삼성전자 주가 알려줘") and not outlook.is_forecast_question("삼성전자 주가 알려줘")


# ---------- 간접 뉴스 ----------

def test_index_rules_for_index_etfs():
    etf = lambda n: NS(name=n, asset_type="etf")
    assert related_news.index_rule(etf("TIGER 미국나스닥100"))[0] == "나스닥100"
    assert related_news.index_rule(etf("KODEX 미국S&P500"))[0] == "S&P500"
    assert related_news.index_rule(etf("KODEX 200"))[0] == "코스피200"
    assert related_news.index_rule(etf("KODEX 레버리지"))[0] == "코스피200"
    assert related_news.index_rule(etf("KODEX 200TR")) is None or related_news.index_rule(etf("KODEX 200TR"))[0] != "코스피200"
    assert related_news.index_rule(NS(name="삼성전자", asset_type="stock")) is None


def test_indirect_section_is_separate_and_not_used_for_signals():
    from tests.test_outlook import COMPANY, NOW, _result

    res = _result()
    text = outlook.forecast_answer(COMPANY, outlook.PERIODS["1주일"], res, [], [], None, NOW,
                                   indirect=(related_news.SECTOR_LABEL, "반도체", [{"title": "반도체 장비 수주", "source": "s",
                                                                                    "published_at": "2026-11-03T00:00:00"}]))
    direct = text.split("## 뉴스·공시(직접 관련)")[1].split("## ")[0]
    indirect = text.split(f"## {related_news.SECTOR_LABEL}")[1].split("## ")[0]
    assert "반도체 장비 수주" in indirect and "반도체 장비 수주" not in direct
    assert "관련 뉴스를 찾지 못했습니다" in direct  # 직접 관련이 0건이어도 간접 뉴스로 채우지 않는다
    assert "신호 판정에는 쓰지 않습니다" in indirect
    assert outlook.judge(res["signals"], res["data"]) == res["label"]


# ---------- 실적 ----------

def test_parse_accounts_prefers_consolidated_and_reads_amounts():
    items = [
        {"fs_div": "OFS", "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "10", "frmtrm_amount": "8", "rcept_no": "20251114000001"},
        {"fs_div": "CFS", "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "1,000", "frmtrm_amount": "800", "rcept_no": "20251114000001"},
        {"fs_div": "CFS", "sj_div": "IS", "account_nm": "영업이익(손실)", "thstrm_amount": "-50", "frmtrm_amount": "20", "rcept_no": "20251114000001"},
        {"fs_div": "CFS", "sj_div": "IS", "account_nm": "당기순이익(손실)", "thstrm_amount": "-", "frmtrm_amount": "5", "rcept_no": "20251114000001"},
    ]
    r = earnings.parse_accounts(items)
    assert r["fs_div"] == "CFS" and r["revenue"] == 1000 and r["revenue_prev"] == 800
    assert r["operating_income"] == -50 and r["net_income"] is None and r["net_income_prev"] == 5


def test_earnings_lines_yoy_and_loss_handling():
    rows = [{"revenue": 1.2e12, "revenue_prev": 1.0e12, "operating_income": -3e9, "operating_income_prev": 5e9, "net_income": 2e9,
             "net_income_prev": -1e9, "period_label": "2026년 2분기", "fs_div": "CFS", "rcept_no": "20260814000001",
             "disclosed_date": "2026-08-14"}]
    line = outlook.earnings_lines(rows)[1]
    assert line.startswith("- 2026년 2분기(연결): 매출액 12,000억 원, 전년 동기 대비 +20.0%")
    assert "영업이익 -30억 원;" in line  # 적자는 증감률을 쓰지 않는다
    assert "당기순이익 20억 원 (" in line  # 전년 적자 대비 증감률도 쓰지 않는다
    assert outlook.earnings_lines(None) == [] and "실적 데이터 없음" in outlook.earnings_lines([])[0]
