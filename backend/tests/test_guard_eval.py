"""숫자 검증기(app/answer_guard)를 평가기와 독립된 고정 세트로 잰다(DB·Ollama 없음).

- tests/fixtures/guard_cases.json(60개): 규칙을 보기 전에 작성. 보강 전 재현율 0.57, 정밀도 0.89, 정상 통과율 0.93.
- tests/fixtures/guard_cases_holdout.json(20개): 보강 뒤 규칙에 맞추지 않고 작성해 한 번만 측정. 보강 전 0.40 / 0.80 / 0.90.
아래 하한은 2026-10-28 보강 후 측정값이다. 내려가면(위반을 더 놓치거나 정상 답변을 더 막으면) 실패한다.
"""

from datetime import date
from pathlib import Path

import pytest

from app import answer_guard as G
from guard_eval import CASES, load_cases, run

HOLDOUT = CASES.with_name("guard_cases_holdout.json")


@pytest.fixture(scope="module")
def dev():
    return run()


@pytest.fixture(scope="module")
def holdout():
    return run(path=HOLDOUT)


def test_fixture_shape():
    scenarios, cases = load_cases()
    assert sum(c["label"] == "violation" for c in cases) == 30 and sum(c["label"] == "ok" for c in cases) == 30
    assert {c["type"] for c in cases if c["label"] == "violation"} == {"digit", "invented", "wrong_target", "unit", "date"}
    assert all(c["scenario"] in scenarios for c in cases)
    assert len({c["id"] for c in cases}) == len(cases)
    _, h = load_cases(HOLDOUT)
    assert len(h) == 20 and not ({c["id"] for c in cases} & {c["id"] for c in h})


def test_dev_set_recall_and_precision(dev):
    assert dev["recall"] >= 0.9, dev["missed"]  # 27/30
    assert dev["precision"] == 1.0 and dev["normal_pass_rate"] == 1.0, dev["false_alarms"]


def test_holdout_set_recall_and_precision(holdout):
    assert holdout["recall"] >= 0.8, holdout["missed"]  # 8/10
    assert holdout["normal_pass_rate"] == 1.0, holdout["false_alarms"]


def test_known_misses_are_documented(dev, holdout):
    """못 잡는 유형(한계, evals/README.md): 같은 종목 시계열의 우연한 차이·변화율과 같은 값(v18, v21), 시계열 행이 없는
    기준일의 값(v28), '하루 동안'처럼 하루·기간 말이 함께 있는 문장(hv09), 종목코드 문자열이 숫자 근거가 됨(hv10)."""
    assert set(dev["missed"]) <= {"v18", "v21", "v28"}
    assert set(holdout["missed"]) <= {"hv09", "hv10"}


# ---------- 규칙별 ----------

S = load_cases()[0]


def test_date_not_in_results_is_a_violation():
    assert G.date_violations("10월 29일 종가는 225,000원입니다.", [r["result"] for r in S["samsung_5d"]]) == ["10월 29일"]


def test_today_and_question_dates_are_allowed():
    results = [r["result"] for r in S["samsung_5d"]]
    assert G.date_violations("오늘(10월 28일) 기준입니다.", results, "", date(2026, 10, 28)) == []
    assert G.date_violations("10월 1일 이후 자료는 위와 같습니다.", results, "10월 1일부터 알려줘") == []


def test_value_right_after_a_date_must_be_that_dates_value():
    results = [r["result"] for r in S["samsung_5d"]]
    assert G.date_violations("10월 24일 종가는 225,000원입니다.", results) == ["10월 24일 225,000원"]
    # 날짜마다 바로 뒤 첫 값만 본다: 차이(11,500원)는 날짜 값이 아니어도 된다
    assert G.date_violations("10월 24일 213,500원에서 10월 27일 225,000원으로 11,500원 올랐습니다.", results) == []


def test_no_dated_results_means_no_date_check():
    assert G.date_violations("10월 29일에 발표 예정입니다.", [{"found": True, "items": []}]) == []


def test_number_of_the_other_company_is_a_target_violation():
    recs = S["hyundai_kia_5d"]
    assert G.target_violations("현대차의 최근 종가는 101,900원입니다.", recs) == ["현대차 101,900원"]
    assert G.target_violations("현대차 262,500원, 기아 101,900원입니다.", recs) == []  # 두 종목을 함께 말한 문장
    assert G.target_violations("현대차의 최근 종가는 101,900원입니다.", recs[:1]) == []  # 종목 하나뿐


def test_daily_and_period_change_confusion():
    recs = S["samsung_5d"]
    assert G.period_violations("하루 등락률은 +7.66%입니다.", recs) == ["+7.66%"]
    assert G.period_violations("5거래일 수익률은 5.39%입니다.", recs) == ["5.39%"]
    assert G.period_violations("5거래일간 7.66% 올랐고 마지막 거래일에는 5.39% 올랐습니다.", recs) == []


def test_cross_company_difference_is_supported():
    out, log = G.guard_answer("현대차의 상승률이 기아보다 1.87%p 높았습니다.", S["hyundai_kia_5d"], "어디가 더 올랐어?",
                              today=date(2026, 10, 28))
    assert log["replaced"] is False


def test_guard_log_counts_each_kind():
    _, log = G.guard_answer("현대차의 10월 29일 종가는 101,900원입니다.", S["hyundai_kia_5d"], "현대차 주가",
                            today=date(2026, 10, 28))
    assert log["replaced"] is True and log["kinds"]["date"] == 1 and log["kinds"]["target"] == 1


def test_value_then_parenthesized_date_is_paired_backwards():
    """stock_tool 요약 문장 형식("최고는 225,000원(2026-10-27), 최저는 212,000원(2026-10-21)")은 괄호 앞의 값과 짝짓는다."""
    results = [r["result"] for r in S["samsung_5d"]]
    ok = "이 기간 종가 최고는 225,000원(2026-10-27), 최저는 212,000원(2026-10-21)입니다."
    assert G.date_violations(ok, results) == []
    assert G.date_violations("최고는 225,000원(2026-10-21)입니다.", results) == ["2026-10-21 225,000원"]
    summary = results[0]["summary"]
    assert G.guard_answer(summary, S["samsung_5d"], "삼성전자 주가", today=date(2026, 10, 28))[1]["replaced"] is False


def test_dates_inside_a_period_span_are_not_paired_with_one_day_value():
    """dev 평가(2026-10-28)에서 ETF 비교 문장의 기간 등락률이 끝 날짜의 하루 값으로 오인돼 정상 답변이 바뀌었다."""
    results = [r["result"] for r in S["samsung_5d"]]
    assert G.date_violations("삼성전자는 2026-10-20~2026-10-27 5거래일간 +7.66%입니다.", results) == []
    assert G.date_violations("10월 20일 대비 10월 27일까지 7.66% 올랐습니다.", results) == []
    assert G.date_violations("10월 24일 종가는 225,000원입니다.", results) == ["10월 24일 225,000원"]  # 하루 값 오기는 그대로
