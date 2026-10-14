"""사건 이후 주가 반응 질문의 에이전트 경로(mock) + /api/research 라우터(mock) + 개발 DB에서 tool 결과 = API 결과(integration)."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.agent as agent
from app.event_answer import SECTION_TITLE
from app.event_reaction import DISCLAIMER, summarize
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def _event(kind="disclosure", title="자기주식취득결과보고서", d1=-4.56, d5=None):
    def h(p, n):
        if p is None:
            return {"pct": None, "index_pct": None, "excess_pct": None, "available": False, "reason": f"{n}거래일 경과 전"}
        return {"pct": p, "index_pct": -3.64, "excess_pct": round(p + 3.64, 2), "available": True, "reason": None}

    return {
        "event_type": kind, "title": title, "url": "https://dart.fss.or.kr/x", "event_at": None, "event_date": "2026-10-12",
        "time_known": False, "base_date": "2026-10-08", "base_close": 263000.0, "reaction_start_date": "2026-10-12",
        "returns": {"d1": h(d1, 1), "d5": h(d5, 5), "d20": h(None, 20)},
        "volume_ratio": 1.17, "volume_sample_days": 20, "other_events_in_window": 1, "note": None,
    }


def _result(events):
    return {
        "found": True, "company_name": "삼성전자", "ticker": "005930", "asset_type": "stock", "market_index": "KS11",
        "price_as_of": "2026-10-14", "since_days": 120, "event_type": "disclosure", "limit": 5, "keyword": None,
        "events": events, "summary": summarize(events), "disclaimer": DISCLAIMER,
    }


class FakeDb:
    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    """DB·Ollama 없이: 질문 속 종목은 '삼성전자' 하나, tool 결과는 고정, LLM 최종 답변은 state['llm']."""
    state = {"llm": "공시 이후 1거래일간 -4.56% 움직였습니다. 이 공시 때문에 주가가 떨어졌습니다.", "chat_calls": [], "tool_args": []}
    monkeypatch.setattr(agent, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(
        agent, "extract_from_text",
        lambda db, text: [SimpleNamespace(name=n) for n in ("삼성전자", "SK하이닉스") if n in (text or "")],
    )
    monkeypatch.setattr(agent, "_company_names", lambda: ["삼성전자", "SK하이닉스"])

    def tool(**kw):
        state["tool_args"].append(kw)
        return state.get("result", _result([_event()]))

    monkeypatch.setattr(agent, "event_reaction_tool", tool)

    def chat(**kw):
        state["chat_calls"].append(kw)
        if state.get("chat_error"):
            raise state["chat_error"]
        return {"message": {"role": "assistant", "content": state["llm"]}}

    monkeypatch.setattr(agent.ollama, "chat", chat)
    return state


class TestEventPath:
    def test_gate_question_runs_event_tool_without_tool_selection_call(self, env):
        resp, records = agent._answer("삼성전자 자기주식취득 공시 이후 주가 반응은?", "m")
        assert resp["used_tools"] == ["event_reaction_tool"]
        assert [r["tool_name"] for r in records] == ["event_reaction_tool"]
        assert env["tool_args"] == [{"company_name": "삼성전자", "event_type": "disclosure", "limit": 5, "keyword": "자기주식"}]
        # 1차 호출(tool 선택) 없이 최종 답변 한 번만, tool 목록 없이 호출한다
        assert len(env["chat_calls"]) == 1 and "tools" not in env["chat_calls"][0]
        last = env["chat_calls"][0]["messages"][-1]
        assert last["role"] == "system" and "인과" in last["content"]

    def test_answer_has_code_section_verified_interpretation_and_disclaimer(self, env):
        resp, records = agent._answer("삼성전자 공시 이후 주가 반응은?", "m")
        a = resp["answer"]
        assert a.startswith(SECTION_TITLE)
        assert "1거래일 -4.56%(코스피 -3.64%, 초과 -0.92%p)" in a
        assert "공시 이후 1거래일간 -4.56% 움직였습니다." in a
        assert "때문에" not in a.split("## 해석")[1].rsplit("\n\n※ ", 1)[0]
        assert a.endswith("※ " + DISCLAIMER)
        dropped = records[0]["result"]["dropped_sentences"]
        assert dropped == [{"sentence": "이 공시 때문에 주가가 떨어졌습니다.", "reason": "인과 단정"}]

    def test_sources_use_existing_types(self, env):
        env["result"] = _result([_event(), _event(kind="news", title="삼성전자 기사")])
        resp, _ = agent._answer("삼성전자 공시 이후 주가 반응은?", "m")
        assert [(s["tool"], s["type"]) for s in resp["sources"]] == [
            ("event_reaction_tool", "disclosure"), ("event_reaction_tool", "news")
        ]
        assert {s["type"] for s in resp["sources"]} <= {"news", "disclosure"}

    def test_llm_text_fully_dropped_still_answers(self, env):
        env["llm"] = "공시 때문에 떨어졌습니다. 앞으로도 오를 것입니다."
        resp, _ = agent._answer("삼성전자 공시 이후 주가 반응은?", "m")
        assert "## 해석" not in resp["answer"] and resp["answer"].startswith(SECTION_TITLE)
        assert resp["answer"].endswith(DISCLAIMER)

    def test_no_events_skips_llm(self, env):
        env["result"] = {"found": False, "company_name": "삼성전자", "message": "최근 120일 안에 삼성전자의 공시 사건이 없습니다. 대상 사건이 없습니다."}
        resp, _ = agent._answer("삼성전자 공시 이후 주가 반응은?", "m")
        assert env["chat_calls"] == []
        assert "대상 사건이 없습니다" in resp["answer"] and resp["answer"].endswith(DISCLAIMER)
        assert resp["sources"] == []

    def test_ollama_down_returns_code_section(self, env):
        env["chat_error"] = ConnectionError("down")
        resp, _ = agent._answer("삼성전자 공시 이후 주가 반응은?", "m")
        assert resp["answer"].startswith(SECTION_TITLE) and "## 해석" not in resp["answer"]

    def test_role_prefix_and_empty_sections_cleaned(self, env):
        env["llm"] = "assistant\n\n## 위험요인\n(이 섹션은 빈칸입니다.)\n\n공시 이후 1거래일간 -4.56% 움직였습니다."
        resp, _ = agent._answer("삼성전자 공시 이후 주가 반응은?", "m")
        body = resp["answer"].split("## 해석")[1]
        assert "빈칸" not in body and "assistant" not in body and "## 위험요인" not in body

    def test_follow_up_uses_previous_company(self, env):
        prev = {"report_id": 3, "question": "삼성전자 주가 알려줘", "answer": "..."}
        resp, _ = agent._answer("그 공시 이후 주가는 어땠어?", "m", prev)
        assert resp["used_tools"] == ["event_reaction_tool"]
        assert env["tool_args"][0]["company_name"] == "삼성전자"

    def test_leveraged_etf_notice(self, env):
        r = _result([_event(kind="news", title="KODEX 레버리지 기사")])
        r.update(company_name="KODEX 레버리지", asset_type="etf", market_index=None)
        env["result"] = r
        env["llm"] = ""
        resp, _ = agent._answer("삼성전자 뉴스 이후 주가 반응", "m")
        assert "복리 효과" in resp["answer"] and resp["answer"].endswith(DISCLAIMER)


class TestOtherQuestionsUntouched:
    def _normal_path(self, env, monkeypatch, question):
        """기존 경로가 그대로 쓰이는지: 1차 호출에 기존 build_request 결과가 그대로 들어간다."""
        monkeypatch.setattr(agent, "_answer_event", lambda *a, **k: pytest.fail("사건 경로를 타면 안 된다"))
        env["llm"] = "개념 설명입니다."
        resp, records = agent._answer(question, "m")
        first = env["chat_calls"][0]
        prompt, tools = agent.build_request(question)
        assert first["tools"] is tools and first["messages"][0]["content"] == prompt
        return resp

    @pytest.mark.parametrize(
        "q",
        ["삼성전자 최근 뉴스 알려줘", "삼성전자 공시 알려줘", "삼성전자 뉴스랑 주가 움직임 같이 알려줘", "PER이 뭐야?",
         "삼성전자 왜 올랐어?", "KODEX 200 최근 흐름 알려줘"],
    )
    def test_non_gate_questions_go_through_existing_path(self, env, monkeypatch, q):
        self._normal_path(env, monkeypatch, q)

    def test_gate_without_company_goes_through_existing_path(self, env, monkeypatch):
        self._normal_path(env, monkeypatch, "공시 이후 주가 반응은 어떻게 봐야 해?")

    def test_gate_with_two_companies_goes_through_existing_path(self, env, monkeypatch):
        self._normal_path(env, monkeypatch, "삼성전자랑 SK하이닉스 공시 이후 주가 반응")

    def test_non_gate_question_does_not_touch_db_for_event_subject(self, monkeypatch):
        monkeypatch.setattr(agent, "SessionLocal", lambda: pytest.fail("DB 접근 없음"))
        assert agent._event_subject("삼성전자 최근 뉴스 알려줘") is None


class TestResearchRoute:
    def test_post_research_records_tool_sources_and_disclaimer(self, env, monkeypatch, login_as):
        login_as(7)
        saved = {}

        def save(question, answer, records, previous_report_id=None, user_id=None):
            saved.update(question=question, tools=[r["tool_name"] for r in records], user_id=user_id)
            return 99

        monkeypatch.setattr(agent, "save_report_row", save)
        r = client.post("/api/research", json={"question": "삼성전자 공시 이후 주가 반응은?"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"answer", "used_tools", "sources", "report_id", "previous_report_id", "disclaimer"}
        assert body["used_tools"] == ["event_reaction_tool"] and body["report_id"] == 99
        assert body["sources"][0]["type"] == "disclosure" and body["sources"][0]["url"]
        assert ("※ " + DISCLAIMER) in body["answer"]
        assert saved["tools"] == ["event_reaction_tool"] and saved["user_id"] == 7


@pytest.mark.integration
class TestDevDb:
    """실제 dev DB: event_reaction_tool 결과가 GET /api/stocks/{ticker}/event-reactions 결과와 같다(같은 함수)."""

    @pytest.mark.parametrize("ticker, name", [("005930", "삼성전자"), ("030200", "KT"), ("073240", "금호타이어"), ("069500", "KODEX 200")])
    @pytest.mark.parametrize("event_type", ["all", "disclosure", "news"])
    def test_tool_equals_api(self, dev_db, ticker, name, event_type):
        from app.tools.event_reaction_tool import TOOL_SINCE_DAYS, event_reaction_tool

        api = client.get(f"/api/stocks/{ticker}/event-reactions?since_days={TOOL_SINCE_DAYS}&limit=5&event_type={event_type}").json()
        tool = event_reaction_tool(name, event_type, 5)
        assert tool["ticker"] == api["ticker"] and tool["market_index"] == api["market_index"]
        assert tool["price_as_of"] == api["price_as_of"]
        assert tool["events"] == api["events"]
        assert tool["summary"] == api["summary"]
        assert tool["found"] == bool(api["events"])
        if name == "KODEX 200":
            assert all(e["event_type"] == "news" for e in tool["events"])
            assert all(h["excess_pct"] is None for e in tool["events"] for h in e["returns"].values())

    def test_keyword_filters_titles(self, dev_db):
        from app.tools.event_reaction_tool import event_reaction_tool

        r = event_reaction_tool("삼성전자", "disclosure", 5, "자기주식")
        assert r["found"] and all("자기주식" in e["title"] for e in r["events"])

    def test_unknown_keyword_ignored_and_unknown_company(self, dev_db):
        from app.tools.event_reaction_tool import event_reaction_tool

        assert event_reaction_tool("삼성전자", "all", 3, "수주")["keyword"] is None
        nf = event_reaction_tool("없는회사이름", "all", 3)
        assert nf["found"] is False and nf["message"]

    def test_no_events_message(self, dev_db):
        from app.tools.event_reaction_tool import event_reaction_tool

        r = event_reaction_tool("KODEX 200", "disclosure", 5)
        assert r["found"] is False and "대상 사건이 없습니다" in r["message"]

    def test_full_answer_with_real_tool_and_fake_llm(self, dev_db, monkeypatch):
        monkeypatch.setattr(
            agent.ollama, "chat",
            lambda **kw: {"message": {"content": "공시 때문에 올랐습니다. 같은 기간 다른 사건도 있어 단정할 수 없습니다."}},
        )
        resp, records = agent._answer("삼성전자 공시 나온 뒤 주가 움직임 알려줘", "m")
        assert resp["used_tools"] == ["event_reaction_tool"]
        assert resp["answer"].startswith(SECTION_TITLE) and resp["answer"].endswith(DISCLAIMER)
        assert "때문에 올랐" not in resp["answer"]
        assert len(resp["sources"]) == len(records[0]["result"]["events"]) > 0
