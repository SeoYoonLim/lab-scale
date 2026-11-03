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
