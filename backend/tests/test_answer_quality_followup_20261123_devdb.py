"""2026-11-23 최종 답변 품질 보완: DB 통합 테스트(LLM은 가짜, 리포트는 저장하지 않는다 save_report=False).

CI(빈 pgvector DB + scripts/ci_seed 종목)에서 도는 것: 종목 교체 후속 질문 판정이 실제 종목 표로 직전 질문의 종목만 바꾸는지,
그 질문이 직전 대화 없이 처리되는지, 기사 범위 판정용 종목 목록.
수집 데이터가 필요한 것(needs_collected_data, 로컬 dev DB): 바꾼 질문으로 새 종목의 20거래일 시세를 다시 조회하는지, 원인 답의 직접 근거
목록에 업종·시장 기사가 섞이지 않는지, 영어 원인 질문의 번역 검증이 실패해도 한국어 답과 같은 등락률이 남는지.
"""
import re

import pytest

from app import agent, evidence_scope, i18n_translate, followup_subject

pytestmark = pytest.mark.integration

PREV_SAMSUNG_20 = {"report_id": 1, "question": "삼성전자 최근 20거래일 수익률 분석해 줘.",
                   "answer": "## 주가 동향\n삼성전자는 최근 20거래일 동안 -10.02%입니다. 최고 242,000원(2026-10-28).",
                   "company_name": "삼성전자"}


@pytest.fixture
def spy_answer(monkeypatch, dev_db):  # noqa: ARG001 - dev_db: DB가 없으면 skip
    """_answer에 실제로 넘어간 질문과 직전 대화를 남긴다(처리는 진짜 _answer). LLM은 도구 없이 짧게 답한다."""
    seen = []
    real = agent._answer

    def wrapped(question, model, previous=None):
        seen.append({"question": question, "previous": previous})
        return real(question, model, previous)

    monkeypatch.setattr(agent, "_answer", wrapped)
    monkeypatch.setattr(agent, "_first_call", lambda model, messages, tools=None: ({"role": "assistant", "content": "답"}, None))
    monkeypatch.setattr(agent, "_llm_chat", lambda **kw: {"message": {"role": "assistant", "content": "조회 결과를 정리했습니다."}})
    return seen


@pytest.mark.parametrize("question, rewritten", [
    ("SK하이닉스는?", "SK하이닉스 최근 20거래일 수익률 분석해 줘."),
    ("그럼 하이닉스는요?", "SK하이닉스 최근 20거래일 수익률 분석해 줘."),
    ("LG에너지솔루션은 어때?", "LG에너지솔루션 최근 20거래일 수익률 분석해 줘."),
])
def test_subject_only_followup_is_rewritten_without_previous(spy_answer, question, rewritten):
    """시나리오 4·5: 종목만 바꾼 후속 질문은 직전 분석 의도·기간을 잇고, 직전 대화(삼성전자 답)는 넘기지 않는다."""
    r = agent.ask_question(question, save_report=False, previous=PREV_SAMSUNG_20)
    assert spy_answer == [{"question": rewritten, "previous": None}]
    assert r["meta"]["subject_swap"]["from"] == "삼성전자"
    assert r["clarification_required"] is False
    assert all(i["name"] != "삼성전자" for i in r["resolved_instruments"])
    assert "-10.02%" not in r["answer"] and "242,000" not in r["answer"]


@pytest.mark.parametrize("question", ["SK하이닉스는 왜 떨어졌어?", "SK하이닉스 뉴스 알려줘", "삼성전자는?"])
def test_new_intent_or_same_company_keeps_previous_flow(spy_answer, question):
    agent.ask_question(question, save_report=False, previous=PREV_SAMSUNG_20)
    assert spy_answer[0]["question"] == question and spy_answer[0]["previous"] is not None


def test_english_subject_only_followup(spy_answer):
    prev = {**PREV_SAMSUNG_20, "question": "Analyze Samsung Electronics returns over the last 20 trading days."}
    r = agent.ask_question("What about SK hynix?", save_report=False, previous=prev, locale="en-US")
    assert spy_answer[0]["previous"] is None
    assert "SK하이닉스" in spy_answer[0]["question"] and "20거래일" in spy_answer[0]["question"]
    assert r["meta"]["subject_swap"]["to"] == "SK하이닉스"


def test_company_context_reads_registered_names(dev_db):  # noqa: ARG001
    aliases, others = evidence_scope.company_context("SK하이닉스")
    assert "하이닉스" in aliases and "삼성전자" in others and "SK하이닉스" not in others


def test_find_mentions_positions_match_extract(dev_db):
    from app.tools.company_resolver import extract_from_text, find_mentions, mention_text

    text = "그럼 하이닉스랑 삼성전자는?"
    hits = find_mentions(dev_db, text)
    base = mention_text(text)
    assert [base[a:b] for a, b, _ in hits] == ["하이닉스", "삼성전자"]
    assert [c.name for c in extract_from_text(dev_db, text)] == [c.name for _, _, c in hits]


# ---------- 수집 데이터가 필요한 것(로컬 dev DB) ----------

