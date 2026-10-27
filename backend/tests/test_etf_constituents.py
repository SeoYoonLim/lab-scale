"""(B) ETF 구성종목·편입 비중 질문: 판정(app/etf.py)과 고정 답변, 에이전트 연결."""

import pytest

from app import agent
from app.etf import asks_constituents, constituents_answer
from evals.metrics import extract_numbers, is_refusal


@pytest.mark.parametrize("q", [
    "KODEX 200 구성종목 비중 알려줘", "TIGER 미국나스닥100에 어떤 종목들이 들어있어?", "KODEX 반도체 편입 비중 상위 종목 알려줘",
    "TIGER 미국S&P500 보유종목 TOP10은?", "KODEX 2차전지산업에 무슨 종목이 들어 있어?", "TIGER 반도체TOP10 구성종목이랑 비중 알려줘",
    "ACE 미국나스닥100 상위 보유 종목이 뭐야?", "KODEX 200 종목 구성 알려줘", "TIGER 200 구성 비중은?", "KODEX 반도체에 담긴 종목은?",
])
def test_constituent_questions(q):
    assert asks_constituents(q)


@pytest.mark.parametrize("q", [
    "KODEX 200 비중 늘려도 돼?", "KODEX 레버리지 장기 보유해도 괜찮아?", "TIGER 반도체TOP10 주가 알려줘",
    "KODEX 200 최근 가격 알려줘", "KODEX 인버스는 어떤 상품이야?", "TIGER 미국나스닥100 최근 뉴스 알려줘",
])
def test_other_etf_questions(q):
    assert not asks_constituents(q)


def test_answer_refuses_without_names_or_weights():
    text = constituents_answer(["KODEX 200"])
    assert "제공하지 않습니다" in text and "운용사" in text and is_refusal(text)
    assert not [n for n in extract_numbers(text) if n.unit == "%"]


def test_leverage_notice_is_kept():
    text = constituents_answer(["KODEX 레버리지", "KODEX 200"])
    assert "KODEX 레버리지, KODEX 200의 구성종목" in text
    assert "레버리지 ETF입니다" in text and text.count("※") == 1


class Co:
    def __init__(self, name, asset_type):
        self.name, self.asset_type = name, asset_type


def test_agent_wiring():
    resp, records = agent._answer_etf_constituents("KODEX 200 구성종목 알려줘", [Co("KODEX 200", "etf")])
    assert "제공하지 않습니다" in resp["answer"] and records == [] and resp["used_tools"] == []
    assert agent._answer_etf_constituents("삼성전자 구성종목", [Co("삼성전자", "stock")]) is None  # ETF가 아님
    assert agent._answer_etf_constituents("KODEX 200 주가", [Co("KODEX 200", "etf")]) is None
