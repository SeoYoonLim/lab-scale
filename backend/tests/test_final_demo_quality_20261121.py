"""2026-11-21 최종 시연 전 품질 수정(단위, DB·Ollama 없음).

1. 종목 식별: 해외 미지원 종목 감지, 일반어만 있는 질문, 분석 기준 종목 출처(app/instrument_scope)
2. 영어 질문: 원인 의도 정규화(app/intent_i18n), 같은 뜻의 한국어 질문과 종목·기간이 같은지
3. 답변 품질: 최고·최저·현재가 검증(app/answer_extremes), 내부 문구 필터·출처 없는 원인 절(app/answer_tidy), LLM에 넘기는 도구 결과 정리
4. 오류 코드: 주문·인증 오류 응답의 code(app/api_errors, app/main)
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import answer_tidy, instrument_scope, intent_i18n
from app.answer_extremes import fix_extremes, period_extremes
from app.answer_guard import guard_answer
from app.period_parser import parse_period


# ---------- 1. 종목 식별 ----------

@pytest.mark.parametrize("question, expected", [
    ("엔비디아는 왜 떨어졌어?", ["엔비디아(NVIDIA)"]),
    ("Why did NVIDIA fall this week?", ["엔비디아(NVIDIA)"]),
    ("Why did Nvidia drop?", ["엔비디아(NVIDIA)"]),
    ("테슬라랑 애플 비교해줘", ["테슬라(Tesla)", "애플(Apple)"]),
    ("エヌビディアはなぜ下がった?", ["엔비디아(NVIDIA)"]),
    ("英伟达为什么跌", ["엔비디아(NVIDIA)"]),
    ("메타버스 관련주 알려줘", []),  # 낱말 일부는 아니다
    ("인텔리안테크 주가", []),
    ("삼성전자 주가 알려줘", []),
    ("mu 값이 뭐야", []),  # 대문자 약칭만 본다
])
def test_foreign_unsupported_names(question, expected):
    assert instrument_scope.foreign_unsupported_names(question) == expected


def test_unsupported_answer_says_no_substitute():
    text = instrument_scope.unsupported_answer(["엔비디아(NVIDIA)"])
    assert text.startswith("'엔비디아(NVIDIA)'는 이 서비스의 분석 대상이 아닙니다.")
    assert "다른 종목으로 대신 분석하지 않았습니다" in text


@pytest.mark.parametrize("question, generic", [
    ("오늘 주가 어때?", True), ("최근 주가 어때?", True), ("전망을 분석해 줘.", True), ("오늘 왜 떨어졌어?", True),
    ("How is the stock doing 오늘 ?", True), ("이 종목 Why did it fall? (왜 떨어졌어)", True), ("뉴스 좀 알려줘", True),
    ("삼성잔자 주가 알려줘", False), ("반도체 관련주 추천해줘", False), ("How is the market doing 오늘 ?", False),
    ("요즘 분위기 어때?", False), ("", False),
])
def test_is_generic_only(question, generic):
    assert instrument_scope.is_generic_only(question) is generic


def test_build_resolved_sources():
    rows = [("삼성전자", "005930"), ("SK하이닉스", "000660")]
    assert instrument_scope.build_resolved(rows, {"SK하이닉스"}, has_previous=True) == [
        {"ticker": "005930", "name": "삼성전자", "source": "previous_context"},
        {"ticker": "000660", "name": "SK하이닉스", "source": "explicit"},
    ]
    # 직전 대화가 없으면 질문에 그대로 없어도(오타 보정) 질문에서 온 종목이다
    assert instrument_scope.build_resolved(rows[:1], set(), has_previous=False)[0]["source"] == "explicit"


def test_instrument_names_skip_not_found_and_non_company_tools():
    records = [
        {"tool_name": "stock_tool", "result": {"company_name": "삼성전자", "found": True}},
        {"tool_name": "news_tool", "result": {"company_name": "삼성전자", "found": True}},
        {"tool_name": "news_tool", "result": {"company_name": "엔비디아", "found": False}},  # 이름은 남지만 DB 확인에서 빠진다
        {"tool_name": "fx_tool", "result": {"pair": "USD/KRW"}},
        {"tool_name": "stock_tool", "result": "문자열"},
    ]
    assert instrument_scope.instrument_names(records) == ["삼성전자", "엔비디아"]


def test_english_name():
    assert instrument_scope.english_name("삼성전자") == "Samsung Electronics"
    assert instrument_scope.english_name("SK하이닉스") == "SK hynix"
    assert instrument_scope.english_name("없는회사") is None


def test_followup_topics_are_not_company_names():
    for word in ("외국인", "배당", "실적", "목표주가", "PER", "환율"):
        assert instrument_scope.is_followup_topic(word)
    assert not instrument_scope.is_followup_topic("롯데칠성")


def test_research_response_new_fields_default_for_old_callers():
    from app.api.research import ResearchResponse

    r = ResearchResponse(answer="a", used_tools=[])
    assert r.resolved_instruments == [] and r.clarification_required is False and r.clarification_reason is None
    r = ResearchResponse(answer="a", used_tools=["stock_tool"],
                         resolved_instruments=[{"ticker": "005930", "name": "삼성전자", "name_en": "Samsung Electronics",
                                                "source": "previous_context"}])
    assert r.model_dump()["resolved_instruments"][0]["source"] == "previous_context"


# ---------- 2. 영어 질문 ----------

NAMES = ["삼성전자", "SK하이닉스"]


@pytest.mark.parametrize("question, intent", [
    ("Why did Samsung Electronics fall this week?", "왜 떨어졌어"),
    ("Why did SK hynix drop yesterday?", "왜 떨어졌어"),
    ("Why is Samsung Electronics down today?", "왜 떨어졌어"),
    ("Why did SK hynix surge last week?", "왜 올랐어"),
    ("What caused the drop in Samsung Electronics?", "왜 떨어졌어"),
    ("サムスン電子はなぜ下がった?", "왜 떨어졌어"),
    ("三星电子为什么跌", "왜 떨어졌어"),
])
def test_cause_intent_is_normalized(question, intent):
    r = intent_i18n.normalize(question, NAMES)
    assert r.changed and intent in r.text


def test_future_question_is_not_a_cause_question():
    r = intent_i18n.normalize("Why will Samsung Electronics fall next week?", NAMES)
    assert "왜 떨어졌어" not in r.text


def test_pronoun_subject_gets_subjectless_marker():
    from app.answer_cause import is_subjectless

    r = intent_i18n.normalize("Why did it fall?", NAMES)
    assert r.text.startswith("이 종목 ") and is_subjectless(r.text)
    named = intent_i18n.normalize("Why did Samsung Electronics fall?", NAMES)
    assert not named.text.startswith("이 종목")


def test_korean_question_is_untouched():
    q = "삼성전자 이번 주 왜 떨어졌어?"
    assert intent_i18n.normalize(q, NAMES).text == q


@pytest.mark.parametrize("english, korean", [
    ("Analyze Samsung Electronics over the last five trading days.", "삼성전자 최근 5거래일 분석해 줘."),
    ("What was the 20-day return of SK Hynix?", "SK하이닉스 20거래일 수익률은?"),
    ("Why did Samsung Electronics fall this week?", "삼성전자 이번 주 왜 떨어졌어?"),
])
def test_english_and_korean_mean_the_same_period(english, korean):
    """같은 뜻의 영어·한국어 질문은 기간(거래일 수·거래일 여부)과 종목 표기가 같다(정규화 결과, DB 없이)."""
    from app.routing import is_cause_question

    routed = intent_i18n.normalize(english, NAMES).text
    pe, pk = parse_period(routed), parse_period(korean)
    assert (pe.period_days, pe.trading_days) == (pk.period_days, pk.trading_days)
    assert [n for n in NAMES if n in routed] == [n for n in NAMES if n in korean]
    assert is_cause_question(routed) == is_cause_question(korean)


# ---------- 3. 최고·최저·현재가 ----------

def stock_record(name="삼성전자", rows=None):
    rows = rows or [("2026-11-06", 100500.0), ("2026-11-07", 98200.0), ("2026-11-10", 97000.0), ("2026-11-11", 99800.0),
                    ("2026-11-12", 101300.0)]
    hi = max(rows, key=lambda r: r[1])
    lo = min(rows, key=lambda r: r[1])
    summary = (f"{name}의 {rows[-1][0]} 종가는 {rows[-1][1]:,.0f}원으로, 2026-11-05 종가 100,000원 대비 5거래일간 +1.30%입니다. "
               f"이 기간 종가 최고는 {hi[1]:,.0f}원({hi[0]}), 최저는 {lo[1]:,.0f}원({lo[0]})입니다.")
    return {"tool_name": "stock_tool", "arguments": {"ticker": name, "period_days": 5},
            "result": {"ticker": "005930", "company_name": name, "found": True, "period_days": 5, "summary": summary,
                       "change_pct": 1.5, "volume": 1000, "period_start_date": "2026-11-05", "period_end_date": rows[-1][0],
                       "period_change_pct": 1.3,
                       "prices": [{"price_date": d, "close_price": v, "volume": 1000, "change_pct": 0.1} for d, v in rows]}}


TODAY = date(2026, 11, 13)
FIXED = "삼성전자의 이 기간 종가 최고는 101,300원(2026-11-12), 최저는 97,000원(2026-11-10)입니다."


def test_period_extremes_reads_summary():
    e = period_extremes([stock_record()])["삼성전자"]
    assert e["high"] == (101300.0, "2026-11-12") and e["low"] == (97000.0, "2026-11-10")


def test_swapped_high_low_is_replaced():
    answer = "## 주가 동향\n삼성전자는 이 기간 최고가 97,000원, 최저가 101,300원을 기록했습니다. 거래는 활발했습니다."
    out, reasons = fix_extremes(answer, [stock_record()], TODAY)
    assert reasons and reasons[0].startswith("extreme_swapped:high")
    assert FIXED in out and "최고가 97,000원" not in out and "거래는 활발했습니다." in out


def test_wrong_high_value_is_replaced():
    out, reasons = fix_extremes("이 기간 최고가는 105,000원이었습니다.", [stock_record()], TODAY)
    assert reasons == ["extreme_value:high:105,000원"] and out == FIXED


def test_wrong_high_date_is_replaced():
    out, reasons = fix_extremes("최고가는 101,300원(2026-11-06)이었습니다.", [stock_record()], TODAY)
    assert reasons == ["extreme_date:high:2026-11-06"] and out == FIXED


def test_correct_extremes_are_kept():
    answer = "삼성전자의 최고가는 101,300원(11월 12일), 최저가는 97,000원(2026-11-10)입니다."
    assert fix_extremes(answer, [stock_record()], TODAY) == (answer, [])


def test_other_concepts_are_not_checked_here():
    answer = "52주 최고가는 120,000원입니다."  # 도구에 없는 개념: 숫자 검증(guard_answer)의 몫
    assert fix_extremes(answer, [stock_record()], TODAY) == (answer, [])


def test_realtime_wording_with_stored_close_is_dated():
    out, reasons = fix_extremes("삼성전자의 현재가는 101,300원입니다.", [stock_record()], TODAY)
    assert reasons == ["realtime_as_close:101,300원"]
    assert out == "삼성전자의 2026-11-12 종가는 101,300원입니다(저장된 일별 종가이며 실시간 가격이 아닙니다)."


def test_today_close_with_older_date_is_dated():
    out, reasons = fix_extremes("오늘 종가는 101,300원입니다.", [stock_record()], TODAY)
    assert reasons == ["today_close_date:2026-11-12"] and "2026-11-12 종가는 101,300원" in out
    same_day, r2 = fix_extremes("오늘 종가는 101,300원입니다.", [stock_record()], date(2026, 11, 12))
    assert r2 == [] and same_day == "오늘 종가는 101,300원입니다."


def test_multi_company_uses_named_subject_only():
    recs = [stock_record(), stock_record("SK하이닉스", [("2026-11-11", 500000.0), ("2026-11-12", 520000.0)])]
    out, reasons = fix_extremes("SK하이닉스의 최고가는 500,000원입니다. 최고가는 1원입니다.", recs, TODAY)
    assert reasons == ["extreme_swapped:high:500,000원"]
    assert out.startswith("SK하이닉스의 이 기간 종가 최고는 520,000원(2026-11-12), 최저는 500,000원(2026-11-11)입니다.")
    assert "최고가는 1원입니다." in out  # 종목을 정할 수 없는 문장은 손대지 않는다(숫자 검증이 따로 본다)


def test_extremes_without_data_are_dropped():
    rec = {"tool_name": "stock_tool", "result": {"company_name": "삼성전자", "found": True,
                                                 "prices": [{"price_date": "2026-11-12", "close_price": 101300.0}]}}
    out, reasons = fix_extremes("주가는 좋았습니다. 최고가는 101,300원입니다.", [rec], TODAY)
    assert reasons == ["extreme_without_data:high:101,300원"] and out == "주가는 좋았습니다."


def test_guard_answer_applies_extremes_before_number_check():
    answer = "삼성전자의 이 기간 최고가는 97,000원, 최저가는 101,300원입니다."
    out, log = guard_answer(answer, [stock_record()], "삼성전자 최근 5거래일 최고가 최저가", today=TODAY)
    assert out == FIXED and log["extreme_fixes"] and not log["replaced"]
    assert set(log["kinds"]) == {"number", "date", "target", "period"}


# ---------- 3. 내부 문구 ----------

@pytest.mark.parametrize("sentence, leak", [
    ("news_tool 호출에 필요한 인자가 없습니다: limit", True),
    ("limit 파라미터가 없어 뉴스를 가져오지 못했습니다.", True),
    ("The parameter limit was missing.", True),
    ("psycopg.OperationalError: connection refused", True),
    ("SQL 오류로 조회하지 못했습니다.", True),
    ("found=false 결과입니다.", True),
    ("period_days=5로 조회했습니다.", True),
    ("company 테이블에 등록된 종목명을 지정해주세요.", True),
    ("Traceback (most recent call last): File x", True),
    ("The stock hit its daily price limit.", False),
    ("Key parameters to watch include interest rates.", False),
    ("There is no upper limit on returns.", False),
    ("삼성전자는 5거래일간 3.2% 하락했습니다.", False),
    ("위험 인자로는 금리가 있습니다.", False),
])
def test_internal_leak_detection(sentence, leak):
    assert answer_tidy.has_internal_leak(sentence) is leak


def test_strip_internal_keeps_normal_sentences():
    text = "## 주가 동향\n삼성전자는 하락했습니다. news_tool 호출에 필요한 인자가 없습니다: limit\n- found=false 입니다."
    out, n = answer_tidy.strip_internal(text)
    assert n == 2 and out == "## 주가 동향\n삼성전자는 하락했습니다."
    assert answer_tidy.strip_internal("SQL 오류입니다.") == ("", 1)
    assert answer_tidy.strip_internal("정상 문장입니다.") == ("정상 문장입니다.", 0)


def test_strip_generic_drops_internal_sentences_too():
    out, n = answer_tidy.strip_generic("삼성전자는 하락했습니다. limit 파라미터가 없어 뉴스를 가져오지 못했습니다.")
    assert n == 1 and out == "삼성전자는 하락했습니다."


def test_llm_safe_result_hides_internal_errors():
    from app.agent import TOOL_ERROR_FOR_LLM, TOOL_MISSING_FOR_LLM, _llm_safe_result

    assert _llm_safe_result({"found": False, "error": "news_tool 실행 중 오류: OperationalError SELECT ..."})["error"] == TOOL_ERROR_FOR_LLM
    assert _llm_safe_result({"found": False, "message": "news_tool 호출에 필요한 인자가 없습니다: company_name"})["message"] == TOOL_MISSING_FOR_LLM
    msg = _llm_safe_result({"found": False, "message": "'롯데칠성' 종목을 찾지 못했습니다. company 테이블에 등록된 종목명 또는 종목코드를 지정해주세요."})
    assert "테이블" not in msg["message"] and "롯데칠성" in msg["message"]
    original = {"found": True, "summary": "s"}
    assert _llm_safe_result(original) == original and _llm_safe_result("x") == "x"


# ---------- 3. 출처 없는 원인 절 ----------

CAUSE_ANSWER = ("## 주가 동향\n삼성전자는 5거래일간 3.2% 하락했습니다.\n\n## 원인 분석\n반도체 업황 둔화 우려가 하락 원인입니다. "
                "같은 기간 코스피는 1.1% 하락했습니다.\n\n## 위험요인\n변동성이 큽니다.")


def test_cause_section_without_sources_is_replaced():
    out, changed = answer_tidy.enforce_cause_section(CAUSE_ANSWER, has_sources=False, has_market=False)
    assert changed and "## 원인 분석" not in out and "업황 둔화" not in out
    assert answer_tidy.NO_CAUSE_EVIDENCE_NOTE in out and "## 위험요인" in out and "3.2% 하락" in out


def test_cause_section_keeps_market_comparison_numbers():
    out, changed = answer_tidy.enforce_cause_section(CAUSE_ANSWER, has_sources=False, has_market=True)
    assert changed and "## 시장과 비교\n같은 기간 코스피는 1.1% 하락했습니다." in out and "업황 둔화" not in out


def test_cause_section_with_sources_is_kept():
    assert answer_tidy.enforce_cause_section(CAUSE_ANSWER, has_sources=True, has_market=False) == (CAUSE_ANSWER, False)
    plain = "삼성전자는 하락했습니다."
    assert answer_tidy.enforce_cause_section(plain, has_sources=False, has_market=False) == (plain, False)


# ---------- 4. 오류 코드 ----------

def test_order_validation_code():
    from app.api_errors import order_validation_code

    assert order_validation_code([{"loc": ("body", "quantity")}]) == "INVALID_ORDER_QUANTITY"
    assert order_validation_code([{"loc": ("body", "side")}]) == "UNSUPPORTED_ORDER_TYPE"
    assert order_validation_code([{"loc": ("body", "ticker")}]) == "INVALID_ORDER_REQUEST"


def test_coded_exception_rejects_unknown_code():
    from app.api_errors import CodedHTTPException

    with pytest.raises(ValueError):
        CodedHTTPException(status_code=400, detail="x", code="NOT_A_CODE")


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_unauthenticated_order_has_code_and_same_detail(client):
    r = client.post("/api/portfolio/orders", json={"ticker": "005930", "side": "buy", "quantity": 1})
    assert r.status_code == 401
    assert r.json() == {"detail": "인증이 필요합니다. 다시 로그인해주세요.", "code": "AUTHENTICATION_REQUIRED"}
    assert r.headers.get("www-authenticate") == "Bearer"


class _FakeDB:
    def close(self):
        pass


@pytest.fixture
def order_client(monkeypatch, client):
    from app.api import portfolio as portfolio_api
    from app.api.deps import get_owner_key
    from app.main import app
    from app.tools.company_resolver import Resolution

    company = type("C", (), {"id": 1, "name": "삼성전자", "ticker": "005930", "asset_type": "stock"})()
    monkeypatch.setattr(portfolio_api, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(portfolio_api, "resolve_company", lambda db, raw, allow_fuzzy=True: Resolution(company=company)
                        if raw == "005930" else Resolution(message=f"'{raw}' 종목을 찾지 못했습니다."))
    app.dependency_overrides[get_owner_key] = lambda: "user:1"
    yield client, portfolio_api, monkeypatch
    app.dependency_overrides.pop(get_owner_key, None)


@pytest.mark.parametrize("exc_name, message, code", [
    ("InsufficientQuantity", "보유 수량이 부족합니다(매도 요청 5주, 보유 0주).", "INSUFFICIENT_HOLDINGS"),
    ("InsufficientBalance", "잔고가 부족합니다(필요 1,000원, 보유 0원).", "INSUFFICIENT_CASH"),
])
def test_order_business_errors_have_codes(order_client, exc_name, message, code):
    client, portfolio_api, monkeypatch = order_client
    exc = getattr(portfolio_api, exc_name)

    def place(*a, **k):
        raise exc(message)

    monkeypatch.setattr(portfolio_api, "place_order", place)
    r = client.post("/api/portfolio/orders", json={"ticker": "005930", "side": "sell", "quantity": 5})
    assert r.status_code == 400 and r.json() == {"detail": message, "code": code}


def test_order_unknown_stock_has_code(order_client):
    client, _, _ = order_client
    r = client.post("/api/portfolio/orders", json={"ticker": "없는종목", "side": "buy", "quantity": 1})
    assert r.status_code == 404 and r.json()["code"] == "STOCK_NOT_FOUND" and "없는종목" in r.json()["detail"]


@pytest.mark.parametrize("body, code", [
    ({"ticker": "005930", "side": "buy", "quantity": 0}, "INVALID_ORDER_QUANTITY"),
    ({"ticker": "005930", "side": "buy", "quantity": "x"}, "INVALID_ORDER_QUANTITY"),
    ({"ticker": "005930", "side": "short", "quantity": 1}, "UNSUPPORTED_ORDER_TYPE"),
])
def test_order_validation_errors_keep_detail_list_and_add_code(order_client, body, code):
    client, _, _ = order_client
    r = client.post("/api/portfolio/orders", json=body)
    assert r.status_code == 422
    data = r.json()
    assert data["code"] == code and isinstance(data["detail"], list) and data["detail"][0]["loc"][0] == "body"


def test_other_http_errors_are_unchanged(client):
    """code가 없는 다른 HTTPException(예: 존재하지 않는 경로 404)은 기존 모양 그대로다."""
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and "code" not in r.json()


def test_plain_korean_high_low_phrases_are_checked():
    """"가장 높은 주가는 X원"처럼 최고·최저 낱말 없이 써도 같은 대조를 한다(실데이터 사본 확인에서 나온 표현)."""
    ok = "가장 높은 주가는 101,300원(2026-11-12)이었고, 최저 주가는 97,000원(2026-11-10)입니다."
    assert fix_extremes(ok, [stock_record()], TODAY) == (ok, [])
    out, reasons = fix_extremes("가장 높은 주가는 97,000원(2026-11-10)이었고, 가장 낮은 주가는 101,300원이었습니다.", [stock_record()], TODAY)
    assert reasons == ["extreme_swapped:high:97,000원"] and out == FIXED


def test_code_summary_lists_shared_article_once():
    """두 종목 뉴스에 같은 기사가 함께 걸려도 한 번만 쓴다(반복 정리가 날짜만 남기던 문제)."""
    from app.answer_tidy import tidy
    from app.answer_guard import code_summary

    shared = {"title": "삼성전자·SK하이닉스, '현금 329조 돌파'...AI 슈퍼사이클", "published_at": "2026-11-17T00:16:00+00:00"}
    recs = [{"tool_name": "news_tool", "result": {"found": True, "news": [shared, {"title": "삼성전자 단독 기사", "published_at": "2026-11-17T00:10:00+00:00"}]}},
            {"tool_name": "news_tool", "result": {"found": True, "news": [shared]}}]
    text, _ = tidy(code_summary(recs))
    lines = [line for line in text.split("\n") if line.startswith("- ")]
    assert lines == ["- 삼성전자·SK하이닉스, '현금 329조 돌파'...AI 슈퍼사이클 (2026-11-17)", "- 삼성전자 단독 기사 (2026-11-17)"]


@pytest.mark.parametrize("title", ["## 원인 분석", "### 원인", "## 하락 원인", "## 주가 하락의 원인", "**원인 분석**", "**하락 원인:**"])
def test_cause_title_variants_without_sources_are_replaced(title):
    """원인 절 제목은 "원인"으로 시작하지 않거나 굵은 글씨여도, 출처가 없으면 근거 있는 것처럼 남기지 않는다."""
    text = f"## 주가 동향\n삼성전자는 3.2% 하락했습니다.\n\n{title}\n업황 둔화 때문입니다.\n\n## 위험요인\n변동성이 큽니다."
    out, changed = answer_tidy.enforce_cause_section(text, has_sources=False, has_market=False)
    assert changed and title not in out and "업황 둔화" not in out
    assert answer_tidy.NO_CAUSE_EVIDENCE_NOTE in out and "## 위험요인\n변동성이 큽니다." in out and "3.2% 하락" in out
    assert answer_tidy.enforce_cause_section(text, has_sources=True, has_market=False) == (text, False)


def test_non_cause_titles_are_untouched_without_sources():
    text = "## 주가 동향\n삼성전자는 하락했습니다.\n\n**요약**\n흐름이 약합니다."
    assert answer_tidy.enforce_cause_section(text, has_sources=False, has_market=False) == (text, False)
