"""48차 문제 1: ETF 보유 종목(구성종목) 질문을 실제 holdings 데이터로 보낸다.

수정 전 실측(2026-11-13, dev DB 읽기 전용): 아래 질문은 구성종목 게이트(asks_constituents)를 지나지 못해 LLM 경로(주가·뉴스)로 갔다.
"KODEX 200 포트폴리오 알려줘", "KODEX 200 안에 뭐 들어있어?", "KODEX 200 비중 큰 종목", "KODEX 200 1위 종목은?",
"What are the top holdings of KODEX 200?", "KODEX 200の構成銘柄を教えて", "KODEX 200的成分股有哪些？" -> 답에 구성종목이 없었다.
"KODEX 200에서 삼성전자 비중은 얼마야?"는 두 종목 주가 비교 답이 됐다(비중 0건).
그리고 구성종목 답(코드 템플릿)을 다른 언어로 보낼 때 LLM 번역을 거쳐, 숫자 많은 목록이 번역 검증에 떨어지면 데이터 요약으로 바뀌었다.
"""

import re

import pytest

from app import answer_i18n, etf_holdings as E, etf_holdings_i18n as EI
from app.etf import asks_constituents, asks_holding_weight

HANGUL = re.compile(r"[가-힣]")


# ---------- 게이트 ----------

KO_NEW = [
    "KODEX 200 포트폴리오 알려줘", "KODEX 200 안에 뭐 들어있어?", "KODEX 200 비중 큰 종목", "KODEX 200이 많이 담은 종목",
    "KODEX 200은 뭘 담고 있어?", "KODEX 200 1위 종목은?", "TIGER 반도체TOP10 안에 뭐 들어있어?", "KODEX 200은 무엇에 투자해?",
]
FOREIGN = [
    "What are the top holdings of KODEX 200?", "What stocks are in TIGER 200?", "KODEX 200 holdings and weights",
    "Show me the constituents of KODEX 반도체", "KODEX 200 top 10 holdings",
    "Which companies does KODEX 미국나스닥100 hold?",  # 48차 holdout E3에서 찾음(수정 후 추가)
    "KODEX 200の構成銘柄を教えて", "KODEX 반도체の組入上位銘柄は？", "TIGER 200の保有銘柄", "KODEX 200には何が入っていますか？",
    "KODEX 200的成分股有哪些？", "TIGER 미국나스닥100持仓前十", "KODEX 200里面有什么股票？", "KODEX 200重仓股",
]
NOT_CONSTITUENTS = [
    "KODEX 200 비중 늘려도 돼?", "KODEX 레버리지 장기 보유해도 괜찮아?", "TIGER 반도체TOP10 주가 알려줘",
    "KODEX 200 포트폴리오에 넣어도 될까?", "Should I keep holding KODEX 200?", "Should I add KODEX 200 to my portfolio?",
    "What is the price of KODEX 200?", "KODEX 200の株価は？", "KODEX 200的股价是多少？", "TIGER 반도체TOP10 price",
]


@pytest.mark.parametrize("q", KO_NEW + FOREIGN)
def test_constituent_gate_accepts_new_wordings(q):
    assert asks_constituents(q)


@pytest.mark.parametrize("q", NOT_CONSTITUENTS)
def test_constituent_gate_rejects_other_etf_questions(q):
    assert not asks_constituents(q)


@pytest.mark.parametrize("q", [
    "KODEX 200에서 삼성전자 비중은 얼마야?", "KODEX 200에 삼성전자 얼마나 들어있어?", "KODEX 200의 SK하이닉스 편입 비중",
    "KODEX 200에 삼성전자 들어있어?", "What is Samsung Electronics' weight in KODEX 200?", "How much Samsung Electronics does KODEX 200 hold?",
    "KODEX 200にサムスン電子はどれくらい入っていますか？", "KODEX 200のサムスン電子の比率は？", "KODEX 200里三星电子占多少权重？",
    "KODEX 200中SK海力士占比多少",
])
def test_holding_weight_gate(q):
    assert asks_holding_weight(q)


