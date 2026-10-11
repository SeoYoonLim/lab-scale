"""리서치 답변 품질 보정 3가지(모두 LLM 1차 호출 뒤에 코드가 하는 일이라 라우팅 벤치마크 경로와 무관하다).

1. strip_role_prefix: 답변 앞에 새어 나온 역할 접두어('assistant' + 줄바꿈) 제거
2. _apply_default_period: 기간을 말하지 않은 흐름 질문의 stock_tool 기본 기간(20거래일)
3. _add_requested_tools: 질문이 이름으로 요청한 주가·뉴스·공시 tool 중 빠진 것 덧붙이기

순수 함수 → _answer() 실행 경로(LLM/tool/DB mock) → /api/research 라우트(LLM mock) 순서로 검증한다.
"""

import copy

import pytest
from fastapi.testclient import TestClient

import app.agent as agent
from app.agent import DEFAULT_TREND_PERIOD_DAYS, strip_role_prefix
from app.main import app
from scripts.benchmark_routing import CASES
from tests.test_period_override import SYNTHETIC_NO_PERIOD_QUESTIONS

Q_TREND = "삼성전자 최근 주가 흐름 알려줘"
Q_BUTTON = "삼성전자 최근 상황을 종합해서 알려줘. 주가, 뉴스, 공시를 같이 확인해줘."  # 종목 상세 [리서치하기] 버튼
ALL_BENCHMARK_QUESTIONS = [q for qs in CASES.values() for q in qs]


def call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


def stock_call(period_days=1, ticker="삼성전자"):
    return call("stock_tool", ticker=ticker, period_days=period_days)


def names(calls):
    return [c["function"]["name"] for c in calls]


# ---------------------------------------------------------------- 1. 역할 접두어 제거


class TestStripRolePrefix:
    @pytest.mark.parametrize(
        "raw, cleaned",
        [
            # 실제로 나온 형태
            ("assistant\n\n## 주가 동향\n삼성전자는 ...", "## 주가 동향\n삼성전자는 ..."),
            ("assistant\n\n카카오 주식이 떨어진 이유는 ...", "카카오 주식이 떨어진 이유는 ..."),
            # 변형: 줄바꿈 한 번, CRLF, 앞 공백, 대문자, 콜론, 템플릿 특수 토큰이 그대로 남은 경우
            ("assistant\n본문", "본문"),
            ("assistant\r\n\r\n본문", "본문"),
            ("  \nassistant\n\n본문", "본문"),
            ("Assistant\n\n본문", "본문"),
            ("assistant:\n본문", "본문"),
            ("<|start_header_id|>assistant<|end_header_id|>\n\n본문", "본문"),
            ("<|start_header_id|>assistant<|end_header_id|>본문", "본문"),
        ],
    )
    def test_prefix_is_removed(self, raw, cleaned):
        assert strip_role_prefix(raw) == cleaned

    @pytest.mark.parametrize(
        "text",
        [
            "## 주가 동향\n삼성전자는 ...",
            "삼성전자의 오늘 거래량은 약 20,157,893 주입니다.",
            # 'assistant'로 시작해도 같은 줄에서 문장이 이어지면 정상 본문이다
            "assistant는 영어로 조수라는 뜻입니다.",
            "assistant 역할을 하는 AI입니다.\n둘째 줄",
            "assistant: 한 줄짜리 답",
            "assistants\n\n복수형은 접두어가 아니다",
            # 본문 중간이나 끝의 assistant는 건드리지 않는다
            "답변입니다.\nassistant\n\n다음 줄",
            "AI assistant\n\n본문",
            "",
            "assistant",
        ],
    )
    def test_normal_text_is_untouched(self, text):
        assert strip_role_prefix(text) == text

    def test_only_the_first_prefix_is_removed(self):
        assert strip_role_prefix("assistant\n\nassistant\n\n본문") == "assistant\n\n본문"

    @pytest.mark.parametrize("value", [None, 123, {"a": 1}, ["x"]])
    def test_non_string_is_returned_as_is(self, value):
        assert strip_role_prefix(value) is value


# ---------------------------------------------------------------- 2. 흐름 질문의 기본 기간


