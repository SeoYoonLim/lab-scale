"""25차(2026-11-04): 단기 방향과 국면 분리, 첫 문장 방향, 신뢰도, 짧은 후속 답(왜·한 줄·고르기), 시세 신선도 문구."""

from datetime import date

import pytest

from app import outlook
from evals.forecast_check import assertion_hits, check_direction25
from tests.test_outlook_round23 import COMPANY, NOW, W1, _skhynix_like, backtest  # noqa: F401  (backtest는 픽스처)


def _lines(text):
    return [x for x in text.splitlines() if x.strip()]


def _with(label_sides: dict, regime_inputs: dict | None = None):
    """_skhynix_like를 바탕으로 신호 방향과 국면 입력만 바꾼 결과."""
    res = _skhynix_like()
    for s in res["signals"]:
        if s["kind"] in label_sides:
            s["side"] = label_sides[s["kind"]]
    if regime_inputs:
        res["indicators"]["regime_inputs"] = dict(res["indicators"]["regime_inputs"], **regime_inputs)
    res["label"] = outlook.judge(res["signals"], res["data"])
    return res


# ---------- 방향 = 묶음 판정(국면과 무관) ----------

def test_direction_is_the_group_label_not_the_regime():
    assert [outlook.direction_of(x) for x in ("상승 우위", "하락 우위", "중립", "판단 유보")] == ["상승 쪽", "하락 쪽", "중립", "판단 유보"]
    res = _skhynix_like()  # 상승 2·하락 0·중립 3, 국면 반등 시도
    regime = outlook.classify_regime(res["indicators"], res["label"])
    assert res["label"] == "상승 우위" and regime == outlook.REGIME_REBOUND
    assert outlook._direction(res, W1)[0] == "상승 쪽"  # 24차에서는 국면과 달라 중립(굳이 고르면 상승 쪽)이었다


def test_confidence_rule(backtest):
    t_weak = {"up": 2, "down": 0, "neutral": 3, "total": 5}
    assert outlook.confidence_of(W1, "상승 우위", t_weak, 0) == "낮음"  # 과거 검증 유의하지 않음
    assert outlook.confidence_of(W1, "중립", t_weak, 0) is None
    sig = {"periods": {"1주일": {"valid": {"labels": {"상승 우위": {"n": 100, "significant": True}}}}}}
    outlook.backtest_summary.cache_clear() if hasattr(outlook.backtest_summary, "cache_clear") else None
    import app.outlook as o
    orig = o.backtest_summary
    try:
        o.backtest_summary = lambda: sig
        assert o.confidence_of(W1, "상승 우위", t_weak, 0) == "보통"  # 유의하지만 약한 우위
        assert o.confidence_of(W1, "상승 우위", {"up": 3, "down": 0, "neutral": 2, "total": 5}, 0) == "높음"
        assert o.confidence_of(W1, "상승 우위", {"up": 3, "down": 0, "neutral": 2, "total": 5}, 2) == "보통"  # 2영업일 지연
    finally:
        o.backtest_summary = orig


@pytest.mark.parametrize("sides,ri,want_dir,want_regime", [
    # 단기 상승 우위 + 하락 추세 중 반등 시도 -> 상승 쪽, 국면은 반등 시도
    ({}, None, "상승 쪽", outlook.REGIME_REBOUND),
    # 단기 하락 우위 + 상승 추세 중 조정 -> 하락 쪽, 국면은 조정
    ({"period_return": "down", "macd": "down", "relative": "down", "close_vs_ma_short": "down"},
     {"r5": -4.0, "r20": 6.0, "r60": 15.0, "ma5": 110.0, "ma20": 108.0, "ma60": 100.0, "close": 105.0, "low20_pct": 2.0},
     "하락 쪽", None),
    # 데이터 부족 -> 판단 유보
])
def test_direction_and_regime_are_kept_apart(backtest, sides, ri, want_dir, want_regime):
    res = _with(sides, ri)
    regime = outlook.classify_regime(res["indicators"], res["label"])
    assert outlook._direction(res, W1)[0] == want_dir
    if want_regime:
        assert regime == want_regime
    text = outlook.followup_answer(COMPANY, W1, res, "verdict", NOW)
    assert _lines(text)[1].startswith(f"{want_dir}입니다.")
    assert f"현재는 {regime} (기준일" in _lines(text)[0]


def test_hold_only_when_data_is_short(backtest):
    res = _skhynix_like()
    res["data"] = {"hold": True, "warnings": ["…"], "lag_business_days": 8, "missing_days": 0}
    res["label"] = outlook.judge(res["signals"], res["data"])
    lines = _lines(outlook.followup_answer(COMPANY, W1, res, "verdict", NOW))
    assert "방향: 판단 유보" in lines[0]
    assert lines[1].startswith("데이터가 부족해 현재는 방향 판단을 보류하겠습니다(마지막 시세가 8영업일 지연, 5개 묶음 중")
    pick = _lines(outlook.followup_answer(COMPANY, W1, res, "pick", NOW))
    assert pick[1].startswith("하나만 고르라고 해도 데이터가 부족해")  # 억지로 고르지 않는다
    one_day = _skhynix_like()
    one_day["data"] = {"hold": False, "warnings": [], "lag_business_days": 1, "missing_days": 0, "freshness": "1영업일 이전 시세(미수집)"}
    assert "방향: 상승 쪽" in _lines(outlook.followup_answer(COMPANY, W1, one_day, "verdict", NOW))[0]  # 1영업일 이전만으로는 유보하지 않는다


