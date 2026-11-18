"""69차(2026-11-18) 원인 단독 질문의 기준 시점 정합성. DB·Ollama 없이 돈다(시세·근거·도구 결과는 가짜).

기준 시점 = 시세 기준일의 정규장 마감(krx_calendar.close_time). 그 뒤에 나온 기사·공시, 기준일 당일인데 시각을 모르는 자료는 원인 근거가 아니다.
1 시각 판정 2 원인 단독 코드 경로(LLM 없음) 3 오늘·어제 4 LLM 경로 필터(LLM에 넘기는 메시지 자체를 본다) 5 종목 정합성 6 68차 원인+전망 경로 유지
"""
import copy
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app import agent, answer_cause, outlook
from tests.test_answer_quality_round68 import KODEX, SAMSUNG, _res
from tests.test_outlook_round23 import COMPANY, KST, _rows, backtest  # noqa: F401  (backtest는 픽스처)

BASE = date(2026, 11, 17)  # 화요일, 정규장 15:30 마감
SPECIAL = date(2026, 11, 19)  # 수능일 16:30 마감(55차)


def kst(d: date, hh: int, mm: int = 0) -> str:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=KST).isoformat()


# ---------- 1. 시각 판정 ----------

@pytest.mark.parametrize("value, date_only, expected", [
    (kst(BASE - timedelta(days=1), 22), False, "before"),   # 기준일 전날 기사: 사용 가능
    (kst(BASE, 9, 10), False, "before"),                     # 같은 날 장중 기사: 마감 전
    (kst(BASE, 15, 29), False, "before"),
    (kst(BASE, 15, 30), False, "after"),                     # 마감 시각 이후는 그날 종가에 반영되지 않는다
    (kst(BASE, 18, 5), False, "after"),                      # 같은 날 장 마감 뒤 기사
    (kst(BASE + timedelta(days=1), 8), False, "after"),      # 기준일 뒤 기사
    (kst(BASE, 0, 0), False, "unknown"),                     # 당일 정각 자정 = 날짜만 있는 행일 수 있다
    (kst(BASE - timedelta(days=2), 0), False, "before"),     # 전날까지는 날짜만으로 충분하다
    (kst(BASE, 0, 0), True, "unknown"),                      # 기준일 당일 공시(접수 시각 없음)
    (kst(BASE - timedelta(days=1), 0), True, "before"),      # 전날 접수 공시
    (kst(BASE + timedelta(days=1), 0), True, "after"),       # 기준일 뒤 공시
    (None, False, "unknown"),
    ("not-a-date", False, "unknown"),
    ("2026-11-17T06:40:00+00:00", False, "after"),           # UTC 06:40 = KST 15:40
])
def test_time_state(value, date_only, expected):
    assert answer_cause.time_state(value, BASE, date_only=date_only) == expected


def test_special_close_day_uses_that_days_close_time():
    assert answer_cause.cutoff_at(SPECIAL).strftime("%H:%M") == "16:30"
    assert answer_cause.time_state(kst(SPECIAL, 16, 0), SPECIAL) == "before"
    assert answer_cause.time_state(kst(SPECIAL, 16, 31), SPECIAL) == "after"
    assert answer_cause.cutoff_at(BASE).strftime("%H:%M") == "15:30"


# ---------- 2. 원인 단독 질문 판정 ----------

@pytest.mark.parametrize("q", ["삼성전자 오늘 왜 올랐어?", "SK하이닉스 최근 왜 떨어졌어?", "카카오가 어제 급등한 이유가 뭐야?",
                               "오늘 주가가 하락한 원인이 뭐야?", "KODEX 200 요즘 왜 빠져?", "삼성전자 왜 이렇게 많이 올랐어?"])
def test_cause_only_questions(q):
    assert answer_cause.is_cause_only_question(q)


