"""전망 간결 답·후속 답의 언어별 템플릿(app.outlook_i18n, 2026-11-06). DB 없이 합성 시세로 app.outlook의 순수 함수를 그대로 돌린다.

확인하는 것: 템플릿이 쓰인다 · 숫자는 모두 한국어 답에 있는 값이다 · 종목명 말고 한글이 남지 않는다 · 언어별 문자 · 줄 수가 한국어와 같다 ·
한국어 답이 기록과 다르거나(가드가 고침) 다루지 않는 종류면 쓰지 않는다(None → 보호 번역) · 한국어 경로는 그대로다.
"""

import math
import re
from datetime import date, datetime, timedelta, timezone

import pytest

import app.i18n_translate as tr
from app import answer_i18n, outlook as o, outlook_i18n as oi
from app.briefing import KST
from app.data_status import last_business_day

LOCALES = ("en-US", "ja-JP", "zh-CN")
KINDS = ("brief", "verdict", "pick", "why", "oneline", "compare", "probability", "buy", "risk", "data")
NAME, TICKER = "가나다전자", "123450"
HANGUL = re.compile(r"[가-힣]")
KANA = re.compile(r"[぀-ヿ]")
HAN = re.compile(r"[一-鿿]")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _days(n: int, end: date) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _series(shape: str, n: int = 260) -> list[float]:
    """모양별 종가(결정적)."""
    out = []
    for i in range(n):
        x = i / n
        wave = 0.03 * math.sin(i / 3.0)
        if shape == "up":
            v = 100 * (1 + 0.6 * x) * (1 + wave)
        elif shape == "down":
            v = 100 * (1.6 - 0.7 * x) * (1 + wave)
        elif shape == "bounce":  # 큰 하락(최근 60거래일 약 -35%) 뒤 마지막 4거래일 반등
            k = min(i, n - 5) - (n - 65)
            v = 150 * (1 - 0.35 * max(0, k) / 60) * (1.025 ** max(0, i - (n - 5))) * (1 + wave / 10)
        elif shape == "dip":  # 꾸준한 상승 뒤 마지막 5거래일 조정
            v = 100 * (1 + 2.0 * min(i, n - 6) / n) * (0.99 ** max(0, i - (n - 6))) * (1 + wave / 10)
        elif shape == "rebound":
            v = 100 * (1.5 - 0.6 * min(x, 0.93) + (2.5 * (x - 0.93) if x > 0.93 else 0)) * (1 + wave / 3)
        elif shape == "pullback":
            v = 100 * (1 + 0.9 * min(x, 0.95) - (1.5 * (x - 0.95) if x > 0.95 else 0)) * (1 + wave / 3)
        else:
            v = 100 * (1 + wave)
        out.append(round(v * 1000))
    return out


END = date(2026, 10, 30)  # 금요일


def _scenario(shape: str, period_label: str, lag: int = 0, missing: int = 0, surge: bool = False, with_index: bool = True):
    period = o.PERIODS[period_label]
    days = _days(260, END)
    closes = _series(shape)
    vols = [1_000_000 + (i % 7) * 10_000 for i in range(260)]
    if surge:
        vols[-1] = 3_000_000
    rows = list(zip(days, closes, vols))
    index_rows = [(d, 2500 + i) for i, d in enumerate(days)] if with_index else None
    ind = o.compute_indicators(rows, period, index_rows)
    if ind.get("relative"):
        ind["relative"].update(index_code="KS11", index_name="코스피")
    signals = o.classify_signals(ind, period)
    expected = END
    for _ in range(lag):
        expected = last_business_day(expected + timedelta(days=3)) if expected.weekday() == 4 else expected + timedelta(days=1)
    now = datetime.combine(expected, datetime.min.time(), tzinfo=KST).replace(hour=20)
    status = {"status": o.PRICE_STATUS_NOT_COLLECTED, "detail": "장 마감 뒤 시세 수집 기록 없음"} if lag else None
    data = o.data_check(END, now, days[-missing - 1:-1] if missing else [], status)
    data["base_is_trading_day"] = True
    res = {"indicators": ind, "signals": signals, "label": o.judge(signals, data), "data": data}
    return period, res, now


SCENARIOS = [
    ("up", "1주일", {}), ("down", "1주일", {}), ("flat", "1개월", {}), ("rebound", "1주일", {"surge": True}),
    ("pullback", "1개월", {}), ("down", "3개월", {"lag": 1}), ("up", "1주일", {"lag": 3}), ("flat", "1주일", {"lag": 7}),
    ("up", "1개월", {"missing": 2}), ("rebound", "3개월", {"with_index": False}),
    ("bounce", "1주일", {}), ("bounce", "1개월", {"lag": 2}), ("dip", "1주일", {}), ("dip", "3개월", {}),
    ("pullback", "3개월", {}),
]


class _Co:
    name, ticker, asset_type = NAME, TICKER, None


def _korean(period, res, kind, now):
    return o.brief_answer(_Co, period, res, now) if kind == "brief" else o.followup_answer(_Co, period, res, kind, now, {})