@pytest.mark.parametrize("q", [
    "KODEX 200 비중 늘려도 돼?", "삼성전자랑 KODEX 200 비중 어떻게 나눠야 해?", "KODEX 200이랑 삼성전자 주가 비교해줘",
    "Should I increase my KODEX 200 weight?", "KODEX 200と삼성전자、どちらを買うべき？", "KODEX 200和三星电子应该怎么配置？",
])
def test_holding_weight_gate_rejects_allocation_and_comparison(q):
    assert not asks_holding_weight(q)


# ---------- 특정 종목 비중 답(코드 템플릿) ----------


def weight_info(found=True, **kw):
    base = {"etf": "KODEX 200", "etf_ticker": "069500", "as_of_date": "2026-10-30", "source": E.SOURCES[E.KODEX],
            "holding_count": 201, "stale_notice": None, "data_warnings": [],
            "items": [{"name": "삼성전자", "ticker": "005930", "found": found, "weight_pct": 34.19 if found else None,
                       "rank": 1 if found else None}]}
    return {**base, **kw}


def test_weight_answer_states_value_rank_source_and_base_date():
    text = E.holding_weight_answer(weight_info())
    assert "KODEX 200에서 삼성전자(005930)의 비중은 34.19%" in text
    assert "201개 구성종목 중 1위" in text and "2026-10-30 기준" in text and "삼성자산운용 KODEX" in text
    assert "투자 권유가 아닙니다" in text


def test_weight_answer_says_not_included_instead_of_guessing():
    text = E.holding_weight_answer(weight_info(found=False))
    assert "삼성전자(005930) 종목은 없습니다" in text and "2026-10-30 기준" in text and "%" not in text.split("※")[0]


class Co:
    def __init__(self, name, asset_type="etf", ticker="069500", id_=1):
        self.id, self.ticker, self.name, self.asset_type = id_, ticker, name, asset_type