class TestApplyDefaultPeriod:
    @pytest.mark.parametrize(
        "question",
        [
            Q_TREND,
            Q_BUTTON,
            "현대차 주가 추이 좀 보여줘",
            "카카오 요즘 주가 어때?",
            "SK하이닉스 주가 동향 알려줘",
            "005930 주가 흐름 알려줘",  # 종목코드의 숫자는 기간이 아니다
        ],
    )
    @pytest.mark.parametrize("llm_guess", [1, 10, 30, None])
    def test_trend_question_without_a_period_gets_the_default(self, question, llm_guess):
        """모델이 뭘 추측했든(1/10/30, 실측으로 호출마다 달랐다) 기간을 말하지 않은 흐름 질문은 20거래일로 맞춘다."""
        calls = [stock_call(period_days=llm_guess)]
        changed = agent._apply_default_period(calls, question)
        assert changed == {0: llm_guess}
        assert calls[0]["function"]["arguments"] == {"ticker": "삼성전자", "period_days": DEFAULT_TREND_PERIOD_DAYS}
        assert DEFAULT_TREND_PERIOD_DAYS == 20

    @pytest.mark.parametrize(
        "question",
        [
            # 사용자가 기간을 말했다: 파서가 인식하는 표현
            "삼성전자 지난주 주가 흐름 알려줘",
            "삼성전자 최근 3일 주가 추이",
            "삼성전자 오늘 주가 흐름 어때?",
            "삼성전자 3개월 주가 흐름",
            # 파서는 못 바꾸지만 기간인 표현 -> LLM이 추론한 값을 그대로 둔다
            "SK하이닉스 지난 한 달 주가 흐름 어땠어?",
            "삼성전자 일주일 주가 추이 알려줘",
            "삼성전자 최근 5거래일 주가 흐름",
            "삼성전자 하루 주가 움직임 알려줘",
            # 흐름 질문이 아니다
            "삼성전자 주가 알려줘",
            "삼성전자 최근 뉴스 알려줘",
            "삼성전자 최근 공시 뭐 있어?",
            "삼성전자 최근 왜 올랐어? 뉴스도 같이 확인해줘",
            "삼성전자 거래량 얼마야?",
        ],
    )
    def test_stated_period_or_non_trend_question_is_left_alone(self, question):
        calls = [stock_call(period_days=7)]
        before = copy.deepcopy(calls)
        assert agent._apply_default_period(calls, question) == {}
        assert calls == before

    def test_only_stock_tool_is_touched(self):
        calls = [call("market_tool", ticker="삼성전자", period_days=5), call("news_tool", company_name="삼성전자"),
                 call("fx_tool", period_days=5), stock_call(period_days=1)]
        before = copy.deepcopy(calls[:3])
        assert agent._apply_default_period(calls, Q_TREND) == {3: 1}
        assert calls[:3] == before
        assert calls[3]["function"]["arguments"]["period_days"] == 20

    def test_already_default_is_not_reported_as_changed(self):
        calls = [stock_call(period_days=20)]
        assert agent._apply_default_period(calls, Q_TREND) == {}

    def test_json_string_arguments_are_normalized(self):
        calls = [{"function": {"name": "stock_tool", "arguments": '{"ticker": "삼성전자", "period_days": 1}'}}]
        assert agent._apply_default_period(calls, Q_TREND) == {0: 1}
        assert calls[0]["function"]["arguments"] == {"ticker": "삼성전자", "period_days": 20}

    def test_never_overlaps_with_the_period_parser_override(self):
        """파서가 기간을 인식한 질문에는 기본 기간이 절대 적용되지 않는다(둘 중 하나만 period_days를 정한다)."""
        for q in ALL_BENCHMARK_QUESTIONS + [Q_TREND, Q_BUTTON, "삼성전자 지난주 주가 흐름"]:
            a, b = [stock_call()], [stock_call()]
            overridden, defaulted = agent._apply_period_override(a, q), agent._apply_default_period(b, q)
            assert not (overridden and defaulted), q

    def test_synthetic_non_trend_corpus_is_untouched(self):
        """기간도 흐름 표현도 없는 합성 질문 528개(test_period_override와 같은 말뭉치)는 전부 무변경이다."""
        assert len(SYNTHETIC_NO_PERIOD_QUESTIONS) >= 500
        for q in SYNTHETIC_NO_PERIOD_QUESTIONS:
            calls = [stock_call(period_days=1)]
            assert agent._apply_default_period(calls, q) == {}, q
            assert calls == [stock_call(period_days=1)], q


