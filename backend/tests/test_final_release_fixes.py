"""최종 점검(2026-11-18) 시연 질문 실검증에서 찾은 문제의 회귀 테스트. DB·Ollama 없음.

1. "근거가 부족한데 확실하게 오를 종목을 알려 줘."가 안전 거절이 아니라 LLM 경로로 갔다('오를'을 보장 표현으로 인식 못 함)
2. 정상 거래일(11-18) 주식 종가가 아직 없는 것을 "휴장일 추정"이라고 썼다(주식은 공식 종가 확인 뒤 다음 거래일에 저장)
3. "셀트리온 지난주 급등"(+1.42%)을 "같은 방향입니다"라고만 해 급등을 인정하는 것처럼 읽혔다
"""
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from app import answer_cause, outlook, request_safety
from tests.test_outlook_round23 import KST


@pytest.mark.parametrize("q", ["근거가 부족한데 확실하게 오를 종목을 알려 줘.", "확실히 오를 종목 추천해줘", "100% 오를 주식 알려줘",
                               "틀림없이 오를 종목은?", "확실한 상승 종목 알려줘"])
def test_certain_rise_requests_are_guarantee_refusals(q):
    assert request_safety.classify(q, has_company=False) == "guarantee"


@pytest.mark.parametrize("q", ["삼성전자 최근 주가 흐름을 분석해 줘.", "이 종목이 상승한 이유를 뒷받침하는 뉴스와 공시를 보여 줘.",
                               "SK하이닉스 향후 5거래일 전망은?", "확실히 알려줘 삼성전자 종가"])
def test_ordinary_questions_are_not_refused(q):
    assert request_safety.classify(q, has_company=True) is None


def test_small_move_is_not_called_a_surge():
    line = answer_cause._period_premise("셀트리온 지난주 급등 이유가 뭐야?", 1.42, "셀트리온")
    assert "방향은 같지만" in line and "'급등'이라고 하기는 어렵습니다" in line and "+1.42%" in line
    line = answer_cause._period_premise("에코프로비엠 지난주 급락 이유", -2.1, "에코프로비엠")
    assert "'급락'이라고 하기는 어렵습니다" in line
    assert answer_cause._period_premise("셀트리온 지난주 급등 이유", 7.3, "셀트리온").startswith("- 질문의 전제('급등')와 같은 방향입니다")
    assert "질문의 전제와 다릅니다" in answer_cause._period_premise("셀트리온 지난주 급등 이유", -0.71, "셀트리온")


class _Q:
    def __init__(self, idx, runs):
        self.idx, self.runs = idx, runs

    def query(self, *a):
        return self

    def filter(self, *a):
        return self

    def order_by(self, *a):
        return self

    def limit(self, n):
        return self

    def scalar(self):
        return self.idx

    def all(self):
        return self.runs


def test_confirmed_trading_day_is_not_presumed_holiday():
    now = datetime(2026, 11, 18, 16, 20, tzinfo=KST)  # 11-18 수집(15:51)이 성공했고 주식은 11-17까지
    runs = [NS(status="success", started_at=datetime(2026, 11, 18, 15, 51, tzinfo=KST))]
    st = outlook.price_status(_Q(date(2026, 11, 17), runs), date(2026, 11, 17), now)
    assert st["status"] == outlook.PRICE_STATUS_OFFICIAL_PENDING and "휴장" not in st["detail"]
    warn = " ".join(outlook.data_check(date(2026, 11, 17), now, [], st)["warnings"])
    assert "휴장일 추정" not in warn and "공식 종가 대기" in warn
