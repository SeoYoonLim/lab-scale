"""최근 N거래일 주가 흐름 질문(app/price_flow, 2026-11-06). DB 없이 합성 시세로 계산·문장·경로를 확인한다.

계기: "삼성전자의 최근 20거래일 주가 흐름을 분석해 줘. 1일·5일·20일 수익률, 5일·20일 이동평균 ..."이 LLM 경로로 가서
기간 해석이 "1일"을 조회 기간으로 읽고 1일 시세만 조회했다(요청한 수익률·이동평균·거래량 비교·국면이 모두 빠짐).
"""

import re
from datetime import date, datetime, timedelta

import pytest

from app import agent, outlook as o, price_flow as pf
from app.briefing import KST

Q = ("삼성전자의 최근 20거래일 주가 흐름을 분석해 줘. 1일·5일·20일 수익률, 5일·20일 이동평균, 최근 거래량과 20거래일 평균 거래량을 "
     "비교해 줘. 상승 추세, 하락 추세, 횡보, 단기 반등 시도 중 어떤 국면인지 판단하되 근거가 부족하면 추세 미확정이라고 표시해 줘. "
     "데이터 기준일과 최신 데이터 지연 여부도 먼저 알려 줘.")
END = date(2026, 10, 30)  # 금요일


def _days(n: int, end: date = END) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _rows(n: int = 260, step: int = 100, vol_last: int = 3_000_000):
    """종가 10,000원에서 거래일마다 step원씩 오르는 시세. 거래량은 1,000,000주, 기준일만 vol_last."""
    days = _days(n)
    return [(d, 10_000 + i * step, 1_000_000 if i < n - 1 else vol_last) for i, d in enumerate(days)]


def _res(rows, now):
    period = o.PERIODS[o.DEFAULT_PERIOD]
    ind = o.compute_indicators(rows, period, None)
    signals = o.classify_signals(ind, period)
    data = o.data_check(rows[-1][0], now, [], None)
    data["base_is_trading_day"] = True
    return {"indicators": ind, "signals": signals, "label": o.judge(signals, data), "data": data}


NOW = datetime(2026, 10, 30, 20, 0, tzinfo=KST)


class _Co:
    name, ticker, asset_type, market = "가나다전자", "123450", None, "KOSPI"


# ---------- 질문 판별 ----------

@pytest.mark.parametrize("q", [
    Q,
    "삼성전자 5일선이랑 20일선 비교해서 추세 알려줘",
    "SK하이닉스 20일 이동평균과 거래량 분석해줘",
    "삼성전자 1일·5일·20일 수익률 알려줘",
    "카카오 5일, 20일 수익률이랑 이평선 흐름",
])
def test_price_flow_questions(q):
    assert pf.is_price_flow_question(q)


@pytest.mark.parametrize("q", [
    "삼성전자 주가 알려줘",
    "SK하이닉스 거래량 얼마야?",
    "LG에너지솔루션 최근 20거래일 중 거래량이 제일 많았던 날은?",
    "삼성전자 최근 1개월 수익률은?",
    "이동평균이 뭐야?",  # 종목 없이 개념(경로에서 종목 하나가 아니라 빠지고, 용어 사전이 먼저다)
    "삼성전자 20일 이동평균 보면 다음 주에 오를까?",  # 전망 경로
    "삼성전자 왜 5일선 아래로 떨어졌어?",  # 원인 질문
])
def test_other_questions_are_not_taken(q):
    if q == "이동평균이 뭐야?":
        assert agent._answer_price_flow(q, []) is None
        return
    assert not pf.is_price_flow_question(q)


def test_windows_from_question():
    w = pf.windows(Q)
    assert w == {"returns": (1, 5, 20), "mas": (5, 20), "volume_avg": 20, "window": 20}
    w = pf.windows("삼성전자 최근 60거래일 흐름, 5일·20일·60일 이동평균과 1일·20일·60일 수익률, 60일 평균 거래량 비교")
    assert w == {"returns": (1, 20, 60), "mas": (5, 20, 60), "volume_avg": 60, "window": 60}
    assert pf.windows("삼성전자 5일선 20일선 흐름")["mas"] == (5, 20)
    assert pf.windows("삼성전자 이동평균 흐름") == {"returns": (1, 5, 20), "mas": (5, 20), "volume_avg": 20, "window": 20}