# ---------------------------------------------------------------- 3. 이름으로 요청한 tool 덧붙이기


class TestAddRequestedTools:
    def test_button_question_with_stock_only_gets_news_and_disclosure(self):
        """실측: 버튼 질문에 모델이 stock_tool만 부르는 경우가 10번에 1~2번 있었다."""
        calls = [stock_call()]
        added = agent._add_requested_tools(calls, Q_BUTTON)
        assert added == [1, 2]
        assert calls == [
            stock_call(),
            call("news_tool", company_name="삼성전자"),
            call("disclosure_tool", company_name="삼성전자"),
        ]

    def test_nothing_added_when_the_model_called_everything(self):
        calls = [stock_call(), call("news_tool", company_name="삼성전자"), call("disclosure_tool", company_name="삼성전자")]
        before = copy.deepcopy(calls)
        assert agent._add_requested_tools(calls, Q_BUTTON) == []
        assert calls == before

    def test_existing_calls_are_never_modified_or_reordered(self):
        calls = [call("news_tool", company_name="삼성전자", limit=3)]
        agent._add_requested_tools(calls, Q_BUTTON)
        assert calls[0] == call("news_tool", company_name="삼성전자", limit=3)
        assert names(calls) == ["news_tool", "stock_tool", "disclosure_tool"]
        assert calls[1] == call("stock_tool", ticker="삼성전자")  # 종목 인자 이름은 tool마다 다르다

    def test_rag_search_counts_as_news_and_disclosure(self):
        """공시/뉴스의 '내용'을 찾는 rag_search_tool이 불렸으면 목록 조회 tool을 덧붙이지 않는다."""
        calls = [stock_call(), call("rag_search_tool", query="전환사채", company_name="삼성전자")]
        assert agent._add_requested_tools(calls, "삼성전자 주가랑 전환사채 공시 내용 자세히 알려줘") == []

    def test_market_tool_counts_as_stock(self):
        calls = [call("market_tool", ticker="삼성전자"), call("news_tool", company_name="삼성전자")]
        assert agent._add_requested_tools(calls, "삼성전자 주가랑 뉴스 같이 봐줘") == []

    @pytest.mark.parametrize(
        "question",
        [
            "삼성전자 최근 공시 뭐 있어?",  # 하나만 요청 -> 모델 판단 그대로
            "삼성전자 주가 알려줘",
            "삼성전자 뉴스 말고 주가만 알려줘",  # 일부를 빼달라는 질문은 손대지 않는다
            "삼성전자 공시는 빼고 주가랑 뉴스만",
            "PER이 뭐야?",
        ],
    )
    def test_questions_that_do_not_ask_for_two_kinds_are_left_alone(self, question):
        calls = [stock_call()]
        assert agent._add_requested_tools(calls, question) == []
        assert calls == [stock_call()]

    def test_no_company_in_the_calls_means_no_guessing(self):
        calls = [call("fx_tool", period_days=5)]
        assert agent._add_requested_tools(calls, Q_BUTTON) == []
        assert agent._add_requested_tools([], Q_BUTTON) == []

    def test_two_different_companies_means_no_guessing(self):
        calls = [stock_call(ticker="현대차"), stock_call(ticker="카카오")]
        assert agent._add_requested_tools(calls, "현대차랑 카카오 주가, 뉴스 같이 비교해줘") == []
        assert len(calls) == 2

    def test_same_company_twice_is_one_company(self):
        calls = [stock_call(ticker="삼성전자"), stock_call(ticker=" 삼성전자 ")]
        assert agent._add_requested_tools(calls, "삼성전자 주가랑 뉴스 같이 봐줘") == [2]
        assert calls[2] == call("news_tool", company_name="삼성전자")

    def test_only_the_multi_benchmark_questions_are_affected(self):
        """라우팅 벤치마크 질문 중 이 보정이 걸리는 것은 주가+뉴스를 같이 묻는 multi 그룹뿐이다
        (rag/공시 목록/개념/시장/환율/탐색 질문에는 걸리지 않는다)."""
        from app.routing import explicitly_requested_tools

        hit = {g for g, qs in CASES.items() for q in qs if explicitly_requested_tools(q)}
        assert hit == {"multi"}
        for q in CASES["multi"]:
            assert explicitly_requested_tools(q) in ([], ["stock_tool", "news_tool"])