@pytest.mark.needs_collected_data
def test_rewritten_followup_recomputes_new_stock_20_days(monkeypatch, dev_db):  # noqa: ARG001
    """시나리오 4: 모델이 직전 종목(삼성전자)·다른 기간을 인자로 넣어도, 바꾼 질문 원문으로 종목과 20거래일이 정해져 SK하이닉스로 새로 조회한다."""
    calls = []
    real_run = agent._run_tool

    def run(name, args):
        out = real_run(name, args)
        calls.append((name, dict(args), out))
        return out

    tool_calls = [{"function": {"name": "stock_tool", "arguments": {"ticker": "삼성전자", "period_days": 5}}}]
    monkeypatch.setattr(agent, "_first_call",
                        lambda model, messages, tools=None: ({"role": "assistant", "content": "", "tool_calls": tool_calls}, tool_calls))
    monkeypatch.setattr(agent, "_llm_chat", lambda **kw: {"message": {"role": "assistant", "content": "SK하이닉스 20거래일 흐름입니다."}})
    monkeypatch.setattr(agent, "_run_tool", run)
    r = agent.ask_question("SK하이닉스는?", save_report=False, previous=PREV_SAMSUNG_20)
    assert r["meta"]["subject_swap"]["question"] == "SK하이닉스 최근 20거래일 수익률 분석해 줘."
    stock = [c for c in calls if c[0] == "stock_tool"]
    if not stock:  # 코드 경로(price_flow 등)가 답했다: 그 답은 SK하이닉스 기록뿐이어야 한다
        assert [i["name"] for i in r["resolved_instruments"]] == ["SK하이닉스"]
        return
    name, args, out = stock[0]
    assert args["ticker"] == "SK하이닉스" and args["period_days"] == 20
    assert out["company_name"] == "SK하이닉스" and out.get("trading_days") == 20
    assert all(c[2].get("company_name") != "삼성전자" for c in calls if isinstance(c[2], dict))
    assert [i["name"] for i in r["resolved_instruments"]] == ["SK하이닉스"]


def _section_titles(answer: str, head: str, stop: str | None) -> list[str]:
    part = answer.split(head, 1)[1] if head in answer else ""
    if stop and stop in part:
        part = part.split(stop, 1)[0]
    return re.findall(r"^\s+- (?:\[[^\]]+\] )?뉴스 \S* (?:\([^)]*\) )?\"(.+)\"$", part, re.M)


@pytest.mark.needs_collected_data
def test_cause_answer_separates_direct_and_reference_news(dev_db):  # noqa: ARG001
    """시나리오 1·2: 원인 답의 직접 근거 목록에는 기업 직접 기사만, 업종·시장 기사는 참고 목록에만 있다."""
    r = agent.ask_question("SK하이닉스 왜 떨어졌어?", save_report=False)
    answer = r["answer"]
    aliases, others = evidence_scope.company_context("SK하이닉스")
    if "직접 다룬 기사·공시입니다" in answer:
        direct = _section_titles(answer, "직접 다룬 기사·공시입니다", "업종·시장 참고")
        assert direct, answer
        for title in direct:
            stem = title.rstrip(".…")
            assert evidence_scope.classify("SK하이닉스", stem, None, aliases, others) in (evidence_scope.COMPANY, evidence_scope.UNCONFIRMED)
    else:
        assert "개별적인" in answer or "원인을 특정할 수 없습니다" in answer
    for line in re.findall(r"^\s+- \[(.+?)\] 뉴스", answer, re.M):
        assert line in evidence_scope.LABEL_KO.values()
    # 출처(sources)는 답에 나온 기사뿐이다(지어낸 제목 없음)
    for s in r["sources"]:
        assert s["title"][:20] in answer


@pytest.mark.needs_collected_data
def test_english_cause_fallback_keeps_same_numbers(monkeypatch, dev_db):  # noqa: ARG001
    """시나리오 6: 영어 원인 질문의 번역 검증이 실패해도 한국어 답과 같은 종목·기간·등락률이 남는다."""
    ko = agent.ask_question("삼성전자 최근 5거래일 동안 왜 떨어졌어?", save_report=False)
    cp = ko["meta"].get("cause_period")
    if not cp or cp.get("status") != "ok":
        pytest.skip("저장 시세로 기간 등락을 계산할 수 없음")
    monkeypatch.setattr(i18n_translate, "_chat", lambda messages: "번역하지 않고 한국어를 그대로 돌려준 출력")  # 검증 실패
    en = agent.ask_question("Why did Samsung Electronics fall over the last 5 trading days?", save_report=False, locale="en-US")
    assert en["meta"]["translation_status"] == "fallback_summary"
    assert en["meta"]["cause_period"]["pct"] == cp["pct"] and en["meta"]["cause_period"]["from"] == cp["from"]
    pct = f"{cp['pct']:+.2f}%"
    assert pct in ko["answer"] and pct in en["answer"]
    assert cp["from"] in en["answer"] and cp["end"] in en["answer"]
    if cp.get("index_pct") is not None:
        assert f"{cp['index_pct']:+.2f}%" in en["answer"]


def test_followup_module_is_wired():
    assert agent.followup_subject is followup_subject