# ---------- 계산 ----------

def test_compute_values():
    rows = _rows()
    flow = pf.compute(rows, pf.windows(Q))
    close = rows[-1][1]  # 10,000 + 259 * 100 = 35,900
    assert flow["base_date"] == "2026-10-30" and flow["close"] == close == 35_900
    r = flow["returns"]
    assert r[1] == {"pct": round((35_900 / 35_800 - 1) * 100, 2), "from_date": rows[-2][0].isoformat(), "from_close": 35_800}
    assert r[5]["from_close"] == 35_400 and r[20]["from_close"] == 33_900
    assert flow["mas"][5]["value"] == 35_700  # 35,500~35,900 평균
    assert flow["mas"][20]["value"] == 34_950  # 33,000~35,900 평균
    v = flow["volume"]
    assert v["value"] == 3_000_000 and v["avg"] == 1_000_000 and v["ratio"] == 3.0
    assert v["from_date"] == rows[-21][0].isoformat() and v["to_date"] == rows[-2][0].isoformat()
    wd = flow["window"]
    assert wd["days"] == 20 and wd["high"] == 35_900 and wd["low"] == 33_900


def test_short_history_says_not_enough():
    rows = _rows(n=10)
    flow = pf.compute(rows, pf.windows(Q))
    assert flow["returns"][20] is None and flow["mas"][20] is None and flow["volume"]["avg"] is None
    assert flow["window"]["days"] == 9
    text = pf.answer(_Co, flow, _res(rows, NOW), NOW)
    assert "- 20거래일: 계산할 시세 부족" in text and "20일 이동평균: 계산할 시세 부족" in text
    assert "## 국면 판단: 추세 미확정" in text  # 60거래일 시세가 없다


# ---------- 문장 ----------

def _numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}


def test_answer_order_and_contents():
    rows = _rows()
    res = _res(rows, NOW)
    text = pf.answer(_Co, pf.compute(rows, pf.windows(Q)), res, NOW)
    lines = text.splitlines()
    assert lines[0] == "가나다전자(123450) · 최근 20거래일 주가 흐름"
    assert lines[2].startswith("- 데이터 기준일: 2026-10-30 종가(최신 시세, 기대 거래일 2026-10-30)")
    assert lines[3] == "- 최신 데이터 지연: 없음"
    heads = [x for x in lines if x.startswith("## ")]
    assert heads == ["## 수익률(기준일 종가 35,900원)", "## 이동평균", "## 거래량", "## 최근 20거래일 범위", "## 국면 판단: 상승 추세"]
    assert "- 5일 이동평균: 35,700원(종가가 0.56% 위)" in lines
    assert "- 5일선이 20일선보다 위에 있습니다." in lines
    assert "- 기준일 거래량은 평균의 3.00배입니다." in lines
    assert f"- 코드 규칙 판정: {o.REGIME_UP}" in lines
    assert lines[-1] == f"- {pf.NOT_A_FORECAST}"


def test_lag_and_missing_warnings_come_first_and_hold_regime():
    rows = _rows()
    later = datetime(2026, 11, 12, 20, 0, tzinfo=KST)  # 기준일보다 9영업일 뒤
    res = _res(rows, later)
    text = pf.answer(_Co, pf.compute(rows, pf.windows(Q)), res, later)
    lines = text.splitlines()
    assert lines[3].startswith("- 데이터 경고: 마지막 시세(2026-10-30)") and "판단을 유보합니다" in lines[3]
    assert "최신 데이터 지연: 없음" not in text
    assert "## 국면 판단: 추세 미확정" in text and "데이터 경고 때문에 판단하지 않습니다" in text


def test_regime_words_cover_every_regime():
    assert set(pf._REGIME_WORD) == set(o.REGIMES)
    assert set(pf._REGIME_WHY) == set(o.REGIMES) - {o.REGIME_HOLD}


# ---------- 에이전트 경로 ----------

class _FakeDb:
    def close(self):
        pass


