"""후속 질문(previous_report_id)이 agent 메시지에 어떻게 들어가는지 검증한다. LLM 호출(_first_call)만 가로챈다."""

import pytest

import app.agent as agent

FEW = agent.FEW_SHOT_MESSAGES
PREV = {"report_id": 5, "question": "삼성전자 최근 주가 어때?", "answer": "삼성전자는 최근 3일간 하락했습니다."}


def capture(monkeypatch):
    seen = {}

    def fake_first_call(model, messages, tools=None):
        seen["messages"] = messages
        return {"role": "assistant", "content": "답변"}, None

    monkeypatch.setattr(agent, "_first_call", fake_first_call)
    return seen


def test_without_previous_messages_are_system_fewshot_question_only(monkeypatch):
    seen = capture(monkeypatch)
    agent._answer("삼성전자 최근 뉴스 알려줘", "m")
    assert seen["messages"] == [
        {"role": "system", "content": agent.SYSTEM_PROMPT},
        *FEW,
        {"role": "user", "content": "삼성전자 최근 뉴스 알려줘"},
    ]


def test_previous_turn_is_inserted_between_fewshot_and_new_question(monkeypatch):
    seen = capture(monkeypatch)
    agent._answer("그럼 최근 뉴스는?", "m", PREV)
    m = seen["messages"]
    assert m[0]["role"] == "system" and m[0]["content"] == agent.SYSTEM_PROMPT
    assert m[1 : 1 + len(FEW)] == FEW
    assert m[1 + len(FEW) :] == [
        {"role": "user", "content": PREV["question"]},
        {"role": "assistant", "content": PREV["answer"]},
        {"role": "user", "content": "그럼 최근 뉴스는?"},
    ]


def test_long_previous_answer_is_truncated(monkeypatch):
    seen = capture(monkeypatch)
    long_answer = "가" * (agent.MAX_PREVIOUS_ANSWER_CHARS + 500)
    agent._answer("그럼?", "m", {**PREV, "answer": long_answer})
    prev_answer = seen["messages"][-2]["content"]
    assert len(prev_answer) == agent.MAX_PREVIOUS_ANSWER_CHARS and prev_answer.endswith("…")


def test_answer_at_limit_is_kept_whole(monkeypatch):
    seen = capture(monkeypatch)
    exact = "가" * agent.MAX_PREVIOUS_ANSWER_CHARS
    agent._answer("그럼?", "m", {**PREV, "answer": exact})
    assert seen["messages"][-2]["content"] == exact


def test_market_gate_uses_only_the_new_question(monkeypatch):
    seen = capture(monkeypatch)
    agent._answer("그럼 코스피랑 비교하면?", "m", PREV)
    assert agent.MARKET_HINT in seen["messages"][0]["content"]
    agent._answer("그럼 최근 뉴스는?", "m", {**PREV, "question": "코스피 대비 삼성전자는?"})
    assert agent.MARKET_HINT not in seen["messages"][0]["content"]


def test_ask_question_saves_and_returns_previous_report_id(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        agent, "_answer", lambda q, m, previous=None: ({"answer": "a", "used_tools": [], "sources": []}, [])
    )

    def fake_save(question, answer, records, previous_report_id=None):
        saved["previous_report_id"] = previous_report_id
        return 9

    monkeypatch.setattr(agent, "save_report_row", fake_save)
    r = agent.ask_question("그럼 뉴스는?", previous=PREV)
    assert r["report_id"] == 9 and r["previous_report_id"] == 5 and saved["previous_report_id"] == 5

    r = agent.ask_question("삼성전자 뉴스")
    assert r["previous_report_id"] is None and saved["previous_report_id"] is None


class TestRepairFromPreviousQuestion:
    """후속 질문엔 회사명이 없어서, 모델이 종목 인자를 엉뚱한 문자열로 지어내면 직전 질문에서 회사를 되찾는다."""

    @staticmethod
    def calls(name, tool="news_tool", key="company_name"):
        return [{"function": {"name": tool, "arguments": {key: name}}}]

    @pytest.fixture(autouse=True)
    def fake_session(self, monkeypatch, make_db):
        class DB:
            def __init__(self):
                self.inner = make_db("삼성전자", "카카오")

            def query(self, model):
                return self.inner.query(model)

            def close(self):
                pass

        monkeypatch.setattr(agent, "SessionLocal", DB)

    def test_garbled_company_is_repaired_from_previous_question(self):
        calls = self.calls("산호다항백")
        repaired = agent._repair_company_args(calls, "그럼 최근 뉴스는?", "삼성전자 최근 주가 어때?")
        assert repaired == {0: "산호다항백"}
        assert calls[0]["function"]["arguments"]["company_name"] == "삼성전자"

    def test_works_for_stock_tool_ticker_argument(self):
        calls = self.calls("살호백의주집하세요", "stock_tool", "ticker")
        agent._repair_company_args(calls, "거래량은 얼마나 돼?", "삼성전자 최근 주가 어때?")
        assert calls[0]["function"]["arguments"]["ticker"] == "삼성전자"

    def test_company_in_new_question_wins_over_previous_question(self):
        calls = self.calls("깨진이름")
        agent._repair_company_args(calls, "카카오는 어때?", "삼성전자 최근 주가 어때?")
        assert calls[0]["function"]["arguments"]["company_name"] == "카카오"

    def test_valid_company_argument_is_left_alone(self):
        calls = self.calls("카카오")
        assert agent._repair_company_args(calls, "그럼 최근 뉴스는?", "삼성전자 최근 주가 어때?") == {}
        assert calls[0]["function"]["arguments"]["company_name"] == "카카오"

    def test_without_previous_question_nothing_is_guessed(self):
        calls = self.calls("산호다항백")
        assert agent._repair_company_args(calls, "그럼 최근 뉴스는?") == {}
        assert calls[0]["function"]["arguments"]["company_name"] == "산호다항백"

    def test_ambiguous_previous_question_is_not_guessed(self):
        # 직전 질문에 회사가 둘이면 어느 쪽인지 알 수 없으니 고치지 않는다(기존 규칙: 후보와 실패 호출이 1:1일 때만)
        calls = self.calls("깨진이름")
        assert agent._repair_company_args(calls, "그럼 뉴스는?", "삼성전자랑 카카오 주가 비교해줘") == {}

    def test_answer_passes_previous_question_to_repair(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            agent,
            "_first_call",
            lambda model, messages, tools=None: (
                {"role": "assistant", "content": "", "tool_calls": self.calls("깨진이름")},
                self.calls("깨진이름"),
            ),
        )
        monkeypatch.setattr(agent, "_run_tool", lambda name, args: seen.setdefault("args", dict(args)) and {"found": True})
        monkeypatch.setattr(agent.ollama, "chat", lambda **kw: {"message": {"content": "답변"}})
        agent._answer("그럼 최근 뉴스는?", "m", PREV)
        assert seen["args"] == {"company_name": "삼성전자"}