@pytest.mark.parametrize("q", [
    "하이닉스가 왜 올랐어? 그리고 앞으로 7일간 오를 거 같아?",   # 원인+전망은 68차 경로
    "삼성전자 실적 발표 후 왜 떨어졌어?",                         # 사건 뒤 반응(기준이 사건 날짜)
    "삼성전자 지난 한 달 동안 왜 떨어졌어?", "삼성전자 3개월간 왜 떨어졌어",  # 긴 기간은 LLM 경로(+필터)
    "PER이 왜 중요해?", "오늘 많이 오른 종목 알려줘", "삼성전자 왜 올랐는지 매수해줘",
])
def test_not_cause_only_questions(q):
    assert not answer_cause.is_cause_only_question(q)


# ---------- 원인 단독 코드 경로 픽스처 ----------

class _DB:
    def close(self):
        pass


@pytest.fixture
def cause_route(monkeypatch, backtest):  # noqa: F811
    state = {"companies": [COMPANY], "found": {"news": [], "disclosures": [], "after_count": 0, "after_first": None},
             "collect_args": [], "rows": _rows(), "by_name": {"SK하이닉스": COMPANY, "삼성전자": SAMSUNG, "KODEX 200": KODEX}}

    def analyze(db, company, period, now, rows=None):
        rows = rows if rows is not None else state["rows"]
        p = outlook.PERIODS[period.label]
        ind = outlook.compute_indicators(rows, p)
        sig = outlook.classify_signals(ind, p)
        base = date.fromisoformat(ind["base_date"])
        data = outlook.data_check(base, now, [])
        return {"indicators": ind, "signals": sig, "label": outlook.judge(sig, data), "data": data}

    def collect(db, company, base, start):
        state["collect_args"].append((company.name, base, start))
        return dict(state["found"])

    monkeypatch.setattr(agent, "_question_companies", lambda q: list(state["companies"]))
    monkeypatch.setattr(agent, "_company_by_name", lambda name: state["by_name"].get(name))
    monkeypatch.setattr(agent, "SessionLocal", _DB)
    monkeypatch.setattr(outlook, "analyze", analyze)
    monkeypatch.setattr(outlook, "price_rows", lambda db, company, now: list(state["rows"]))
    monkeypatch.setattr(answer_cause, "collect", collect)
    monkeypatch.setattr(agent, "_llm_chat", lambda **k: pytest.fail("원인 단독 경로는 채팅 LLM을 부르지 않는다"))
    agent._reset_llm_state()
    return state


def _ask(question, previous=None):
    resp, records = agent._answer_forecast(question, previous)
    return resp["answer"], records


def _base_of_rows():
    return _rows()[-1][0]


def test_cause_only_question_answers_with_code_without_forecast(cause_route):
    base = _base_of_rows()
    cause_route["found"] = {"news": [{"company_name": "SK하이닉스", "title": "SK하이닉스, HBM 공급 확대", "source": "가신문", "date": (base - timedelta(days=1)).isoformat(),
                                      "url": "https://a.kr/1", "published_at": kst(base - timedelta(days=1), 9)}],
                            "disclosures": [], "after_count": 4, "after_first": (base + timedelta(days=1)).isoformat(),
                            "after_close": 2, "unknown_news": 1, "unknown_disclosures": 1, "cutoff": "15:30"}
    text, records = _ask("SK하이닉스 최근 왜 올랐어?")
    assert text.startswith(f"SK하이닉스(000660) · 최근 주가 움직임과 원인 · 시세 기준일 {base.isoformat()}")
    assert "방향:" not in text and "향후 5거래일" not in text            # 전망을 덧붙이지 않는다
    assert f"{base.isoformat()} 장 마감 15:30 전까지" in text
    assert f"뉴스 {(base - timedelta(days=1)).isoformat()} (가신문) \"SK하이닉스, HBM 공급 확대\"" in text  # 기사 발행일 명시
    assert f"기준일 {base.isoformat()} 장 마감(15:30) 뒤에 나온 이 종목 기사 2건" in text
    assert "발행 시각이 없는 기사 1건·접수 시각이 없는 공시 1건은 장 마감 전후를 알 수 없어 원인 후보에서 뺐습니다" in text
    assert "기사 4건은" in text and "시세에 반영되지 않아" in text
    tools = [r["tool_name"] for r in records]
    assert tools[0] == "stock_tool" and tools[-2:] == ["news_tool", "disclosure_tool"]
    news = next(r for r in records if r["tool_name"] == "news_tool")
    assert news["result"]["called_via"] == "cause_only" and news["arguments"]["until"] == base.isoformat()
    assert cause_route["collect_args"][0][1] == base
    assert agent._meta()["cause_cutoff"]["after_close"] == 2