def test_agent_routes_to_code_answer_without_llm(monkeypatch):
    rows = _rows()
    monkeypatch.setattr(agent, "SessionLocal", _FakeDb)
    monkeypatch.setattr(agent, "_question_companies", lambda q: [_Co])
    monkeypatch.setattr(o, "price_rows", lambda db, c, now: rows)
    monkeypatch.setattr(o, "analyze", lambda db, c, period, now, rows=None: _res(rows, NOW))
    monkeypatch.setattr(agent, "_first_call", lambda *a, **k: pytest.fail("LLM을 부르면 안 된다"))
    monkeypatch.setattr(agent, "_answer_forecast", lambda q, p: None)
    monkeypatch.setattr(agent, "_event_subject", lambda q, p: None)
    monkeypatch.setattr(agent, "_answer_briefing", lambda q, u: None)
    response, records = agent._answer(Q.replace("삼성전자", _Co.name), "m")
    assert response["used_tools"] == ["stock_tool"]
    assert records[0]["result"]["called_via"] == "price_flow"
    assert records[0]["arguments"] == {"ticker": _Co.name, "period_days": 20}
    text = response["answer"]
    assert "## 이동평균" in text and "## 거래량" in text and "## 국면 판단:" in text
    # 답의 숫자는 모두 기록(시세 계산 결과)이나 질문에서 왔다
    record_text = repr(records[0]["result"])
    for n in _numbers(text) - _numbers(Q):
        assert n in record_text.replace(",", "") or n.rstrip("0").rstrip(".") in record_text or len(n) <= 2, n


def test_two_companies_go_to_the_old_path():
    assert agent._answer_price_flow(Q, [_Co, _Co]) is None


# ---------- 언어별 템플릿(app/price_flow_i18n) ----------

import math  # noqa: E402

import app.i18n_translate as tr  # noqa: E402
from app import answer_i18n, price_flow_i18n as pfi  # noqa: E402

LOCALES = ("en-US", "ja-JP", "zh-CN")
HANGUL = re.compile(r"[가-힣]")
KANA = re.compile(r"[぀-ヿ]")
HAN = re.compile(r"[一-鿿]")


def _shape_rows(shape: str, n: int = 260):
    """test_i18n_outlook의 합성 모양과 같은 종류(결정적). 거래량은 기준일만 3배."""
    out = []
    for i, d in enumerate(_days(n)):
        x = i / n
        wave = 0.03 * math.sin(i / 3.0)
        if shape == "up":
            v = 100 * (1 + 0.6 * x) * (1 + wave)
        elif shape == "down":
            v = 100 * (1.6 - 0.7 * x) * (1 + wave)
        elif shape == "bounce":
            k = min(i, n - 5) - (n - 65)
            v = 150 * (1 - 0.35 * max(0, k) / 60) * (1.025 ** max(0, i - (n - 5))) * (1 + wave / 10)
        elif shape == "dip":
            v = 100 * (1 + 2.0 * min(i, n - 6) / n) * (0.99 ** max(0, i - (n - 6))) * (1 + wave / 10)
        elif shape == "pullback":
            v = 100 * (1 + 0.9 * min(x, 0.95) - (1.5 * (x - 0.95) if x > 0.95 else 0)) * (1 + wave / 3)
        else:
            v = 100 * (1 + wave)
        out.append((d, round(v * 1000), 1_000_000 if i < n - 1 else 3_000_000))
    return out


# (모양, 행 수, 지금) — 모든 국면 + 판단 유보 3가지(데이터 지연, 60거래일 부족, 지표 묶음 부족은 판정 라벨로) + 지연 경고
LATE = datetime(2026, 11, 4, 20, 0, tzinfo=KST)  # 기준일보다 3영업일 뒤(경고만)
VERY_LATE = datetime(2026, 11, 12, 20, 0, tzinfo=KST)
CASES = [("linear", 260, NOW), ("up", 260, NOW), ("down", 260, NOW), ("flat", 260, NOW), ("bounce", 260, NOW), ("dip", 260, NOW),
         ("pullback", 260, NOW), ("up", 260, LATE), ("down", 260, VERY_LATE), ("up", 40, NOW), ("up", 10, NOW)]


def _case(i):
    shape, n, now = CASES[i]
    rows = _rows(n) if shape == "linear" else _shape_rows(shape, n)
    res = _res(rows, now)
    flow = pf.compute(rows, pf.windows(Q))
    return flow, res, now


