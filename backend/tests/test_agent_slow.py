"""Ollama(llama3.1:8b)를 실제로 호출하는 에이전트 테스트. 느리고 비결정적이라 기본 실행에서 빠진다.

실행: pytest -m slow
단발 실행이라 성공률 측정이 아니라 "완전히 망가지지 않았는지"만 본다. 성공률 비교는
scripts/benchmark_models.py로 한다.
"""

import pytest

from app.agent import ask_question

pytestmark = [pytest.mark.slow, pytest.mark.integration]

FEWSHOT_LEAKS = ["롯데칠성", "종목을 찾지 못했습니다"]


def test_concept_question_answers_in_korean():
    r = ask_question("공매도가 뭔지 짧게 설명해줘.", save_report=False)
    assert r["answer"].strip()
    assert r["report_id"] is None


def test_stock_question_calls_stock_tool_without_fewshot_leak():
    r = ask_question("삼성전자 최근 3일 등락률 알려줘.", save_report=False)
    assert "stock_tool" in r["used_tools"]
    assert not any(s in r["answer"] for s in FEWSHOT_LEAKS)


def test_multi_question_leaves_no_fewshot_leak():
    r = ask_question("KCC 오늘 주가랑 관련 뉴스 같이 확인해줘.", save_report=False)
    assert r["used_tools"]
    assert not any(s in r["answer"] for s in FEWSHOT_LEAKS)


# ---- 후속 질문(previous_report_id): 이번 질문에 종목명이 없어도 직전 보고서의 종목을 이어받는지 ----


# 삼성전자는 일부러 뺐다: 모델이 회사명 없는 질문에 "삼성전자"를 기본값처럼 채워서, 삼성전자로는 종목 유지 여부를 검증할 수 없다
# (삼성전자만으로 검증했을 때 24/24였던 후속 질문이 다른 종목에서는 20~60%만 맞았다). 네이버는 별칭(네이버 -> NAVER) 경로도 같이 본다.
@pytest.fixture(scope="module", params=[("카카오 최근 주가 어때?", "카카오"), ("네이버 최근 주가 어때?", "NAVER")], ids=["카카오", "네이버"])
def base_report(request):
    """직전 보고서를 실제 API로 만들고, 테스트가 만든 보고서를 끝나고 모두 지운다."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.reports import delete_report

    question, company = request.param
    client = TestClient(app)
    created = []
    r = client.post("/api/research", json={"question": question})
    assert r.status_code == 200 and r.json()["report_id"] is not None
    base_id = r.json()["report_id"]
    created.append(base_id)
    try:
        yield {"client": client, "id": base_id, "created": created, "company": company}
    finally:
        for report_id in created:
            delete_report(report_id)


def _tool_company(report_id: int, tool: str):
    """그 보고서에서 실제로 실행된 tool 결과의 종목명(없으면 None)."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT result->>'company_name' FROM tool_call_log WHERE report_id = :i AND tool_name = :t"),
            {"i": report_id, "t": tool},
        ).all()
        return [r[0] for r in rows]
    finally:
        db.close()


@pytest.mark.parametrize(
    "follow_up, tool",
    [
        ("그럼 최근 뉴스는?", "news_tool"),
        ("그럼 최근 공시는 뭐 올라왔어?", "disclosure_tool"),
        ("그건 코스피랑 비교하면 어때?", "market_tool"),
    ],
)
def test_follow_up_without_company_name_keeps_previous_company(base_report, follow_up, tool):
    client = base_report["client"]
    r = client.post("/api/research", json={"question": follow_up, "previous_report_id": base_report["id"]})
    assert r.status_code == 200
    body = r.json()
    if body["report_id"] is not None:
        base_report["created"].append(body["report_id"])

    companies = _tool_company(body["report_id"], tool)
    detail = f"used_tools={body['used_tools']} {tool} 종목={companies} 답변={body['answer'][:120]!r}"
    assert body["previous_report_id"] == base_report["id"]
    assert tool in body["used_tools"], detail
    assert companies and set(companies) == {base_report["company"]}, detail
    assert client.get(f"/api/research/{body['report_id']}").json()["previous_report_id"] == base_report["id"]
