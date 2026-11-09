"""두 시점 사이 수익률: 날짜 인식, 대상 판별, 문장. DB 없이 돈다(SQL 검산은 test_holding_return_devdb.py)."""

from datetime import date

import pytest

from app import period_answers as A
from app.period_scope import date_pair

TODAY = date(2026, 11, 2)


@pytest.mark.parametrize("q, start, end", [
    ("삼성전자를 2025년 1월 2일에 사서 2026년 1월 2일까지 들고 있었으면 수익률이 얼마야?", date(2025, 1, 2), date(2026, 1, 2)),
    ("현대차 2025년 2월 3일부터 2025년 8월 1일까지 얼마나 올랐어?", date(2025, 2, 3), date(2025, 8, 1)),
    ("TIGER 200 2025년 1월 6일부터 6월 30일까지 수익률 알려줘", date(2025, 1, 6), date(2025, 6, 30)),
    ("한화오션 2024-07-01 대비 2025-07-01 주가는 몇 퍼센트 변했어?", date(2024, 7, 1), date(2025, 7, 1)),
    ("HD현대중공업 25년 1월 2일에 사서 26년 1월 2일까지 들고 있었다면?", date(2025, 1, 2), date(2026, 1, 2)),
    ("기아 작년 3월 4일에 샀으면 올해 3월 4일까지 수익률은?", date(2025, 3, 4), date(2026, 3, 4)),
    ("카카오 2024년 3월부터 2025년 6월까지 수익률", date(2024, 3, 1), date(2025, 6, 30)),
    ("LG전자를 2025년 1월 2일에 샀으면 지금까지 수익률은?", date(2025, 1, 2), TODAY),
    ("셀트리온 2025년 9월 1일부터 2025년 3월 3일까지 수익률", date(2025, 9, 1), date(2025, 3, 3)),
])
def test_date_pair(q, start, end):
    assert date_pair(q, TODAY) == (start, end)


@pytest.mark.parametrize("q", ["삼성전자 2025년 1월 2일 종가 알려줘", "삼성전자 주가 알려줘", "2024년 등락률은?",
                               "셀트리온 올해 1월부터 지금까지 등락률은?"])  # 달 단위는 기간 등락률 경로다(전년 말 종가 기준, ref_12)
def test_date_pair_needs_two_points(q):
    assert date_pair(q, TODAY) is None


@pytest.mark.parametrize("q", [
    "삼성전자를 2025년 1월 2일에 사서 2026년 1월 2일까지 들고 있었으면?",
    "현대차 2025년 2월 3일부터 2025년 8월 1일까지 얼마나 올랐어?",
    "한화오션 2024년 7월 1일 대비 2025년 7월 1일 주가는 몇 퍼센트 변했어?",
    "기아 2025년 5월 2일부터 2025년 10월 5일까지 수익률 계산해줘",
    "SK하이닉스에 2024년 6월 3일 투자했다면 2025년 6월 2일에는 수익률이 어떻게 됐을까?",
])
def test_holding_questions(q):
    assert A.is_holding_return_question(q)


@pytest.mark.parametrize("q", [
    "삼성전자 2025년 1월 2일부터 2026년 1월 2일까지 왜 올랐어?",
    "삼성전자 2025년 1월부터 2026년 1월까지 뉴스 알려줘",
    "삼성전자 2025년 1월 2일부터 2025년 3월 2일까지 거래량 알려줘",
    "삼성전자 2025년 1월 2일부터 2025년 3월 2일까지 최고가는?",
    "2025년 1월 2일부터 2026년 1월 2일까지 코스피 대비 삼성전자 수익률",
])
def test_not_holding_questions(q):
    assert not A.is_holding_return_question(q)


DATA = (date(2023, 10, 30), date(2026, 10, 30))


@pytest.mark.parametrize("start, end, word", [
    (date(2025, 9, 1), date(2025, 3, 3), "시작일"),
    (date(2025, 3, 3), date(2025, 3, 3), "시작일"),
    (date(2025, 1, 2), date(2027, 1, 4), "아직 오지 않은"),
    (date(2022, 1, 3), date(2024, 1, 3), "2023-10-30"),
    (date(2026, 10, 31), date(2026, 11, 1), "2023-10-30"),
])
def test_invalid_periods_are_refused(start, end, word):
    from evals.metrics import is_refusal

    text = A.holding_invalid_text("카카오", start, end, TODAY, *DATA)
    assert word in text and is_refusal(text)


def test_valid_period_is_not_refused():
    assert A.holding_invalid_text("카카오", date(2025, 1, 2), date(2026, 1, 2), TODAY, *DATA) is None
    # 끝이 보유 범위 뒤여도 오늘 이전이면 직전 거래일로 계산한다
    assert A.holding_invalid_text("카카오", date(2025, 1, 2), date(2026, 11, 1), TODAY, *DATA) is None


RESULT = {"base_date": "2025-03-04", "base_close": 205500.0, "period_change_pct": 4.87,
          "prices": [{"price_date": "2025-09-01", "close_price": 215500.0}]}


def test_holding_text_states_values_and_basis():
    text = A.holding_text("NAVER", date(2025, 3, 4), date(2025, 9, 1), RESULT)
    assert "2025-03-04 종가 205,500원" in text and "2025-09-01 종가 215,500원" in text and "+4.87%" in text
    assert A.HOLDING_BASIS_NOTE in text and "거래일이 아니어서" not in text and "시세가 없어" not in text


def test_holding_text_states_substituted_dates():
    text = A.holding_text("NAVER", date(2025, 3, 1), date(2025, 9, 7), RESULT)
    assert "시작일 2025-03-01은 거래일이 아니어서 다음 거래일 2025-03-04" in text
    assert "종료일 2025-09-07의 시세가 없어 직전 거래일 2025-09-01" in text