def test_no_evidence_says_cause_cannot_be_identified_without_inventing_reasons(cause_route):
    text, records = _ask("삼성전자 오늘 왜 떨어졌어?")
    assert "직접 다룬 기사·공시를 찾지 못해 움직임의 원인을 특정할 수 없습니다" in text
    for word in ("금리", "환율", "외국인", "수급", "차익실현", "경기"):  # 근거 없는 경제적 이유를 지어내지 않는다
        assert word not in text
    news = next(r for r in records if r["tool_name"] == "news_tool")
    assert news["result"]["found"] is False


def test_etf_cause_only_has_no_disclosure_and_says_articles(cause_route):
    cause_route["companies"] = [KODEX]
    text, records = _ask("KODEX 200 요즘 왜 빠져?")
    assert "disclosure_tool" not in [r["tool_name"] for r in records]
    assert "직접 다룬 기사를 찾지 못해" in text


def test_cause_only_without_company_uses_previous_company_but_not_for_market_questions(cause_route):
    cause_route["companies"] = []
    prev = {"report_id": 1, "question": "삼성전자 주가 알려줘", "answer": "...", "company_name": "삼성전자"}
    text, _ = _ask("오늘 주가가 하락한 원인이 뭐야?", prev)
    assert text.startswith("삼성전자(005930) · 최근 주가 움직임과 원인")
    assert agent._forecast_subject("오늘 코스피가 왜 떨어졌어?", prev) is None


def test_two_companies_or_long_period_stay_on_llm_path(cause_route):
    cause_route["companies"] = [COMPANY, SAMSUNG]
    assert agent._forecast_subject("SK하이닉스랑 삼성전자 왜 올랐어?", None) is None
    cause_route["companies"] = [COMPANY]
    assert agent._forecast_subject("SK하이닉스 지난 한 달 동안 왜 떨어졌어?", None) is None


# ---------- 3. 오늘·어제 ----------

def test_yesterday_trims_prices_to_that_day_and_cuts_evidence_there(cause_route, monkeypatch):
    rows = _rows()
    last, prev_day = rows[-1][0], rows[-2][0]
    now = datetime(last.year, last.month, last.day, 18, 0, tzinfo=KST)
    monkeypatch.setattr(answer_cause, "requested_day", lambda q, n: ("어제", prev_day) if "어제" in q else None)
    text, records = _ask("SK하이닉스 어제 왜 올랐어?")
    assert f"시세 기준일 {prev_day.isoformat()}" in text.splitlines()[0]
    assert f"질문하신 '어제'는 {prev_day.isoformat()} 거래일로 보고" in text
    assert cause_route["collect_args"][-1][1] == prev_day       # 근거 창의 끝 = 어제(그 뒤 기사는 후보가 아니다)
    assert "상태라" not in text                                   # 일부러 자른 것이므로 '지연' 경고를 붙이지 않는다
    assert now  # noqa: B018


def test_today_without_todays_price_says_last_stored_day():
    rows = _rows()
    last = rows[-1][0]
    now = datetime(last.year, last.month, last.day, 10, 0, tzinfo=KST) + timedelta(days=1)
    out, note, trimmed = answer_cause.rows_until(rows, "삼성전자 오늘 왜 올랐어?", now)
    assert out == rows and trimmed is False
    assert f"'오늘'({now.date().isoformat()}) 시세는 아직 저장되지 않아" in note and f"마지막 저장 거래일 {last.isoformat()}" in note


def test_missing_yesterday_price_is_not_called_intraday():
    rows = _rows()
    last = rows[-1][0]
    now = datetime(last.year, last.month, last.day, 10, 0, tzinfo=KST) + timedelta(days=3)
    _, note, _ = answer_cause.rows_until(rows, "카카오 어제 왜 올랐어?", now)
    assert "(미수집)" in note and "장중" not in note


