import pytest

import app.agent as agent
from app.routing import needs_market_tool
from scripts.benchmark_routing import CASES

BASE_TOOLS = ["stock_tool", "news_tool", "disclosure_tool", "rag_search_tool"]

# 키워드를 정할 때 보지 않고 따로 쓴 질문(첫 실행 결과: 12/14 잡힘, '장세'만 뒤에 패턴 추가).
# 마지막 둘은 키워드가 전혀 없는 표현이라 못 잡는 것이 정상이다.
FRESH_MARKET = [
    "삼성전자 오늘 하락이 코스피 전체가 빠져서 그런 거야?",
    "네이버가 시장 수익률보다 많이 올랐는지 궁금해",
    "SK하이닉스 지난주 낙폭이 증시 전반 약세 때문인가?",
    "현대차가 지수 상승률만큼 올랐어?",
    "카카오 주가가 시장 분위기랑 따로 노는 것 같은데 비교해줘",
    "셀트리온 최근 한 달 성과를 시장 평균과 비교해줘",
    "POSCO홀딩스 급락이 전체 장세 탓인지 알려줘",
    "알테오젠이 코스닥 대비 얼마나 올랐어?",
    "삼성바이오로직스 이번 주 지수보다 나았어?",
    "기아 주가가 시장 전체가 아니라 자기 요인으로 움직인 건지 확인해줘",
    "두산에너빌리티 상승이 시장 베타 때문인지 개별 이슈 때문인지",
    "크래프톤 이번 주에 시장 대비 아웃퍼폼했어?",
    "한화에어로스페이스 벤치마크 대비 성과 알려줘",
]
KNOWN_MISSES = ["LG화학이 다른 종목들보다 더 빠진 거야, 아니면 다 같이 빠진 거야?"]

# 시장/지수라는 단어가 들어 있지만 비교 질문이 아닌 것. 이 중 뒤의 셋은 키워드 방식이 구분 못 하는 알려진 오탐이다.
FRESH_NOT_MARKET = [
    "삼성전자 시장 점유율이 어떻게 돼?",
    "카카오 해외 시장 진출 관련 공시 내용 자세히 알려줘",
    "공포 탐욕 지수가 뭐야?",
    "소비자물가지수가 오르면 금리는 어떻게 돼?",
    "현대차 전기차 시장에서 점유율 관련 뉴스 알려줘",
    "삼성전자 공장 전체 가동률 관련 공시 알려줘",
    "네이버 최근 뉴스 알려줘",
    "LG에너지솔루션 시장 조사 기관 전망 관련 공시 내용",
    "SK하이닉스 지수 편입 이슈 알려줘",
    "배당 성향이 뭐야?",
    "카카오 지수 산정 방식이 바뀌었어?",
    "증시 용어 중 서킷브레이커가 뭐야?",
]
KNOWN_FALSE_POSITIVES = [
    "PER이 시장 전체 평균보다 높으면 고평가야?",
    "코스피 200이 뭐야?",
    "코스닥 상장 요건 알려줘",
]


@pytest.mark.parametrize("group", ["market", "market_ho"])
def test_all_benchmark_market_questions_match(group):
    missed = [q for q in CASES[group] if not needs_market_tool(q)]
    assert missed == []


def test_no_false_positive_on_other_benchmark_groups():
    total = 0
    hits = []
    for group, questions in CASES.items():
        if group.startswith("market"):
            continue
        total += len(questions)
        hits += [q for q in questions if needs_market_tool(q)]
    assert total >= 38 and hits == []


@pytest.mark.parametrize("q", FRESH_MARKET)
def test_fresh_market_questions_match(q):
    assert needs_market_tool(q)


@pytest.mark.parametrize("q", KNOWN_MISSES)
def test_known_misses_have_no_keyword(q):
    assert not needs_market_tool(q)


@pytest.mark.parametrize("q", FRESH_NOT_MARKET)
def test_non_comparison_questions_do_not_match(q):
    assert not needs_market_tool(q)


@pytest.mark.parametrize("q", KNOWN_FALSE_POSITIVES)
def test_known_false_positives(q):
    # 오탐이어도 tool 목록이 5개가 될 뿐이라 허용하지만, 동작이 바뀌면 알 수 있게 고정해 둔다.
    assert needs_market_tool(q)


@pytest.mark.parametrize("q", ["", None, "   "])
def test_empty_question(q):
    assert not needs_market_tool(q)


class TestBuildRequest:
    def test_default_request_is_unchanged_for_non_market_question(self):
        prompt, tools = agent.build_request("삼성전자 최근 3일 등락률이랑 거래량 알려줘.")
        assert prompt is agent.SYSTEM_PROMPT
        assert tools is agent.TOOLS
        assert [t["function"]["name"] for t in tools] == BASE_TOOLS

    def test_market_tool_is_never_in_default_tools(self):
        assert "market_tool" not in [t["function"]["name"] for t in agent.TOOLS]

    def test_market_question_adds_tool_and_hint_without_mutating_defaults(self):
        prompt, tools = agent.build_request("현대차가 코스피보다 더 올랐어?")
        assert [t["function"]["name"] for t in tools] == BASE_TOOLS + ["market_tool"]
        assert agent.MARKET_HINT in prompt and prompt != agent.SYSTEM_PROMPT
        assert prompt.replace(agent.MARKET_HINT, "", 1) == agent.SYSTEM_PROMPT
        assert [t["function"]["name"] for t in agent.TOOLS] == BASE_TOOLS

    def test_market_tool_runs_through_the_agent_dispatch(self):
        assert agent.AVAILABLE_FUNCTIONS["market_tool"] is not None
        assert agent._COMPANY_ARG["market_tool"] == "ticker"
        spec = agent.MARKET_TOOL_SPEC["function"]
        assert spec["name"] == "market_tool" and spec["parameters"]["required"] == ["ticker"]

    def test_answer_passes_gated_tools_to_the_llm(self, monkeypatch):
        seen = {}

        def fake_first_call(model, messages, tools=None):
            seen["tools"] = [t["function"]["name"] for t in tools]
            seen["system"] = messages[0]["content"]
            return {"role": "assistant", "content": "답변"}, None

        monkeypatch.setattr(agent, "_first_call", fake_first_call)
        agent._answer("셀트리온 주가 빠진 게 장 전체가 약해서야?", "m")
        assert seen["tools"][-1] == "market_tool" and agent.MARKET_HINT in seen["system"]
        agent._answer("삼성전자 최근 뉴스 알려줘", "m")
        assert seen["tools"] == BASE_TOOLS and agent.MARKET_HINT not in seen["system"]
