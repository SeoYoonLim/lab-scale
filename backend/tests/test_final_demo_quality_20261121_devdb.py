"""2026-11-21 최종 시연 전 품질 수정: 종목 식별 경로 통합 테스트(종목 시드만 있는 DB, LLM은 가짜).

CI backend-integration(빈 pgvector DB + scripts/ci_seed 종목 350개)에서 돈다. 시세·뉴스가 없어도 되는 것만 본다: 어떤 종목으로
도구를 부르는지(또는 부르지 않는지), 되묻기 상태, 분석 기준 종목 출처. 리포트는 저장하지 않는다(save_report=False).
실제 Ollama를 부르는 비교는 여기 없다(live 확인은 docs의 최종 품질 보고서).
"""
import pytest

from app import agent, instrument_scope

pytestmark = pytest.mark.integration

PREV_SAMSUNG = {"report_id": 1, "question": "삼성전자 최근 5거래일 분석해 줘.", "answer": "삼성전자는 최근 5거래일 동안 하락했습니다.",
                "company_name": "삼성전자"}


def _call(name, **args):
    return {"function": {"name": name, "arguments": args}}


@pytest.fixture
def fake_llm(monkeypatch, dev_db):  # noqa: ARG001 - dev_db: DB가 없으면 skip(CI에서는 실패)
    """1차 호출(도구 선택)은 calls에 넣은 호출을 돌려주고, 최종 답 호출은 고정 문장을 돌려준다. 부른 횟수를 센다."""
    state = {"calls": [], "first": 0, "final": 0}

    def first_call(model, messages, tools=None):
        state["first"] += 1
        calls = [dict(c, function=dict(c["function"], arguments=dict(c["function"]["arguments"]))) for c in state["calls"]]
        return ({"role": "assistant", "content": "", "tool_calls": calls}, calls) if calls else ({"role": "assistant", "content": "답"}, None)

    def chat(**kwargs):
        state["final"] += 1
        return {"message": {"role": "assistant", "content": "조회 결과를 정리했습니다."}}

    monkeypatch.setattr(agent, "_first_call", first_call)
    monkeypatch.setattr(agent, "_llm_chat", chat)
    return state


def ask(question, previous=None, locale=None):
    return agent.ask_question(question, save_report=False, previous=previous, locale=locale)


@pytest.mark.parametrize("question", ["오늘 주가 어때?", "최근 주가 어때?", "How is the stock doing today?", "뉴스 좀 알려줘"])
def test_no_subject_question_asks_without_llm(fake_llm, question):
    fake_llm["calls"] = [_call("stock_tool", ticker="삼성전자", period_days=1)]  # 모델이 고를 종목(실행되면 실패)
    r = ask(question, locale="en-US" if question.startswith("How") else None)
    assert fake_llm["first"] == 0 and r["used_tools"] == []
    assert r["clarification_required"] is True and r["resolved_instruments"] == []
    assert r["clarification_reason"] == "missing_instrument"
    if question.startswith("How"):
        assert r["answer"].startswith("Which stock do you mean?") and r["meta"]["translation_status"] == "localized"
    else:
        assert "삼성전자" not in r["answer"] or r["answer"] == instrument_scope.CLARIFY_ANSWER


def test_model_guess_is_dropped_and_asks(fake_llm):
    """일반어가 아닌 낱말이 있어 LLM 경로로 가도, 모델이 질문과 무관한 종목을 채우면 실행하지 않고 되묻는다."""
    fake_llm["calls"] = [_call("stock_tool", ticker="삼성전자", period_days=5), _call("news_tool", company_name="삼성전자", limit=5)]
    r = ask("요즘 분위기 어때?")
    assert fake_llm["first"] == 1 and fake_llm["final"] == 0 and r["used_tools"] == []
    assert r["answer"] == instrument_scope.CLARIFY_ANSWER and r["clarification_required"] is True
    assert r["meta"]["guessed_instruments"] == ["삼성전자"]


def test_typo_from_question_is_still_resolved(fake_llm):
    """질문에 흔적이 있는 종목(오타)은 추측이 아니다: 그대로 조회하고 분석 기준은 explicit이다."""
    fake_llm["calls"] = [_call("stock_tool", ticker="삼성잔자", period_days=5)]
    r = ask("삼성잔자 주가 흐름 좀 봐줘")
    assert r["used_tools"] == ["stock_tool"] and r["clarification_required"] is False
    assert r["resolved_instruments"] == [{"ticker": "005930", "name": "삼성전자", "source": "explicit", "name_en": "Samsung Electronics"}]


@pytest.mark.parametrize("question, locale", [("엔비디아는 왜 하락했어?", None), ("Why did NVIDIA fall this week?", "en-US"),
                                              ("엔비디아 전망 어때?", None)])
def test_unsupported_company_never_falls_back_to_previous(fake_llm, question, locale):
    fake_llm["calls"] = [_call("stock_tool", ticker="삼성전자", period_days=5)]
    r = ask(question, previous=PREV_SAMSUNG, locale=locale)
    assert fake_llm["first"] == 0 and r["used_tools"] == [] and r["resolved_instruments"] == []
    assert r["clarification_required"] is True and r["clarification_reason"] == "unsupported_instrument"
    if locale:
        assert r["answer"].startswith("'NVIDIA' is not covered by this service.")
        assert r["meta"]["translation_status"] == "localized"
    else:
        assert r["answer"].startswith("'엔비디아(NVIDIA)'는 이 서비스의 분석 대상이 아닙니다.")