def test_yesterday_is_the_last_trading_day_before_today():
    monday = datetime(2026, 11, 16, 9, 0, tzinfo=KST)
    assert answer_cause.requested_day("어제 왜 올랐어?", monday) == ("어제", date(2026, 11, 13))  # 월요일의 어제 = 금요일
    assert answer_cause.requested_day("오늘 왜 올랐어?", monday) == ("오늘", date(2026, 11, 16))
    assert answer_cause.requested_day("최근 왜 올랐어?", monday) is None


# ---------- 4. LLM 경로 필터 ----------

def _stock(name, base):
    return {"tool_name": "stock_tool", "arguments": {"ticker": name},
            "result": {"found": True, "company_name": name, "period_end_date": base.isoformat()}}


def _news(name, *items, limit=5):
    return {"tool_name": "news_tool", "arguments": {"company_name": name},
            "result": {"found": True, "company_name": name, "limit": limit,
                       "news": [{"company_name": name, "title": t, "url": f"https://n/{t}", "published_at": at} for t, at in items]}}


def test_filter_drops_after_close_next_day_and_unknown_time_news_and_refetches():
    rec = _news("삼성전자", ("다음날", kst(BASE + timedelta(days=1), 9)), ("마감뒤", kst(BASE, 17)), ("자정", kst(BASE, 0)),
                ("장중", kst(BASE, 11)), ("전날", kst(BASE - timedelta(days=1), 20)))
    calls = []

    def refetch(name, args):
        calls.append((name, args))
        return {"found": True, "news": [{"company_name": "삼성전자", "title": "장중", "url": "https://n/장중", "published_at": kst(BASE, 11)},
                                        {"company_name": "삼성전자", "title": "이틀전", "url": "https://n/이틀전",
                                         "published_at": kst(BASE - timedelta(days=2), 10)},
                                        {"company_name": "삼성전자", "title": "자정2", "url": "https://n/자정2", "published_at": kst(BASE, 0)}]}

    records = [rec, _stock("삼성전자", BASE)]
    out = answer_cause.filter_records(records, "삼성전자 지난 한 달 동안 왜 떨어졌어?", refetch=refetch)
    titles = [n["title"] for n in rec["result"]["news"]]
    assert titles == ["장중", "전날", "이틀전"]
    assert calls == [("news_tool", {"company_name": "삼성전자", "limit": 5, "before_kst": kst(BASE, 15, 30)})]
    cut = rec["result"]["cause_cutoff"]
    assert cut["excluded_after"] == 2 and cut["excluded_unknown_time"] == 1 and cut["cutoff"] == "15:30"
    assert "원인을 특정할 수 없다고" in cut["note"] and "지어내지 마세요" in cut["note"]
    assert out["excluded_after"] == 2 and out["refetched"] == ["news_tool"]


def test_filter_drops_base_day_and_later_disclosures_and_refetches_before_base_midnight():
    rec = {"tool_name": "disclosure_tool", "arguments": {"company_name": "삼성전자"},
           "result": {"found": True, "company_name": "삼성전자", "limit": 5, "disclosures": [
               {"company_name": "삼성전자", "title": "뒤공시", "source_url": "https://d/1", "disclosed_at": kst(BASE + timedelta(days=1), 0)},
               {"company_name": "삼성전자", "title": "당일공시", "source_url": "https://d/2", "disclosed_at": kst(BASE, 0)}]}}
    seen = {}

    def refetch(name, args):
        seen.update(args)
        return {"found": True, "disclosures": [{"company_name": "삼성전자", "title": "전날공시", "source_url": "https://d/3",
                                                "disclosed_at": kst(BASE - timedelta(days=1), 0)}]}

    answer_cause.filter_records([_stock("삼성전자", BASE), rec], "삼성전자 왜 떨어졌어?", refetch=refetch)
    assert [d["title"] for d in rec["result"]["disclosures"]] == ["전날공시"]
    assert seen["before_kst"] == kst(BASE, 0)  # 공시는 기준일 0시 전(전날까지)만 다시 읽는다
    assert rec["result"]["cause_cutoff"]["excluded_after"] == 1 and rec["result"]["cause_cutoff"]["excluded_unknown_time"] == 1