def test_neutral_says_neutral_without_picking_a_side(backtest):
    res = _with({"macd": "down"})  # 상승 1(단기 흐름) : 하락 1(MACD)
    assert res["label"] == "중립"
    for kind in ("verdict", "pick"):
        text = outlook.followup_answer(COMPANY, W1, res, kind, NOW)
        assert "어느 쪽도 뚜렷하게 우세하지 않습니다" in _lines(text)[1] and "굳이 고르면" not in text
        assert "방향: 중립" in _lines(text)[0]


# ---------- 후속 답 종류별 ----------

def test_followup_lengths_and_first_sentence(backtest, monkeypatch):
    from evals import forecast_check as FC
    monkeypatch.setattr(FC, "regime_expected", lambda db, name, base, hold=False: outlook.REGIME_REBOUND)
    monkeypatch.setattr(FC, "_backtest_row24", lambda period, label: {"n": 22176, "diff_ci95": [-0.014, 0.053], "significant": False})
    res = _skhynix_like()
    for kind, limit in (("pick", 2), ("verdict", 4), ("why", 5), ("buy", 5), ("compare", 5), ("probability", 6), ("risk", 6), ("oneline", 1)):
        text = outlook.followup_answer(COMPANY, W1, res, kind, NOW)
        lines = _lines(text)
        assert len(lines) <= limit, (kind, lines)
        assert "현재 판단" not in text and "굳이 고르면" not in text and assertion_hits(text) == []
        assert check_direction25(None, text, "SK하이닉스", "1주일", kind) == [], kind
    assert _lines(outlook.followup_answer(COMPANY, W1, res, "pick", NOW))[1].startswith("하나만 고르면 상승 쪽입니다.")


def test_why_answer_names_the_groups(backtest):
    lines = _lines(outlook.followup_answer(COMPANY, W1, _skhynix_like(), "why", NOW))
    assert lines[1] == ("상승 쪽입니다. 그렇게 본 이유는 상승 쪽 묶음이 2개(단기 흐름, MACD)이고 하락 쪽 묶음은 없으며 중립이 3개로, "
                        "상승·하락 묶음 차이 2개가 판정 기준(2개)에 이르기 때문입니다(5개 묶음 중 상승 2·하락 0·중립 3).")
    assert lines[2].startswith("- 상승 근거: [단기 흐름] 최근 5거래일 수익률 +2.91%")  # 26차: 묶음 표시
    assert lines[3].startswith("- 하락 위험: 하락 쪽으로 판정된 묶음은 없습니다.") and "아래" in lines[3]
    assert lines[4].startswith("- 추세(따로 봄): 현재는 단기 반등 시도")


def test_oneline_is_one_line_and_keeps_the_marker(backtest):
    text = outlook.followup_answer(COMPANY, W1, _skhynix_like(), "oneline", NOW)
    assert "\n" not in text
    assert text.startswith("SK하이닉스(000660) · 향후 5거래일 · 방향: 상승 쪽(신뢰도 낮음) · 상승 쪽입니다(5개 묶음 중 상승 2·하락 0·중립 3, "
                           "약한 신호, 신뢰도 낮음).")
    assert outlook.marker_of(text) == {"name": "SK하이닉스", "ticker": "000660", "period": "1주일"}


@pytest.mark.parametrize("q,kind,special", [
    ("상승할까 하락할까?", "verdict", None), ("지금 사면 일주일 뒤 오를까?", "buy", None),
    ("현재 방향은 중립이야 상승이야?", "verdict", None), ("상승 추세인데 다음 주 조정받을까?", "verdict", None),
    ("하락 추세인데 단기 반등할 가능성은?", "probability", None), ("둘 중 하나만 선택해 봐.", "pick", None),
    ("왜?", "verdict", "why"), ("왜 상승 쪽이라고 판단했어?", "verdict", "why"), ("한 줄로만 답해.", "verdict", "oneline"),
    ("왜 떨어졌어?", "verdict", None),
])
def test_question_kinds(q, kind, special):
    assert outlook.followup_kind(q) == kind and outlook.special_followup_kind(q) == special


# ---------- 시세 신선도 ----------

def test_freshness_wording():
    d1 = outlook.data_check(date(2026, 11, 3), NOW, [])
    assert d1["warnings"][0].startswith("마지막 시세(2026-11-03)가 기대 거래일(2026-11-04)보다 1영업일 이전 데이터입니다.")
    assert d1["freshness"] == "1영업일 이전 시세" and not d1["hold"]
    d3 = outlook.data_check(date(2026, 10, 30), NOW, [])
    assert "3영업일 지연된 데이터입니다" in d3["warnings"][0] and d3["freshness"] == "3영업일 지연" and not d3["hold"]
    d8 = outlook.data_check(date(2026, 10, 23), NOW, [])
    assert "8영업일 지연(5영업일 초과)되어 데이터가 부족합니다. 판단을 유보합니다." in d8["warnings"][0] and d8["hold"]
    dm = outlook.data_check(date(2026, 11, 4), NOW, [date(2026, 10, 15)])
    assert dm["freshness"] == "가격 데이터 부족" and dm["warnings"][0].startswith("가격 데이터 부족: ") and dm["hold"]
    assert outlook.data_check(date(2026, 11, 4), NOW, [])["freshness"] == "최신 시세"
    assert "늦습니다" not in " ".join(d1["warnings"] + d3["warnings"] + d8["warnings"])