def _records(period, res, kind):
    return [{"tool_name": "stock_tool", "called_at": datetime.now(timezone.utc),
             "result": {"company_name": NAME, "ticker": TICKER, "found": True, "called_via": f"forecast_{kind}",
                        "period": period.label, "label": res["label"], "indicators": res["indicators"],
                        "signals": res["signals"], "data": res["data"]}}]


def _nums(s: str) -> set[str]:
    return {n.replace(",", "") for n in NUM.findall(s)}


def test_scenarios_cover_every_label_and_data_state():
    labels, states, regimes = set(), set(), set()
    for shape, pl, kw in SCENARIOS:
        _, res, _ = _scenario(shape, pl, **kw)
        labels.add(res["label"])
        states.add((res["data"]["lag_business_days"] > 0, bool(res["data"]["missing_days"])))
        regimes.add(o.classify_regime(res["indicators"], res["label"]))
    assert labels == {o.LABEL_UP, o.LABEL_DOWN, o.LABEL_NEUTRAL, o.LABEL_HOLD}
    assert regimes == set(o.REGIMES)
    assert {(False, False), (True, False), (False, True)} <= states


@pytest.mark.parametrize("scenario", range(len(SCENARIOS)))
@pytest.mark.parametrize("kind", KINDS)
def test_template_is_used_and_keeps_values(scenario, kind):
    shape, pl, kw = SCENARIOS[scenario]
    period, res, now = _scenario(shape, pl, **kw)
    ko = _korean(period, res, kind, now)
    for L in LOCALES:
        out = oi.localize(ko, _records(period, res, kind), L)
        assert out is not None, (L, kind)
        assert _nums(out) <= _nums(ko), (L, kind, _nums(out) - _nums(ko))
        rest = out.replace(NAME, "").replace("자세히 분석해줘", "")
        assert not HANGUL.search(rest), (L, kind, rest)
        assert "{" not in out and "}" not in out
        assert out.count("\n") == ko.count("\n"), (L, kind)
        assert f"{NAME}({TICKER})" in out  # 공식 종목명·종목코드는 원문 그대로
        if L == "en-US":
            assert not KANA.search(rest) and not HAN.search(rest)
        elif L == "ja-JP":
            assert KANA.search(rest)
        else:
            assert HAN.search(rest) and not KANA.search(rest)


def test_dates_and_prices_from_the_korean_answer_are_all_kept():
    """한국어 답의 날짜·가격(쉼표 숫자)은 템플릿 답에도 모두 있다(빠뜨리지 않는다)."""
    period, res, now = _scenario("rebound", "1주일", surge=True)
    ko = _korean(period, res, "brief", now)
    for L in LOCALES:
        out = oi.localize(ko, _records(period, res, "brief"), L)
        for token in re.findall(r"\d{4}-\d{2}-\d{2}|\d{1,3}(?:,\d{3})+|[+-]\d+\.\d+%p?", ko):
            assert token in out, (L, token)


def test_not_used_when_korean_answer_differs_from_record():
    period, res, now = _scenario("up", "1주일")
    ko = _korean(period, res, "brief", now)
    assert oi.localize(ko + "\n추가 문장", _records(period, res, "brief"), "en-US") is None
    assert oi.localize(ko.replace("상승", "하락", 1), _records(period, res, "brief"), "ja-JP") is None


@pytest.mark.parametrize("mode", ["forecast", "earnings", "filing", "signal", "ask"])
def test_other_modes_are_left_to_protected_translation(mode):
    period, res, now = _scenario("up", "1주일")
    ko = _korean(period, res, "brief", now)
    assert oi.localize(ko, _records(period, res, mode), "en-US") is None


def test_unknown_warning_disables_template():
    period, res, now = _scenario("up", "1주일")
    res["data"]["warnings"].append("모르는 경고")
    ko = _korean(period, res, "brief", now)
    assert oi.localize(ko, _records(period, res, "brief"), "zh-CN") is None


def test_answer_i18n_uses_template_without_llm(monkeypatch):
    def no_llm(*a, **k):
        raise AssertionError("LLM을 부르면 안 된다")

    monkeypatch.setattr(tr, "_chat", no_llm)
    period, res, now = _scenario("down", "1주일")
    ko = _korean(period, res, "brief", now)
    out, status = answer_i18n.localize_answer("가나다전자 일주일 전망 알려줘", ko, _records(period, res, "brief"), "ja-JP")
    assert status == tr.STATUS_LOCALIZED and out.startswith(f"{NAME}({TICKER}) · 今後5取引日")


def test_korean_outlook_is_untouched_by_localizing():
    period, res, now = _scenario("pullback", "1개월")
    before = _korean(period, res, "brief", now)
    for L in LOCALES:
        oi.localize(before, _records(period, res, "brief"), L)
    assert _korean(period, res, "brief", now) == before


def test_direction_words_per_locale():
    period, res, now = _scenario("rebound", "1주일", surge=True)
    ko = _korean(period, res, "oneline", now)
    assert res["label"] == o.LABEL_UP
    words = {"en-US": "Direction: upward bias", "ja-JP": "方向: 上昇寄り", "zh-CN": "方向：偏上涨"}
    for L, w in words.items():
        assert w in oi.localize(ko, _records(period, res, "oneline"), L)