def test_filter_empties_list_and_says_cause_cannot_be_identified():
    rec = _news("삼성전자", ("다음날", kst(BASE + timedelta(days=1), 9)))
    answer_cause.filter_records([_stock("삼성전자", BASE), rec], "삼성전자 왜 떨어졌어?",
                                refetch=lambda n, a: {"found": False})
    assert rec["result"]["found"] is False and rec["result"]["news"] == []
    assert "원인을 특정할 수 없습니다" in rec["result"]["message"]


def test_filter_uses_each_companys_own_base_date_for_mixed_etf_and_stock_rag_results():
    etf_base, stock_base = BASE - timedelta(days=1), BASE
    rag = {"tool_name": "rag_search_tool", "arguments": {"query": "q"},
           "result": {"found": True, "company_name": None, "results": [
               {"type": "news", "company_names": ["KODEX 200"], "title": "ETF당일", "published_at": kst(BASE, 10)},  # ETF 기준일 뒤
               {"type": "news", "company_names": ["삼성전자"], "title": "주식당일", "published_at": kst(BASE, 10)},  # 주식 기준일 장중
               {"type": "disclosure", "company_names": ["삼성전자"], "title": "공시전날", "disclosed_at": kst(BASE - timedelta(days=1), 0)},
               {"type": "disclosure", "company_names": ["삼성전자"], "title": "공시당일", "disclosed_at": kst(BASE, 0)}]}}
    answer_cause.filter_records([_stock("KODEX 200", etf_base), _stock("삼성전자", stock_base), rag],
                                "KODEX 200이랑 삼성전자 왜 떨어졌어?")
    assert [x["title"] for x in rag["result"]["results"]] == ["주식당일", "공시전날"]
    assert rag["result"]["cause_cutoff"]["excluded_after"] == 1 and rag["result"]["cause_cutoff"]["excluded_unknown_time"] == 1


def test_filter_is_noop_for_non_cause_questions_or_without_base():
    rec = _news("삼성전자", ("다음날", kst(BASE + timedelta(days=1), 9)))
    before = copy.deepcopy(rec)
    assert answer_cause.filter_records([_stock("삼성전자", BASE), rec], "삼성전자 최근 뉴스 알려줘") is None
    assert answer_cause.filter_records([rec], "삼성전자 왜 떨어졌어?") is None  # 기준일을 모르면 추정하지 않는다
    assert rec == before


def test_internal_before_arg_is_stripped_from_llm_tool_calls(monkeypatch):
    seen = {}

    def fake_news(company_name, limit=5, before_kst=None):
        seen.update(company_name=company_name, before_kst=before_kst)
        return {"found": True}

    monkeypatch.setitem(agent.AVAILABLE_FUNCTIONS, "news_tool", fake_news)
    agent._run_tool("news_tool", {"company_name": "삼성전자", "before_kst": "2026-11-17T15:30:00+09:00"})
    assert seen == {"company_name": "삼성전자", "before_kst": None}
    agent._refetch_before("news_tool", {"company_name": "삼성전자", "before_kst": "2026-11-17T15:30:00+09:00"})
    assert seen["before_kst"] == "2026-11-17T15:30:00+09:00"


