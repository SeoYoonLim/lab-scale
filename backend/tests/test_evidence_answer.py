"""근거 우선 답변(app/evidence_answer.py, agent._answer_evidence). DB·Ollama 없이 돈다."""

import pytest

from app import agent, evidence_answer as E

NEWS = {"tool_name": "news_tool", "result": {"found": True, "company_name": "한미반도체", "news": [
    {"company_name": "한미반도체", "url": "https://b.example/1", "title": "시황 기사", "content": "한미반도체 등 반도체주가 약세였다.", "published_at": "2026-10-30T01:00:00+00:00", "source": "b.example"},
    {"company_name": "한미반도체", "url": "https://a.example/1", "title": "한미반도체 주가 공매도 집중", "content": "공매도 비중이 38.11%에 달했다.", "published_at": "2026-10-31T01:00:00+00:00", "source": "a.example"},
    {"company_name": "한미반도체", "url": "https://c.example/1", "title": "한미반도체 주가 공매도 집중", "content": "같은 제목", "published_at": "2026-10-31T01:00:00+00:00", "source": "c.example"},
]}}
ITEMS = E.collect_items([NEWS], ["한미반도체"])


def test_collect_items_title_first_and_no_duplicates():
    assert [it["title"] for it in ITEMS] == ["한미반도체 주가 공매도 집중", "시황 기사"]
    assert ITEMS[0]["date"] == "2026-10-31" and ITEMS[0]["source"] == "a.example"


def test_collect_items_balances_two_companies():
    def rec(name, n):
        return {"tool_name": "news_tool", "result": {"found": True, "company_name": name, "news": [{"title": f"{name} {i}", "content": ""} for i in range(n)]}}

    items = E.collect_items([rec("KB금융", 6), rec("신한지주", 6)], ["KB금융", "신한지주"])
    assert [it["company"] for it in items] == ["KB금융"] * 3 + ["신한지주"] * 3


def test_not_found_results_give_no_items():
    assert E.collect_items([{"tool_name": "news_tool", "result": {"found": False, "company_name": "SK"}}], ["SK"]) == []


def test_item_lines_are_written_by_code():
    assert E.item_lines(ITEMS)[0] == "[1] 한미반도체 주가 공매도 집중 (2026-10-31, a.example)"


@pytest.mark.parametrize("sentence", [
    "기사에 따르면 한미반도체 공매도가 집중됐다고 보도됐습니다 [1].",
    "기사에 따르면 공매도 비중이 38.11%에 달했다고 보도됐습니다 [1].",
    "한미반도체 등 반도체주가 약세였습니다 [2].",
])
def test_grounded_sentence_is_kept(sentence):
    assert E.check_sentences(sentence, ITEMS, ["한미반도체"]) == ([sentence], [])


@pytest.mark.parametrize("sentence, reason", [
    ("한미반도체는 공매도가 집중됐습니다.", "no_citation"),
    ("공매도가 집중됐습니다 [9].", "no_citation"),
    ("공매도 비중이 45.00%에 달했습니다 [1].", "number"),
    ("공매도 비중이 38.11%에 달했습니다 [2].", "number"),  # 다른 근거의 숫자
    ("공매도 때문에 한미반도체가 떨어졌습니다 [1].", "causal"),
    ("반도체 수요 감소와 글로벌 경기 둔화가 투자 심리를 악화시켰습니다 [1].", "overlap"),
    ("공매도 비중이 시장 상황을 반영한 것으로 보입니다 [1].", "speculation"),
])
def test_ungrounded_sentence_is_dropped(sentence, reason):
    kept, dropped = E.check_sentences(sentence, ITEMS, ["한미반도체"])
    assert kept == [] and dropped[0].startswith(reason)


def test_citation_after_punctuation_stays_with_its_sentence():
    kept, dropped = E.check_sentences("한미반도체 공매도가 집중됐습니다. [1] 반도체주가 약세였습니다.[2]", ITEMS, ["한미반도체"])
    assert kept == ["한미반도체 공매도가 집중됐습니다 [1].", "반도체주가 약세였습니다[2]."] and dropped == []


