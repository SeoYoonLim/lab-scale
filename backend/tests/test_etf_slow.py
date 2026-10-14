"""ETF 질문을 Ollama(llama3.1:8b)로 실제 호출하는 테스트. 느리고 비결정적이라 기본 실행에서 빠진다(pytest -m slow).

질문마다 한 번 돌려서 코드가 보장하는 것만 단언한다: 호출된 tool(가격+뉴스), 기간(말하지 않으면 20거래일), 답변의 등락률이
주가 조회 결과에 있는 값인지, 템플릿 잔재("빈칸입니다")가 없는지, 레버리지/인버스면 고정 문구가 붙는지. 같은 질문 3회 반복 결과는
2단계 보고서(사실 대조 표)에 따로 남겼다.
"""

import re
from decimal import Decimal

import pytest

from app.agent import DEFAULT_TREND_PERIOD_DAYS, MODEL_NAME, _answer
from app.etf import allowed_percents, check_etf_answer

pytestmark = [pytest.mark.slow, pytest.mark.integration]

RESIDUE = re.compile(r"빈칸|빈 칸|이 섹션은|해당\s?(?:사항\s?)?없음")
PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
NOTICE = "복리 효과"
NEWS_LIST = re.compile(r"## 관련 뉴스\n(?:- .*(?:\n|$))*")

# (질문, 기대하는 stock_tool 대상, 기간: None이면 20거래일 기본값, 숫자면 그 값 이상, 레버리지/인버스 고정 문구가 붙는가)
CASES = [
    ("KODEX 200 최근 흐름 알려줘", ["KODEX 200"], None, False),
    ("TIGER 미국나스닥100 어때?", ["TIGER 미국나스닥100"], None, False),
    ("KODEX 레버리지 위험해?", ["KODEX 레버리지"], None, True),
    ("KODEX 인버스는 어떤 상품이야?", ["KODEX 인버스"], None, True),
    ("TIGER 미국S&P500 한 달 흐름", ["TIGER 미국S&P500"], 15, False),
    ("KODEX 200이랑 삼성전자 중에 최근 어느 게 더 올랐어?", ["KODEX 200", "삼성전자"], None, False),
    ("KODEX 200 주가 알려줘", ["KODEX 200"], None, False),
    ("KODEX 코스닥150레버리지 왜 떨어졌어?", ["KODEX 코스닥150레버리지"], None, True),
    ("TIGER 200 최근 뉴스 알려줘", ["TIGER 200"], None, False),
    ("KODEX 200TR 3개월 추이", ["KODEX 200TR"], 55, False),
]


@pytest.mark.parametrize("question, subjects, period, leveraged", CASES, ids=[c[0] for c in CASES])
def test_etf_question(question, subjects, period, leveraged):
    resp, records = _answer(question, MODEL_NAME)
    stock = {r["result"].get("company_name"): r for r in records if r["tool_name"] == "stock_tool" and isinstance(r["result"], dict)}
    news = {r["result"].get("company_name") for r in records if r["tool_name"] == "news_tool" and isinstance(r["result"], dict)}

    # (a) 가격 흐름은 질문 속 종목마다, 뉴스는 ETF마다
    assert set(subjects) <= set(stock), (question, list(stock))
    assert subjects[0] in news, (question, news)
    # (b) 기간
    for name in subjects:
        days = stock[name]["arguments"]["period_days"]
        assert days == DEFAULT_TREND_PERIOD_DAYS if period is None else days >= period, (question, name, days)
    # (c) 답변의 등락률은 주가 조회 결과에 있는 값뿐이다
    # '## 관련 뉴스'의 목록 줄은 코드가 기사 제목을 그대로 인용한 것이라(제목 속 수치는 기사의 것) 수치 검사에서 뺀다
    full = resp["answer"]
    answer = NEWS_LIST.sub("", full)
    allowed = allowed_percents([r["result"] for r in records if r["tool_name"] in ("stock_tool", "market_tool")])
    assert [p for p in PCT.findall(answer) if Decimal(p).normalize() not in allowed] == [], full
    # (c') 본문(고정 문구 앞)을 다시 대조해도 지울 문장이 없다: 종목별 수치·기간 등락 방향·출처 없는 상품 설명 등
    prices = {name: r["result"] for name, r in stock.items() if r["result"].get("found")}
    body = answer.split("\n\n※")[0]
    assert check_etf_answer(body, prices)[1] == [], full
    if len(subjects) > 1:
        assert "※ 조회 결과로 계산한 비교:" in full
    # (d) 템플릿 잔재 없음, 레버리지/인버스 고정 문구
    assert not RESIDUE.search(full), full
    assert (NOTICE in full) is leveraged, full
