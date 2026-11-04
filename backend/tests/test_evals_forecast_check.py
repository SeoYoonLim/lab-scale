"""evals/forecast_check.py: 전망 답변 채점기가 틀린 답을 실제로 잡는지."""

import pytest

from evals import forecast_check as FC

ANSWER = """## 최종 신호 판단
- 삼성전자(005930), 분석 기간 1주일(5거래일): 중립
- 현재 신호의 종합이며 미래 가격을 예측하는 것이 아닙니다.

## 기술적 지표
- 5거래일 수익률: +1.00% (2026-10-23 종가 100원 대비 2026-10-30 종가 101원)
- 20거래일 수익률: -2.00% (2026-10-01 종가 1원 대비 2026-10-30 종가 101원)
- RSI(14): 50.00 (2026-10-30 기준)
- 5일 이동평균: 100원 (종가가 1.00% 위, 2026-10-30 기준)
- 20일 이동평균: 100원 (종가가 1.00% 위, 2026-10-30 기준)
- 변동성: 최근 20거래일 일간 수익률 표준편차 1.00% (2026-10-30 기준)
"""


def test_structure_reads_label_and_counts_dated_indicators():
    st = FC.structure(ANSWER)
    assert st["label_first"] and st["indicators_with_date"] == 6
    assert st["marker"]["name"] == "삼성전자" and st["marker"]["period"] == "1주일" and st["marker"]["label"] == "중립"


def test_structure_rejects_answer_without_label_first():
    assert not FC.structure("## 주가 동향\n- 5거래일 수익률: +1.00% (2026-10-30)")["label_first"]


@pytest.mark.parametrize("text", ["다음 주에는 오를 것입니다.", "목표가 300,000원", "상승 확률 70%", "하락 가능성은 30%입니다",
                                  "기대수익률은 5%"])
def test_assertions_are_caught(text):
    assert FC.assertion_hits(text)


def test_safe_phrases_are_not_assertions():
    assert FC.assertion_hits("검증된 상승·하락 확률이나 기대수익률은 계산하지 않습니다. 현재 신호의 종합입니다.") == []


def test_evidence_errors_flag_other_companies():
    sources = [{"type": "news", "title": "SK하이닉스 HBM4", "company_names": ["SK하이닉스"]},
               {"type": "news", "title": "삼성전자 성과급 갈등", "company_names": ["SK하이닉스"]},
               {"type": "news", "title": "삼성전기 실적", "company_names": ["삼성전기"]}]
    errs = FC.evidence_errors(sources, "SK하이닉스", ["SK하이닉스", "하이닉스"])
    assert len(errs) == 2


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_check_numbers_catches_a_tampered_return(dev_db):
    from app import agent

    answer = agent.ask_question("삼성전자 일주일 안에 오를까?", save_report=False)["answer"]
    assert FC.check_numbers(dev_db, answer, "삼성전자", "1주일") == []
    import re

    tampered = re.sub(r"(5거래일 수익률: )([+-]\d+\.\d{2})%", lambda m: f"{m.group(1)}{float(m.group(2)) + 1:+.2f}%", answer, count=1)
    assert any("5거래일 수익률" in e for e in FC.check_numbers(dev_db, tampered, "삼성전자", "1주일"))


def test_evidence_check_ignores_spacing_case_and_width():
    """2026-11-03 승인: 띄어쓰기 없는 ETF 이름("TIGER미국S&P500")·전각 문자를 무관으로 세지 않는다. 다른 회사 기사는 그대로 잡는다."""
    ok = [{"type": "news", "title": "ETF 순위…TIGER미국S&P500 1위 탈환", "company_names": ["TIGER 미국S&P500"]},
          {"type": "news", "title": "ｔｉｇｅｒ 미국s&p500 순자산 증가", "company_names": ["TIGER 미국S&P500"]}]
    assert FC.evidence_errors(ok, "TIGER 미국S&P500", ["TIGER 미국S&P500"]) == []
    bad = [{"type": "news", "title": "TIGER 미국나스닥100 신고가", "company_names": ["TIGER 미국S&P500"]}]
    assert len(FC.evidence_errors(bad, "TIGER 미국S&P500", ["TIGER 미국S&P500"])) == 1


# ---------- batch 18(2026-11-04, 23차) 채점기 ----------

ANSWER23 = """SK하이닉스(000660) · 향후 5거래일

현재는 단기 반등 시도, 추세 전환은 미확인 (기준일 2026-11-03 종가)

## 종합 판단
단기 신호는 상승 쪽이 많지만(묶음 표 상승 2, 하락 0), 급락 뒤의 반등이라 추세 전환은 확인되지 않았습니다. 과거 검증에서 이 신호 조합(상승 우위)은 방향을 잘 맞히지 못했습니다(상승 비율 51.8% vs 전체 49.9%).

## 신호 판정 상세
- SK하이닉스(000660), 분석 기간 1주일(5거래일): 상승 우위
- 묶음 판정(묶음당 1표): 상승 쪽 2표, 하락 쪽 0표, 중립 1표 (계산한 묶음 3개)
  - 단기 흐름(지표): 상승 쪽 1표 ← 5거래일 수익률 +2.91%(상승 쪽), 지수보다 강한 흐름(상승 쪽)
  - 중기 추세(지표): 중립 1표 ← 종가가 20일 이동평균 아래(하락 쪽), 5일선이 20일선 위(상승 쪽), 2026-11-02 골든크로스(상승 쪽, '5일선이 20일선 위'와 같은 사실이라 세지 않음)
  - MACD(12,26,9): 상승 쪽 1표 ← MACD가 시그널 위(상승 쪽)
"""