def test_uncited_sentence_matching_one_source_gets_its_number():
    items = [{"company": "한미반도체", "title": "한미반도체 3분기 영업이익 증가", "content": "해외 고객사 장비 수주 확대로 영업이익이 크게 늘었다.", "date": "", "source": "", "kind": "news"}]
    kept, dropped = E.check_sentences("한미반도체는 해외 고객사 장비 수주가 크게 늘었습니다.", items, ["한미반도체"])
    assert kept == ["한미반도체는 해외 고객사 장비 수주가 크게 늘었습니다 [1]."] and dropped == []


def test_uncited_sentence_not_matching_is_still_dropped():
    assert E.check_sentences("이 회사는 전기차 배터리와 해외 시장 경쟁력이 뛰어납니다.", ITEMS, ["한미반도체"])[0] == []


@pytest.mark.parametrize("sentence", ["한미반도체 공매도가 집중되면서 떨어졌습니다 [1].", "이에 따라 한미반도체 공매도가 집중됐습니다 [1]."])
def test_causal_without_attribution_is_dropped(sentence):
    assert E.check_sentences(sentence, ITEMS, ["한미반도체"])[1][0] == "causal_without_attribution"


def test_markdown_and_list_marks_are_removed():
    kept, _ = E.check_sentences("1. **한미반도체 공매도가 집중됐습니다** [1].", ITEMS, ["한미반도체"])
    assert kept == ["한미반도체 공매도가 집중됐습니다 [1]."]


def test_repeated_citations_do_not_leave_commas():
    kept, _ = E.check_sentences("한미반도체 공매도가 집중됐습니다 [1]., [2]", ITEMS, ["한미반도체"])
    assert kept == ["한미반도체 공매도가 집중됐습니다 [1] [2]"]


def test_stray_citations_leave_nothing():
    assert E.check_sentences("[1] [2]", ITEMS, ["한미반도체"]) == ([], [])


def test_sentence_limit():
    text = " ".join(["한미반도체 공매도가 집중됐습니다 [1]."] * 6)
    assert len(E.check_sentences(text, ITEMS, ["한미반도체"])[0]) == E.MAX_SENTENCES


@pytest.mark.parametrize("q, pct, expected", [
    ("삼성전자 왜 올랐어?", -18.0, "하락"), ("펄어비스 왜 주가가 빠졌어?", 8.2, "상승"),
    ("삼성전자 왜 올랐어?", 3.0, None), ("삼성전자 최근 이슈 뭐야?", -3.0, None), ("왜 올랐어?", None, None),
])
def test_premise_note(q, pct, expected):
    note = E.premise_note(q, pct)
    assert (note is None) if expected is None else (expected in note)


@pytest.mark.parametrize("q, tools", [
    ("카카오 왜 떨어졌어?", ["stock_tool", "market_tool", "news_tool"]),
    ("카카오 최근 소식 알려줘", ["stock_tool", "news_tool"]),
    ("카카오 최근 공시 뭐 있어?", ["stock_tool", "news_tool", "disclosure_tool"]),
    ("카카오 사업 위험이 뭐야?", ["stock_tool", "news_tool", "rag_search_tool"]),
])
def test_wanted_tools(q, tools):
    from app.routing import is_cause_question

    assert E.wanted_tools(q, is_cause_question(q)) == tools


@pytest.mark.parametrize("q, yes", [("카카오 요즘 분위기 어때?", True), ("카카오 최근 이슈 뭐야?", True), ("카카오 주가 얼마야?", False), ("KODEX 200 구성종목 알려줘", False), ("카카오 종가 알려줘", False)])
def test_is_narrative_question(q, yes):
    assert E.is_narrative_question(q) is yes


def test_compose_without_material_says_so():
    text = E.compose(["SK의 종가는 494,500원입니다."], [], [], True)
    assert E.NO_MATERIAL_TEXT in text and "근거 요약" not in text


def test_compose_with_no_sentences_keeps_list_without_cause_note():
    text = E.compose([], ITEMS, [], True)
    assert "[1] 한미반도체" in text and E.NO_SUMMARY_TEXT in text and E.CAUSE_NOTE not in text