# ---------------------------------------------------------------- _answer() 실행 경로


class FakeLLM:
    """_first_call(1차 호출)과 최종 답변 호출(ollama.chat), tool 실행, 종목 보정(DB)을 가짜로 바꾼다."""

    def __init__(self, monkeypatch, first_calls, final="최종 답변"):
        self.ran = []
        self.final_messages = None

        def fake_first_call(model, messages, tools=None):
            if first_calls is None:
                return {"role": "assistant", "content": final}, None
            calls = copy.deepcopy(first_calls)
            return {"role": "assistant", "content": "", "tool_calls": calls}, calls

        def fake_run_tool(name, args):
            self.ran.append((name, dict(args)))
            if name == "news_tool":
                return {"found": True, "news": [{"title": "뉴스1", "company_name": "삼성전자", "url": "http://n/1"}]}
            if name == "disclosure_tool":
                return {"found": True, "disclosures": [{"title": "공시1", "company_name": "삼성전자", "source_url": "http://d/1"}]}
            return {"found": True}

        def fake_chat(**kw):
            self.final_messages = kw["messages"]
            return {"message": {"content": final}}

        monkeypatch.setattr(agent, "_first_call", fake_first_call)
        monkeypatch.setattr(agent, "_run_tool", fake_run_tool)
        monkeypatch.setattr(agent, "_repair_company_args", lambda calls, q, prev=None: {})
        monkeypatch.setattr(agent.ollama, "chat", fake_chat)


class TestAnswerPath:
    def test_role_prefix_is_stripped_from_the_final_answer(self, monkeypatch):
        FakeLLM(monkeypatch, [stock_call()], final="assistant\n\n## 주가 동향\n삼성전자는 ...")
        response, _ = agent._answer("삼성전자 주가 알려줘", "m")
        assert response["answer"] == "## 주가 동향\n삼성전자는 ..."

    def test_role_prefix_is_stripped_from_a_no_tool_answer(self, monkeypatch):
        """tool 없이 바로 답한 경로에서도 같은 접두어가 나온 적이 있다(dev DB 리포트 225 'PER이 뭐야?')."""
        FakeLLM(monkeypatch, None, final="assistant\n\n주가수익비율(PER)은 ...")
        response, records = agent._answer("PER이 뭐야?", "m")
        assert response == {"answer": "주가수익비율(PER)은 ...", "used_tools": [], "sources": []}
        assert records == []

    def test_normal_answer_is_unchanged(self, monkeypatch):
        FakeLLM(monkeypatch, [stock_call()], final="삼성전자의 종가는 263,000원입니다.")
        assert agent._answer("삼성전자 주가 알려줘", "m")[0]["answer"] == "삼성전자의 종가는 263,000원입니다."

    def test_trend_question_runs_stock_tool_with_twenty_days(self, monkeypatch):
        llm = FakeLLM(monkeypatch, [stock_call(period_days=1)])
        _, records = agent._answer(Q_TREND, "m")
        assert llm.ran == [("stock_tool", {"ticker": "삼성전자", "period_days": 20})]
        assert records[0]["arguments"]["period_days"] == 20
        assert records[0]["result"]["period_days_corrected_from"] == 1
        assert records[0]["result"]["period_days_corrected_via"] == "default_trend_period"

    def test_stated_period_wins_over_the_default(self, monkeypatch):
        llm = FakeLLM(monkeypatch, [stock_call(period_days=1)])
        _, records = agent._answer("삼성전자 지난주 주가 흐름 알려줘", "m")
        assert llm.ran[0][1]["period_days"] == 5
        assert records[0]["result"]["period_days_corrected_via"] == "period_parser"

    def test_unparsed_period_keeps_the_llm_guess(self, monkeypatch):
        llm = FakeLLM(monkeypatch, [stock_call(period_days=30, ticker="SK하이닉스")])
        _, records = agent._answer("SK하이닉스 지난 한 달 주가 어땠어?", "m")
        assert llm.ran[0][1]["period_days"] == 30
        assert "period_days_corrected_via" not in records[0]["result"]

    def test_button_question_runs_all_three_tools_and_builds_sources(self, monkeypatch):
        llm = FakeLLM(monkeypatch, [stock_call(period_days=1)])
        response, records = agent._answer(Q_BUTTON, "m")
        assert [name for name, _ in llm.ran] == ["stock_tool", "news_tool", "disclosure_tool"]
        assert llm.ran[0][1] == {"ticker": "삼성전자", "period_days": 20}  # '최근 상황'도 흐름 질문이다
        assert llm.ran[1][1] == {"company_name": "삼성전자"} and llm.ran[2][1] == {"company_name": "삼성전자"}
        assert response["used_tools"] == ["stock_tool", "news_tool", "disclosure_tool"]
        assert [s["type"] for s in response["sources"]] == ["news", "disclosure"]
        # 덧붙인 호출만 표시가 남는다
        assert "called_via" not in records[0]["result"]
        assert records[1]["result"]["called_via"] == records[2]["result"]["called_via"] == "question_keywords"

    def test_added_calls_are_shown_to_the_final_llm_call_with_their_results(self, monkeypatch):
        """최종 답변 호출에 넘기는 대화: assistant의 tool_calls 3개와 tool 결과 3개가 짝이 맞아야 한다."""
        llm = FakeLLM(monkeypatch, [stock_call(period_days=1)])
        agent._answer(Q_BUTTON, "m")
        assistant = [m for m in llm.final_messages if m["role"] == "assistant" and m.get("tool_calls")][-1]
        tail = llm.final_messages[llm.final_messages.index(assistant) + 1:]
        assert names(assistant["tool_calls"]) == ["stock_tool", "news_tool", "disclosure_tool"]
        assert [m["role"] for m in tail] == ["tool", "tool", "tool", "system"]

    def test_response_keys_are_unchanged(self, monkeypatch):
        FakeLLM(monkeypatch, [stock_call()])
        response, _ = agent._answer(Q_BUTTON, "m")
        assert set(response) == {"answer", "used_tools", "sources"}