@pytest.fixture
def agent_db(monkeypatch):
    import app.agent as agent

    monkeypatch.setattr(agent, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    agent._reset_llm_state()
    return agent


def kodex_view():
    items = [{"rank": 1, "name": "삼성전자", "code": "005930", "weight_pct": 34.19},
             {"rank": 2, "name": "SK하이닉스", "code": "000660", "weight_pct": 27.40},
             {"rank": 3, "name": "SK스퀘어", "code": "402340", "weight_pct": 2.76}]
    return {"ticker": "069500", "name": "KODEX 200", "as_of_date": "2026-10-30", "source": E.SOURCES[E.KODEX], "stale": False,
            "stale_notice": None, "holding_count": 201, "total_weight_pct": 100.0, "data_warnings": [], "items": items}


class TestAgentRouting:
    def test_weight_question_uses_holdings_not_prices(self, agent_db, monkeypatch):
        calls = []

        def fake(db, etf, stocks, today=None):
            calls.append((etf.name, [s.name for s in stocks]))
            return weight_info()

        monkeypatch.setattr(agent_db, "holding_weights", fake)
        monkeypatch.setattr(agent_db, "chat_views", lambda *a, **k: pytest.fail("목록 조회가 아니다"))
        resp, records = agent_db._answer_etf_constituents(
            "KODEX 200에서 삼성전자 비중은 얼마야?", [Co("KODEX 200"), Co("삼성전자", "stock", "005930", 2)])
        assert calls == [("KODEX 200", ["삼성전자"])]
        assert "34.19%" in resp["answer"] and resp["used_tools"] == [] and records == []  # API 계약: used_tools 값 목록 그대로

    @pytest.mark.parametrize("q", ["What are the top holdings of KODEX 200?", "KODEX 200の構成銘柄を教えて", "KODEX 200的成分股有哪些？",
                                   "KODEX 200 포트폴리오 알려줘"])
    def test_foreign_and_new_korean_wordings_get_the_holdings_template(self, agent_db, monkeypatch, q):
        monkeypatch.setattr(agent_db, "chat_views", lambda db, cs: ([kodex_view()], []))
        resp, _ = agent_db._answer_etf_constituents(q, [Co("KODEX 200")])
        assert "1. 삼성전자 (005930) 34.19%" in resp["answer"] and "2026-10-30 기준" in resp["answer"]
        assert agent_db._meta()["etf_holdings"]["kind"] == "constituents"

    def test_end_to_end_locale_answer_is_a_template_without_llm(self, agent_db, monkeypatch):
        import ollama

        monkeypatch.setattr(agent_db, "_question_companies", lambda q: [Co("KODEX 200")])
        monkeypatch.setattr(agent_db, "chat_views", lambda db, cs: ([kodex_view()], []))
        monkeypatch.setattr(ollama, "chat", lambda *a, **k: pytest.fail("LLM을 부르면 안 된다"))
        monkeypatch.setattr(ollama.Client, "chat", lambda *a, **k: pytest.fail("번역 LLM을 부르면 안 된다"))
        r = agent_db.ask_question("KODEX 200の構成銘柄を教えて", save_report=False, locale="ja-JP")
        assert r["meta"]["translation_status"] == "localized"
        assert "1. 삼성전자 (005930) 34.19%" in r["answer"] and "2026-10-30" in r["answer"] and "構成銘柄" in r["answer"]


# ---------- 언어별 템플릿 ----------


def _nums(s):
    return sorted(re.findall(r"\d+(?:\.\d+)?", s))


@pytest.mark.parametrize("locale", ["en-US", "ja-JP", "zh-CN"])
def test_constituents_template_keeps_every_number_and_name(locale):
    view = kodex_view()
    ko = E.constituents_chat_answer([view], [], lambda names: "", None)
    out = EI.localize(ko, {"kind": "constituents", "views": [view], "missing": [], "top_n": None}, locale)
    assert out is not None
    assert _nums(out) == _nums(ko)  # 숫자·날짜·종목코드가 하나도 바뀌거나 빠지지 않는다
    for name in ("KODEX 200", "삼성전자", "SK하이닉스", "SK스퀘어"):
        assert name in out
    assert not HANGUL.search(re.sub("|".join(["삼성전자", "SK하이닉스", "SK스퀘어"]), "", out))  # 이름 밖에 한국어가 없다


@pytest.mark.parametrize("locale", ["en-US", "ja-JP", "zh-CN"])
def test_weight_template_keeps_numbers(locale):
    info = weight_info()
    ko = E.holding_weight_answer(info)
    out = EI.localize(ko, {"kind": "weight", "info": info}, locale)
    assert out is not None and _nums(out) == _nums(ko) and "삼성전자" in out
    info2 = weight_info(found=False)
    ko2 = E.holding_weight_answer(info2)
    out2 = EI.localize(ko2, {"kind": "weight", "info": info2}, locale)
    assert out2 is not None and _nums(out2) == _nums(ko2)


def test_template_is_skipped_when_the_answer_differs():
    view = kodex_view()
    ko = E.constituents_chat_answer([view], [], lambda names: "", None)
    assert EI.localize(ko + " 덧붙인 문장", {"kind": "constituents", "views": [view], "missing": [], "top_n": None}, "en-US") is None


def test_leverage_and_stale_notes_are_localized():
    view = {**kodex_view(), "name": "KODEX 레버리지", "stale": True,
            "stale_notice": "마지막으로 수집한 구성종목 기준일은 2026-10-30입니다.", "total_weight_pct": 210.5,
            "data_warnings": E.weight_warnings("KODEX 레버리지", 210.5, 208)}
    ko = E.constituents_chat_answer([view], [], lambda names: "", None)
    out = EI.localize(ko, {"kind": "constituents", "views": [view], "missing": [], "top_n": None}, "en-US")
    assert out is not None and "leveraged" in out and "2026-10-30" in out and _nums(out) == _nums(ko)


def test_localize_answer_uses_the_template(monkeypatch):
    monkeypatch.setattr(answer_i18n, "translate", lambda *a, **k: pytest.fail("번역 LLM을 부르면 안 된다"))
    view = kodex_view()
    ko = E.constituents_chat_answer([view], [], lambda names: "", None)
    out, status = answer_i18n.localize_answer("q", ko, [], "zh-CN",
                                              code_data={"kind": "constituents", "views": [view], "missing": [], "top_n": None})
    assert status == "localized" and "成分股" in out