def test_compose_with_sentences_adds_cause_note_only_for_cause():
    assert E.CAUSE_NOTE in E.compose([], ITEMS, ["문장 [1]."], True)
    assert E.CAUSE_NOTE not in E.compose([], ITEMS, ["문장 [1]."], False)


# ---------- agent 연결(도구와 LLM은 가짜) ----------


class _Company:
    def __init__(self, name, asset_type="stock"):
        self.name, self.asset_type = name, asset_type


STOCK = {"found": True, "company_name": "한미반도체", "summary": "한미반도체의 2026-10-30 종가는 181,300원입니다.", "period_change_pct": -29.73,
         "prices": [{"price_date": "2026-10-30", "close_price": 181300.0, "change_pct": -6.83, "volume": 1000}]}


@pytest.fixture
def tools(monkeypatch):
    def fake(name, args):
        return {"stock_tool": STOCK, "news_tool": NEWS["result"]}.get(name, {"found": False})

    monkeypatch.setattr(agent, "_run_tool", fake)
    monkeypatch.setattr(agent, "_market_comparison_available", lambda company: False)
    monkeypatch.setattr(agent, "save_report_row", lambda *a, **k: None)
    agent._reset_llm_state()


def test_llm_is_called_once_with_only_the_evidence(tools, monkeypatch):
    calls = []

    def fake_llm(**kw):
        calls.append(kw)
        return {"message": {"content": "기사에 따르면 한미반도체 공매도가 집중됐다고 보도됐습니다 [1]. 반도체 수요 급감이 원인입니다."}, "done_reason": "stop"}

    monkeypatch.setattr(agent, "_llm_chat", fake_llm)
    response, records = agent._answer_evidence("한미반도체 왜 떨어졌어?", [_Company("한미반도체")], "m")
    assert len(calls) == 1 and "tools" not in calls[0] and "공매도 비중" in calls[0]["messages"][1]["content"]
    answer = response["answer"]
    assert "181,300원" in answer and "[1] 한미반도체 주가 공매도 집중 (2026-10-31, a.example)" in answer
    assert "보도됐습니다 [1]." in answer and "수요 급감" not in answer
    assert agent._meta()["answer_guard"]["evidence_dropped"] == ["no_citation"]
    assert [r["tool_name"] for r in records] == ["stock_tool", "news_tool"]


def test_no_news_does_not_call_llm(monkeypatch):
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: STOCK if name == "stock_tool" else {"found": False})
    monkeypatch.setattr(agent, "_market_comparison_available", lambda company: False)
    monkeypatch.setattr(agent, "_llm_chat", lambda **k: pytest.fail("근거가 없으면 LLM을 부르지 않는다"))
    agent._reset_llm_state()
    response, _ = agent._answer_evidence("SK 왜 떨어졌어?", [_Company("SK")], "m")
    assert E.NO_MATERIAL_TEXT in response["answer"] and "181,300원" in response["answer"]


def test_llm_failure_keeps_code_part(tools, monkeypatch):
    def fail(**kw):
        raise agent.LLMUnavailable("timeout")

    monkeypatch.setattr(agent, "_llm_chat", fail)
    response, _ = agent._answer_evidence("한미반도체 왜 떨어졌어?", [_Company("한미반도체")], "m")
    assert "181,300원" in response["answer"] and E.NO_SUMMARY_TEXT in response["answer"] and agent._meta()["fallback"] == "timeout"


@pytest.mark.parametrize("q, companies", [
    ("KODEX 200 왜 떨어졌어?", [_Company("KODEX 200", "etf")]),
    ("한미반도체 2025년 3월에 왜 떨어졌어?", [_Company("한미반도체")]),
    ("한미반도체 주가 얼마야?", [_Company("한미반도체")]),
    ("왜 떨어졌어?", []),
    ("셋 다 왜 떨어졌어?", [_Company("가"), _Company("나"), _Company("다")]),
])
def test_other_questions_stay_on_the_existing_path(q, companies, monkeypatch):
    monkeypatch.setattr(agent, "_run_tool", lambda name, args: pytest.fail("조회하면 안 된다"))
    assert agent._answer_evidence(q, companies, "m") is None