# ---------------------------------------------------------------- /api/research 라우트(LLM만 mock)


class TestResearchRoute:
    @pytest.fixture
    def client(self, monkeypatch, login_as):
        login_as(7)
        saved = []
        monkeypatch.setattr(
            agent, "save_report_row",
            lambda question, answer, records, previous_report_id=None, user_id=None: saved.append(
                {"question": question, "answer": answer, "tools": [r["tool_name"] for r in records], "user_id": user_id}
            ) or 99,
        )
        self.saved = saved
        return TestClient(app, raise_server_exceptions=False)

    def test_button_question_response(self, monkeypatch, client):
        FakeLLM(monkeypatch, [stock_call(period_days=1)], final="assistant\n\n## 주가 동향\n삼성전자는 ...")
        r = client.post("/api/research", json={"question": Q_BUTTON})
        assert r.status_code == 200
        body = r.json()
        # 응답 필드는 그대로다
        assert set(body) == {"answer", "used_tools", "sources", "report_id", "previous_report_id", "disclaimer"}
        assert body["answer"] == "## 주가 동향\n삼성전자는 ..."
        assert body["used_tools"] == ["stock_tool", "news_tool", "disclosure_tool"]
        assert len(body["sources"]) == 2 and body["report_id"] == 99
        # 저장되는 리포트에도 접두어 없는 답변과 덧붙인 tool 호출이 들어간다
        assert self.saved == [{"question": Q_BUTTON, "answer": "## 주가 동향\n삼성전자는 ...",
                               "tools": ["stock_tool", "news_tool", "disclosure_tool"], "user_id": 7}]

    def test_no_tool_answer_response(self, monkeypatch, client):
        FakeLLM(monkeypatch, None, final="assistant\n\nPER은 주가수익비율입니다.")
        body = client.post("/api/research", json={"question": "PER이 뭐야?"}).json()
        assert body["answer"] == "PER은 주가수익비율입니다." and body["used_tools"] == [] and body["sources"] == []