def test_unknown_domestic_name_in_followup_is_not_replaced(fake_llm):
    fake_llm["calls"] = [_call("stock_tool", ticker="삼성전자", period_days=5)]
    r = ask("롯데칠성은 왜 떨어졌어?", previous=PREV_SAMSUNG)
    assert fake_llm["first"] == 0 and r["used_tools"] == [] and r["clarification_reason"] == "unresolved_instrument"
    assert r["answer"].startswith("'롯데칠성'은 등록된 종목에서 찾지 못했습니다. 직전 대화의 종목으로 대신 분석하지 않았습니다.")


def test_model_copied_unknown_name_is_not_replaced_by_previous(fake_llm):
    """첫 낱말 규칙에 안 걸리는 모양("롯데칠성 뉴스 알려줘")이어도, 모델이 질문 속 이름을 그대로 넘겼는데 등록 종목이 아니면 직전 종목으로 바꾸지 않는다."""
    fake_llm["calls"] = [_call("news_tool", company_name="롯데칠성", limit=5)]
    r = ask("롯데칠성 뉴스 알려줘", previous=PREV_SAMSUNG)
    assert r["used_tools"] == [] and r["clarification_reason"] == "unresolved_instrument" and "롯데칠성" in r["answer"]


def test_followup_without_subject_uses_previous_and_says_so(fake_llm):
    fake_llm["calls"] = [_call("news_tool", company_name="삼성전자", limit=5)]
    r = ask("그럼 뉴스는?", previous=PREV_SAMSUNG)
    assert r["used_tools"] == ["news_tool"] and r["clarification_required"] is False
    assert r["resolved_instruments"][0]["name"] == "삼성전자" and r["resolved_instruments"][0]["source"] == "previous_context"


def test_followup_topic_word_keeps_previous_context(fake_llm):
    fake_llm["calls"] = [_call("news_tool", company_name="삼성전자", limit=5)]
    r = ask("외국인은 왜 팔았어?", previous=PREV_SAMSUNG)
    assert r["clarification_required"] is False and r["resolved_instruments"][0]["source"] == "previous_context"


def test_explicit_new_company_wins_over_previous(fake_llm):
    fake_llm["calls"] = [_call("news_tool", company_name="SK하이닉스", limit=5)]
    r = ask("SK하이닉스 뉴스 알려줘", previous=PREV_SAMSUNG)
    assert [i["name"] for i in r["resolved_instruments"]] == ["SK하이닉스"]
    assert r["resolved_instruments"][0]["source"] == "explicit"


def test_comparison_keeps_both_instruments(fake_llm):
    fake_llm["calls"] = [_call("stock_tool", ticker="삼성전자", period_days=20), _call("stock_tool", ticker="SK하이닉스", period_days=20)]
    r = ask("Compare Samsung Electronics and SK Hynix.", locale="en-US")
    assert [i["ticker"] for i in r["resolved_instruments"]] == ["005930", "000660"]
    assert all(i["source"] == "explicit" for i in r["resolved_instruments"])


def test_english_then_korean_followup_keeps_subject(fake_llm):
    """영어로 물은 직전 리포트(질문 원문 영어) 뒤 한국어 후속 질문도 직전 종목을 이어받는다."""
    prev = {"report_id": 2, "question": "Analyze Samsung Electronics over the last five trading days.", "answer": "삼성전자는 하락했습니다.",
            "company_name": "삼성전자"}
    fake_llm["calls"] = [_call("news_tool", company_name="삼성전자", limit=5)]
    r = ask("그럼 뉴스는?", previous=prev)
    assert r["resolved_instruments"][0]["name"] == "삼성전자" and r["resolved_instruments"][0]["source"] == "previous_context"


def test_korean_then_english_followup_keeps_subject(fake_llm):
    fake_llm["calls"] = [_call("news_tool", company_name="삼성전자", limit=5)]
    r = ask("What about the news?", previous=PREV_SAMSUNG, locale="en-US")
    assert r["resolved_instruments"] and r["resolved_instruments"][0]["source"] == "previous_context"
    assert r["clarification_required"] is False


@pytest.mark.parametrize("question", ["포트폴리오는 어때?", "배당은 어때?", "실적은 어땠어?", "경쟁사는 어때?"])
def test_followup_topics_are_not_treated_as_unknown_names(fake_llm, question):
    """주제어로 시작하는 후속 질문은 "종목을 찾지 못했습니다"로 가로채지 않는다(브리핑·직전 종목 흐름 그대로)."""
    fake_llm["calls"] = [_call("news_tool", company_name="삼성전자", limit=5)]
    r = ask(question, previous=PREV_SAMSUNG)
    assert r["clarification_reason"] != "unresolved_instrument"


def test_topic_word_copied_into_tool_arg_keeps_previous_context(fake_llm):
    """모델이 주제어("포트폴리오")를 종목 인자로 옮겨도 "종목을 찾지 못했습니다"로 끊지 않는다(실데이터 사본 확인에서 나온 경우)."""
    fake_llm["calls"] = [_call("news_tool", company_name="포트폴리오", limit=5)]
    r = ask("포트폴리오는 어때?", previous=PREV_SAMSUNG)
    assert r["clarification_reason"] != "unresolved_instrument"