def _records(flow, res):
    return [{"tool_name": "stock_tool", "called_at": NOW,
             "result": {"company_name": _Co.name, "ticker": _Co.ticker, "found": True, "called_via": "price_flow",
                        "flow": flow, "label": res["label"], "indicators": res["indicators"], "signals": res["signals"],
                        "data": res["data"]}}]


def test_cases_cover_every_regime_and_hold_reason():
    regimes, holds = set(), set()
    for i in range(len(CASES)):
        _, res, _ = _case(i)
        r = o.classify_regime(res["indicators"], res["label"])
        regimes.add(r)
        if r == o.REGIME_HOLD:
            holds.add(pf.hold_reason(res))
    assert regimes == set(o.REGIMES)
    assert {"data", "rows"} <= holds


def test_signals_hold_reason():
    flow, res, now = _case(0)
    res = {**res, "label": o.LABEL_HOLD}
    assert pf.hold_reason(res) == "signals"
    assert "판단에 쓸 지표 묶음이 부족해" in pf.answer(_Co, flow, res, now)
    for L in LOCALES:
        assert pfi.localize(pf.answer(_Co, flow, res, now), _records(flow, res), L) is not None


@pytest.mark.parametrize("i", range(len(CASES)))
def test_template_keeps_values_and_script(i):
    flow, res, now = _case(i)
    ko = pf.answer(_Co, flow, res, now)
    for L in LOCALES:
        out = pfi.localize(ko, _records(flow, res), L)
        assert out is not None, (CASES[i], L)
        assert _numbers(out) <= _numbers(ko), (L, _numbers(out) - _numbers(ko))
        assert out.count("\n") == ko.count("\n")
        assert f"{_Co.name}({_Co.ticker})" in out and "{" not in out and "}" not in out
        rest = out.replace(_Co.name, "")
        assert not HANGUL.search(rest), (L, HANGUL.findall(rest))
        if L == "en-US":
            assert not KANA.search(rest) and not HAN.search(rest)
        elif L == "ja-JP":
            assert KANA.search(rest)
        else:
            assert HAN.search(rest) and not KANA.search(rest.replace("ウォン", ""))
        # 날짜·가격(쉼표 숫자)·부호 붙은 비율은 빠짐없이 그대로
        for token in re.findall(r"\d{4}-\d{2}-\d{2}|\d{1,3}(?:,\d{3})+(?:\.\d+)?|[+-]\d+\.\d+%", ko):
            assert token in out, (L, token)


def test_template_not_used_when_answer_differs_or_unknown_warning():
    flow, res, now = _case(0)
    ko = pf.answer(_Co, flow, res, now)
    assert pfi.localize(ko + "\n추가", _records(flow, res), "en-US") is None
    assert pfi.localize(ko.replace("상승", "하락", 1), _records(flow, res), "ja-JP") is None
    res2 = {**res, "data": {**res["data"], "warnings": res["data"]["warnings"] + ["모르는 경고"]}}
    assert pfi.localize(pf.answer(_Co, flow, res2, now), _records(flow, res2), "zh-CN") is None
    other = _records(flow, res)
    other[0]["result"]["called_via"] = "forecast_brief"
    assert pfi.localize(ko, other, "en-US") is None
    assert pfi.localize(ko, _records(flow, res), "ko-KR") is None


def test_answer_i18n_uses_price_flow_template_without_llm(monkeypatch):
    monkeypatch.setattr(tr, "_chat", lambda *a, **k: pytest.fail("LLM을 부르면 안 된다"))
    flow, res, now = _case(1)
    ko = pf.answer(_Co, flow, res, now)
    out, status = answer_i18n.localize_answer(Q, ko, _records(flow, res), "ja-JP")
    assert status == tr.STATUS_LOCALIZED and out.startswith(f"{_Co.name}({_Co.ticker}) · 直近20取引日の株価推移")


def test_korean_answer_unchanged_by_localizing():
    flow, res, now = _case(3)
    before = pf.answer(_Co, flow, res, now)
    for L in LOCALES:
        pfi.localize(before, _records(flow, res), L)
    assert pf.answer(_Co, flow, res, now) == before