def test_layout_accepts_conclusion_first_and_rejects_old_order():
    assert FC.check_layout(ANSWER23, "SK하이닉스", "1주일") == []
    assert FC.check_layout(ANSWER23, "SK하이닉스", "1개월")  # 기간이 다르면 실패
    old = "## 최종 신호 판단\n- SK하이닉스(000660), 분석 기간 1주일(5거래일): 상승 우위\n"
    assert FC.check_layout(old, "SK하이닉스", "1주일")
    labeled = ANSWER23.replace("현재는 단기 반등 시도", "상승 우위 · 현재는 단기 반등 시도")
    assert "제목·상태 줄에 우위 라벨" in " ".join(FC.check_layout(labeled, "SK하이닉스", "1주일"))


def test_double_vote_check_catches_cross_counted_twice():
    assert FC.check_double_vote(ANSWER23) == []
    twice = ANSWER23.replace("(상승 쪽, '5일선이 20일선 위'와 같은 사실이라 세지 않음)", "(상승 쪽)").replace("중기 추세(지표): 중립", "중기 추세(지표): 상승 쪽")
    errs = FC.check_double_vote(twice)
    assert any("따로 셈" in e for e in errs)
    separate = ANSWER23.replace(", 2026-11-02 골든크로스(상승 쪽, '5일선이 20일선 위'와 같은 사실이라 세지 않음)", "") + \
        "  - 이동평균 교차: 상승 쪽 1표 ← 2026-11-02 골든크로스(상승 쪽)\n"
    assert any("다른 묶음" in e for e in FC.check_double_vote(separate))


def test_short_followup_check():
    ok = ("SK하이닉스(000660) · 향후 5거래일 · 현재는 단기 반등 시도, 추세 전환은 미확인 (기준일 2026-11-03 종가)\n\n"
          "지금 데이터로는 1주일 뒤 방향을 믿을 만하게 판단할 수 없습니다.\n\n- 가장 큰 상승 근거: a\n- 가장 큰 하락 위험: b\n"
          "- 확인할 가격대: c\n\n자세한 근거는 바로 위 분석에 있습니다.")
    assert FC.check_short_followup(ok, "SK하이닉스", "1주일", "verdict") == []
    assert FC.check_short_followup(ok + "\n- 판정 규칙: ...", "SK하이닉스", "1주일", "verdict")  # 7줄, 규칙
    assert FC.check_short_followup(ok, "삼성전자", "1주일", "verdict")  # 종목이 바뀜
    assert FC.check_short_followup(ok.replace("지금 데이터로는", "판정 결과는"), "SK하이닉스", "1주일", "verdict")
    assert FC.title_of(ok) == {"name": "SK하이닉스", "ticker": "000660", "period": "1주일"}


@pytest.mark.integration
@pytest.mark.needs_collected_data
def test_numbers23_and_regime_catch_tampering(dev_db):
    import re

    from app import agent

    answer = agent.ask_question("SK하이닉스 일주일 안에 오를지 내릴지 분석해줘", save_report=False)["answer"]
    assert FC.check_numbers23(dev_db, answer, "SK하이닉스") == [] and FC.check_regime(dev_db, answer, "SK하이닉스") == []
    tampered = re.sub(r"60일선\(([\d,]+)원\)", lambda m: f"60일선({int(m.group(1).replace(',', '')) + 5000:,}원)", answer, count=1)
    assert any("60일선" in e for e in FC.check_numbers23(dev_db, tampered, "SK하이닉스"))
    wrong = re.sub(r"52주 기준 최근 250거래일", "52주 기준 최근 240거래일", answer, count=1)
    assert any("조회 기간" in e for e in FC.check_numbers23(dev_db, wrong, "SK하이닉스"))
    flipped = re.sub(r"현재는 [^(]+ \(", "현재는 상승 추세 지속 (", answer, count=1)
    assert FC.check_regime(dev_db, flipped, "SK하이닉스")


# ---------- 24차(batch 20): 현재 판단 규칙·잠정실적 독립 파서 ----------

