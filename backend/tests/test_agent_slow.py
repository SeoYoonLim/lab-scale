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


def test_recognized_period_expression_overrides_the_llm_guess_in_a_real_call():
    """"지난달"처럼 app.period_parser가 인식하는 기간 표현이 있으면, 모델이 뭘 추론했든 상관없이
    실제 호출에서도 stock_tool의 period_days가 파서 값으로 덮어써지는지 확인한다."""
    from app.agent import _answer
    from app.period_parser import parse_period

    question = "삼성전자 지난달 등락률이랑 거래량 알려줘."
    expected = parse_period(question).period_days

    _, records = _answer(question, "llama3.1:8b")
    stock_calls = [r for r in records if r["tool_name"] == "stock_tool"]
    assert stock_calls, f"stock_tool이 호출되지 않음: used_tools={[r['tool_name'] for r in records]}"
    assert stock_calls[0]["arguments"]["period_days"] == expected


def test_fx_question_calls_fx_tool_without_fewshot_leak():
    r = ask_question("요즘 환율 어때?", save_report=False)
    assert "fx_tool" in r["used_tools"]
    assert not any(s in r["answer"] for s in FEWSHOT_LEAKS)


def test_discovery_question_calls_discovery_tool_without_fewshot_leak():
    r = ask_question("요즘 급등하는 종목 뭐 있어?", save_report=False)
    assert "discovery_tool" in r["used_tools"]
    assert not any(s in r["answer"] for s in FEWSHOT_LEAKS)


# ---- FR-11 답변 구조화: tool 결과에 맞는 섹션만 마크다운 소제목(##)으로 나오는지 ----


def test_comprehensive_question_produces_markdown_sections():
    """여러 tool이 필요한 "종합적으로" 질문에서 실제로 마크다운 섹션(##)이 나오는지 확인한다.

    멀티 tool 호출 자체는 비결정적이라(README "multi" 그룹, 측정마다 56~78%대) 어떤 tool 조합이 불렸는지는
    따지지 않고, 최소 2개 이상의 tool을 함께 쓴 질문에서 구조화된 답변이 나오는지만 본다."""
    r = ask_question(
        "삼성전자 오늘 왜 올랐어? 주가 동향이랑 관련 뉴스도 같이 알려주고, 코스피 대비 흐름도 비교해서 종합적으로 알려줘.",
        save_report=False,
    )
    detail = f"used_tools={r['used_tools']} 답변={r['answer'][:300]!r}"
    assert len(r["used_tools"]) >= 2, detail
    assert "##" in r["answer"], detail


def test_simple_quantity_question_is_not_forced_into_unrelated_sections():
    """단순 조회 질문은 호출하지 않은 tool에 대응하는 섹션(시장 상황/위험요인 등)을 지어내지 않아야 한다."""
    r = ask_question("삼성전자 오늘 거래량 얼마야?", save_report=False)
    detail = f"used_tools={r['used_tools']} 답변={r['answer'][:300]!r}"
    assert "stock_tool" in r["used_tools"], detail
    assert "market_tool" not in r["used_tools"], detail
    assert "## 시장 상황" not in r["answer"], detail
    assert "## 위험요인" not in r["answer"], detail
    assert "## 뉴스" not in r["answer"], detail


# ---- 후속 질문(previous_report_id): 이번 질문에 종목명이 없어도 직전 보고서의 종목을 이어받는지 ----


# 삼성전자는 일부러 뺐다: 모델이 회사명 없는 질문에 "삼성전자"를 기본값처럼 채워서, 삼성전자로는 종목 유지 여부를 검증할 수 없다
# (삼성전자만으로 검증했을 때 24/24였던 후속 질문이 다른 종목에서는 20~60%만 맞았다). 네이버는 별칭(네이버 -> NAVER) 경로도 같이 본다.
@pytest.fixture(scope="module", params=[("카카오 최근 주가 어때?", "카카오"), ("네이버 최근 주가 어때?", "NAVER")], ids=["카카오", "네이버"])
def base_report(request):
    """직전 보고서를 실제 API로 만들고, 테스트가 만든 보고서를 끝나고 모두 지운다.

    리서치는 로그인이 필요해서 dev DB에 임시 사용자를 만들고 get_current_user를 그 사용자로 고정한다
    (module 스코프라 테스트마다 바뀌는 JWT_SECRET과 엮이지 않게 토큰 대신 override를 쓴다).
    끝나면 사용자를 지우고, 그 사용자의 리포트는 FK ON DELETE CASCADE로 함께 지워진다."""
    import secrets

    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.auth import create_user
    from app.db.session import SessionLocal
    from app.main import app
    from app.models import User
    from app.reports import delete_report

    question, company = request.param
    db = SessionLocal()
    try:
        user = create_user(db, f"pt_{secrets.token_hex(6)}", secrets.token_urlsafe(12))
    finally:
        db.close()
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    created = []
    r = client.post("/api/research", json={"question": question})
    assert r.status_code == 200 and r.json()["report_id"] is not None
    base_id = r.json()["report_id"]
    created.append(base_id)
    try:
        yield {"client": client, "id": base_id, "created": created, "company": company}
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        for report_id in created:
            delete_report(report_id, user_id=user.id)
        db = SessionLocal()
        try:
            db.query(User).filter(User.id == user.id).delete()
            db.commit()
        finally:
            db.close()


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