class _FakeLLMPath:
    """_answer()의 LLM 경로: 1차 호출은 news_tool만(모델이 원인 질문에 보통 그렇게 부른다), stock_tool은 코드가 덧붙인다."""

    def __init__(self, monkeypatch, question_companies):
        self.final_messages = None
        calls = [{"function": {"name": "news_tool", "arguments": {"company_name": n, "limit": 5}}} for n in question_companies]
        monkeypatch.setattr(agent, "_question_companies", lambda q: None)  # 코드 답 경로(원인 단독)를 건너뛰고 LLM 경로를 본다
        monkeypatch.setattr(agent, "_first_call", lambda model, messages, tools=None: (
            {"role": "assistant", "content": "", "tool_calls": copy.deepcopy(calls)}, copy.deepcopy(calls)))
        monkeypatch.setattr(agent, "_repair_company_args", lambda calls, q, prev=None, prev_company=None: {})
        monkeypatch.setattr(agent, "_market_comparison_available", lambda name: False)

        def run_tool(name, args):
            if name == "stock_tool":
                return {"found": True, "company_name": args["ticker"], "period_end_date": BASE.isoformat(),
                        "prices": [{"price_date": BASE.isoformat(), "close_price": 100.0, "change_pct": -1.0, "volume": 1}]}
            if name == "news_tool":
                c = args["company_name"]
                return {"found": True, "company_name": c, "limit": 5, "news": [
                    {"company_name": c, "title": f"{c} 미래기사", "url": f"https://n/{c}/f", "published_at": kst(BASE + timedelta(days=1), 9)},
                    {"company_name": c, "title": f"{c} 마감뒤기사", "url": f"https://n/{c}/a", "published_at": kst(BASE, 19)},
                    {"company_name": c, "title": f"{c} 장중기사", "url": f"https://n/{c}/b", "published_at": kst(BASE, 10)}]}
            return {"found": False}

        monkeypatch.setattr(agent, "_run_tool", run_tool)
        monkeypatch.setattr(agent, "_refetch_before", lambda name, args: {"found": False})

        def chat(**kw):
            self.final_messages = kw["messages"]
            return {"message": {"content": "근거가 부족해 원인을 특정할 수 없습니다."}}

        monkeypatch.setattr(agent.ollama, "chat", chat)


@pytest.mark.parametrize("question, names", [
    ("삼성전자 지난 한 달 동안 왜 떨어졌어?", ["삼성전자"]),
    ("삼성전자랑 SK하이닉스 왜 올랐어?", ["삼성전자", "SK하이닉스"]),
])
def test_llm_path_never_sends_articles_after_the_price_base_point(monkeypatch, question, names):
    llm = _FakeLLMPath(monkeypatch, names)
    agent._reset_llm_state()
    response, records = agent._answer(question, "m")
    tool_text = "\n".join(m["content"] for m in llm.final_messages if m["role"] == "tool")
    for n in names:
        assert f"{n} 장중기사" in tool_text
        assert f"{n} 미래기사" not in tool_text and f"{n} 마감뒤기사" not in tool_text
    assert all("미래기사" not in s.get("title", "") and "마감뒤기사" not in s.get("title", "") for s in response["sources"])
    stored = [r for r in records if r["tool_name"] == "news_tool"]
    assert all(r["result"]["cause_cutoff"]["excluded_after"] == 2 for r in stored)
    assert json.loads(next(m["content"] for m in llm.final_messages if m["role"] == "tool" and "cause_cutoff" in m["content"]))


def test_llm_path_keeps_articles_for_non_cause_questions(monkeypatch):
    llm = _FakeLLMPath(monkeypatch, ["삼성전자"])
    agent._reset_llm_state()
    agent._answer("삼성전자 최근 뉴스랑 주가 같이 알려줘", "m")
    tool_text = "\n".join(m["content"] for m in llm.final_messages if m["role"] == "tool")
    assert "삼성전자 미래기사" in tool_text and "cause_cutoff" not in tool_text


# ---------- 6. 68차 원인+전망 경로도 같은 규칙 ----------

def test_cause_forecast_section_shows_same_day_exclusions(cause_route):
    base = _base_of_rows()
    cause_route["found"] = {"news": [], "disclosures": [], "after_count": 0, "after_first": None,
                            "after_close": 3, "unknown_news": 0, "unknown_disclosures": 2, "cutoff": "15:30"}
    text, _ = _ask("하이닉스가 왜 올랐어? 그리고 앞으로 7일간 오를 거 같아?")
    assert "방향:" in text  # 결론(방향)이 먼저
    assert f"기준일 {base.isoformat()} 장 마감(15:30) 뒤에 나온 이 종목 기사 3건" in text
    assert "접수 시각이 없는 공시 2건은 장 마감 전후를 알 수 없어" in text


def test_section_without_new_keys_still_renders_like_round68():
    res, _ = _res()
    found = {"news": [], "disclosures": [], "after_count": 0, "after_first": None}
    text = answer_cause.section(COMPANY, res, "왜 올랐어?", found)
    assert "직접 다룬 기사·공시를 찾지 못해 움직임의 원인을 특정할 수 없습니다" in text
    assert "장 마감 15:30 전까지" in text or "장 마감 16:30 전까지" in text