PRELIM = ("실적기간 당기실적 2026-07-01 ~ 2026-09-30 전기실적 2026-04-01 ~ 2026-06-30 전년동기실적 2025-07-01 ~ 2025-09-30 "
          "※ 동 정보는 잠정치로서 향후 확정치와는 다를 수 있음. 1. 연결실적내용 단위 : 백만원, % 구분 당기실적 전기실적 전기대비 "
          "전년동기실적 전년동기대비 매출액 당해실적 104,615,483 79,318,746 31.9 - 24,448,929 327.9 - 누계실적 236,510,516 - - - "
          "64,320,022 267.7 - 영업이익 당해실적 83,333,818 60,542,608 37.6 - 11,383,390 632.1 - 누계실적 181,486,709 - - - "
          "28,036,745 547.3 - 당기순이익 당해실적 39,775,593 93,922,593 -57.7 - 12,597,538 215.7 - 누계실적 174,044,095 - - - "
          "27,701,949 528.3 - 지배기업 소유주지분 순이익 당해실적 39,534,392 93,820,236 -57.9 - 12,595,195 213.9 - 누계실적 1 - - - 1 1 -")


def test_judgment_rule_needs_label_and_regime_to_agree():
    assert FC.judgment_rule("상승 우위", "상승 추세 지속") == ("상승", None)
    assert FC.judgment_rule("하락 우위", "하락 추세 지속") == ("하락", None)
    assert FC.judgment_rule("상승 우위", "단기 반등 시도, 추세 전환은 미확인") == ("중립", "상승")
    assert FC.judgment_rule("중립", "하락 추세 지속") == ("중립", "하락")
    assert FC.judgment_rule("중립", "상승 추세 중 조정") == ("중립", "상승")
    assert FC.judgment_rule("중립", "횡보, 방향 불분명") == ("중립", None)
    assert FC.judgment_rule("판단 유보", "상승 추세 지속") == ("판단 유보", None)
    assert FC.judgment_rule("상승 우위", "판단 유보(데이터 부족)") == ("판단 유보", None)


def test_label_and_strength_from_tally():
    assert FC.label_from_tally(2, 0, 5) == "상승 우위" and FC.strength_word("상승 우위", 2, 0, 5) == "약한 "
    assert FC.label_from_tally(3, 0, 5) == "상승 우위" and FC.strength_word("상승 우위", 3, 0, 5) == ""
    assert FC.label_from_tally(1, 2, 5) == "중립" and FC.strength_word("중립", 1, 2, 5) == ""
    assert FC.label_from_tally(2, 0, 2) == "판단 유보"


def test_prelim_parser_reads_quarter_not_cumulative():
    p = FC.parse_prelim24("연결재무제표기준영업(잠정)실적(공정공시)", PRELIM)
    assert (p["year"], p["quarter"], p["basis"]) == (2026, 3, "연결")
    assert p["revenue"] == (104_615_483e6, 24_448_929e6, 236_510_516e6)
    assert p["net_income"][0] == 39_775_593e6  # "지배기업 소유주지분 순이익"이 아니라 당기순이익
    assert FC.parse_prelim24("연결재무제표기준영업(잠정)실적(공정공시)(자회사의 주요경영사항)", PRELIM) is None
    assert FC.parse_prelim24("[기재정정]연결재무제표기준영업(잠정)실적(공정공시)", PRELIM) is None
    monthly = PRELIM.replace("2026-07-01 ~ 2026-09-30", "2026-09-01 ~ 2026-09-30")
    assert FC.parse_prelim24("영업(잠정)실적(공정공시)", monthly) is None


def test_judgment24_catches_evasive_and_fake_probability(monkeypatch):
    monkeypatch.setattr(FC, "regime_expected", lambda db, name, base, hold=False: "단기 반등 시도, 추세 전환은 미확인")
    head = "SK하이닉스(000660) · 향후 5거래일 · 현재는 단기 반등 시도, 추세 전환은 미확인 (기준일 2026-11-03 종가)"
    ok = (f"{head}\n\n현재 판단: 중립(굳이 고르면 상승 쪽). 5개 묶음 중 상승 2·하락 0·중립 3으로 단기 신호는 상승 쪽이 조금 많습니다.\n\n"
          "- 예측력: 표본 22,176, 95% 구간 -1.4~+5.3%p로 예측력은 확인되지 않았습니다.")
    assert [e for e in FC.check_judgment24(None, ok, "SK하이닉스", "1주일", "verdict") if "표본" not in e and "구간" not in e] == []
    evasive = f"{head}\n\n지금 데이터로는 1주일 뒤 방향을 믿을 만하게 판단할 수 없습니다. 5개 묶음 중 상승 2·하락 0·중립 3."
    assert any("회피" in e or "현재 판단" in e for e in FC.check_judgment24(None, evasive, "SK하이닉스", "1주일", "verdict"))
    wrong = ok.replace("현재 판단: 중립(굳이 고르면 상승 쪽)", "현재 판단: 상승")
    assert any("규칙" in e for e in FC.check_judgment24(None, wrong, "SK하이닉스", "1주일", "verdict"))
    fake = ok + "\n- 상승 확률은 60%입니다."
    assert any("확률 지어냄" in e for e in FC.check_judgment24(None, fake, "SK하이닉스", "1주일", "verdict"))
