"""ETF 2단계 순수 로직(DB·Ollama 없음): 조사, 레버리지/인버스 판별과 고정 문구, 뉴스 이름 확인·수치 가리기, 근거 없는 등락률 문장 제거,
빈 섹션 정리, 에이전트의 ETF 경로(가짜 LLM/tool), 진단의 ETF 처리와 새 플래그, 진단 이력 비교의 새 플래그.
라우터는 test_etf_stage2_api.py, 실제 DB는 test_etf_stage2_devdb.py, 실제 Ollama는 test_etf_slow.py."""

import json
from decimal import Decimal

import pytest

import app.agent as agent
import app.collectors as collectors
from app.diagnosis import HoldingInput, build_diagnosis, compute_flags, compute_portfolio, holding_fact, rule_based_text
from app.diagnosis_history import compute_changes
from app.etf import (
    ETF_ANSWER_RULES,
    NUMBER_MASK,
    allowed_percents,
    drop_unsupported_percent_sentences,
    leverage_kind,
    leverage_notice,
    mask_news_numbers,
    mentions_etf_name,
    news_result_for_llm,
    remove_empty_sections,
)
from app.korean import josa


class TestJosa:
    @pytest.mark.parametrize("word, eun, ga, ro", [
        ("KODEX 200", "은", "이", "으로"), ("TIGER 미국S&P500", "은", "이", "으로"), ("KODEX 200TR", "은", "이", "로"),
        ("KODEX 레버리지", "는", "가", "로"), ("삼성전자", "는", "가", "로"), ("에코프로비엠", "은", "이", "으로"),
        ("NAVER", "는", "가", "로"), ("LG", "는", "가", "로"), ("HMM", "은", "이", "으로"), ("TIGER 200 IT", "는", "가", "로"),
        ("KODEX CD금리액티브(합성)", "은", "이", "으로"), ("서울", "은", "이", "로"), ("10", "은", "이", "으로"), ("5", "는", "가", "로"),
    ])
    def test_reading_based_particles(self, word, eun, ga, ro):
        assert josa(word, "은/는") == word + eun
        assert josa(word, "이/가") == word + ga
        assert josa(word, "으로/로") == word + ro

    def test_other_pairs_and_errors(self):
        assert josa("KODEX 200", "을/를") == "KODEX 200을" and josa("삼성전자", "과/와") == "삼성전자와"
        with pytest.raises(ValueError):
            josa("x", "의/에")
        assert josa("", "은/는") == "는"


class TestLeverage:
    @pytest.mark.parametrize("name, kind", [
        ("KODEX 레버리지", "레버리지"), ("KODEX 코스닥150레버리지", "레버리지"), ("KODEX 인버스", "인버스"),
        ("KODEX 200선물인버스2X", "인버스 레버리지"), ("TIGER 미국나스닥100레버리지(합성)", "레버리지"), ("KODEX 2X 미국나스닥", "레버리지"),
        ("KODEX 200", None), ("TIGER 미국S&P500", None), ("KODEX 200TR", None), ("TIGER 200 IT", None), ("ACE KRX금현물", None),
    ])
    def test_kind_by_name(self, name, kind):
        assert leverage_kind(name) == kind

    def test_notice_is_fixed_text_without_numbers(self):
        text = leverage_notice("KODEX 레버리지", "레버리지")
        assert text.startswith("※ KODEX 레버리지는 이름으로 보아 레버리지 ETF입니다.")
        assert "복리 효과" in text and "기초지수" in text
        assert not any(ch.isdigit() for ch in text)
        assert not any(w in text for w in ("매수", "매도", "추천"))
        assert leverage_notice("KODEX 200선물인버스2X", "인버스 레버리지").startswith("※ KODEX 200선물인버스2X는")


class TestNewsName:
    OTHERS = ["KODEX 200TR", "KODEX 200타겟위클리커버드콜", "KODEX 레버리지", "KODEX 코스닥150레버리지"]

    @pytest.mark.parametrize("text, expected", [
        ("KODEX 200 순자산 10조 돌파", True),
        ("코덱스 200에 개인 매수 몰려", True),
        ("KODEX200 거래대금 1위", True),
        ("KODEX 200TR 분배금 지급", False),                       # 더 긴 다른 ETF 이름의 일부
        ("KODEX 200타겟위클리커버드콜 인기", False),
        ("KODEX 200TR과 KODEX 200 비교", True),                  # 둘 다 나오면 이 ETF도 나온 것
        ("코스피200 지수 하락", False),
    ])
    def test_kodex_200(self, text, expected):
        assert mentions_etf_name(text, "KODEX 200", self.OTHERS) is expected

    def test_leverage_not_confused_with_kosdaq_leverage(self):
        assert mentions_etf_name("KODEX 코스닥150레버리지 급락", "KODEX 레버리지", self.OTHERS) is False
        assert mentions_etf_name("KODEX 레버리지는 12.45% 하락", "KODEX 레버리지", self.OTHERS) is True

    def test_mask_numbers(self):
        text = "반도체 레버리지 20% 급락…KODEX 레버리지는 12.45% 하락, 순자산 1조2,000억원, 2배 추종"
        masked = mask_news_numbers(text)
        assert "20%" not in masked and "12.45%" not in masked and "2배" not in masked
        assert masked.count(NUMBER_MASK) >= 3 and "KODEX 레버리지" in masked

    def test_news_result_for_llm_keeps_original(self):
        result = {"found": True, "news": [{"title": "20% 급락", "content": "12.45% 하락", "url": "u"}]}
        llm = news_result_for_llm(result)
        assert llm["news"][0]["title"] == f"{NUMBER_MASK} 급락" and llm["news"][0]["url"] == "u" and "note" in llm
        assert result["news"][0]["title"] == "20% 급락"  # 원본(기록·출처)은 그대로
        assert news_result_for_llm({"found": False}) == {"found": False}


STOCK_RESULT = {
    "found": True, "change_pct": 2.13, "period_change_pct": -27.21, "trading_days": 20, "volume": 12056891,
    "period_start_date": "2026-09-10", "period_end_date": "2026-10-14",
    "summary": "KODEX 레버리지의 2026-10-14 종가는 83,710원으로, 2026-09-10 종가 대비 20거래일간 -27.21%입니다. 마지막 거래일 하루 등락률은 +2.13%입니다.",
}


class TestPercentGuard:
    def test_misattributed_news_number_is_dropped(self):
        """1단계 관찰: 기사 속 다른 상품의 -20% 급락을 KODEX 레버리지의 것으로 썼다."""
        answer = ("## 주가 동향\nKODEX 레버리지는 20거래일간 27.21% 하락했습니다. KODEX 레버리지의 주가가 최근 20% 급락했다.\n"
                  "## 위험요인\n변동성이 큽니다.")
        out, dropped = drop_unsupported_percent_sentences(answer, allowed_percents([STOCK_RESULT]))
        assert dropped == ["KODEX 레버리지의 주가가 최근 20% 급락했다."]
        assert "27.21% 하락" in out and "20% 급락" not in out and "## 위험요인" in out

    @pytest.mark.parametrize("pct", ["27.21%", "27.2%", "27%", "2.13%", "+2.13%", "2.1%"])
    def test_numbers_from_the_price_result_and_their_roundings_stay(self, pct):
        out, dropped = drop_unsupported_percent_sentences(f"등락률은 {pct}입니다.", allowed_percents([STOCK_RESULT]))
        assert dropped == [] and pct in out

    def test_sentences_without_percent_are_untouched(self):
        text = "## 뉴스·공시 근거\n레버리지 ETF 관련 기사가 있었습니다.\n변동성이 커졌다는 기사입니다."
        assert drop_unsupported_percent_sentences(text, set())[0] == text

    def test_allowed_percents_are_only_percent_values(self):
        a = allowed_percents([{"x": "종가는 83,710원, 20거래일간 -27.21%", "change_pct": 1.5, "period_days": 20, "volume": 7}])
        assert {Decimal("27.21"), Decimal("27.2"), Decimal("1.5")} <= a
        assert Decimal("83710") not in a and Decimal("20") not in a and Decimal("7") not in a


class TestRemoveEmptySections:
    def test_placeholder_sections_are_removed(self):
        """1단계 실제 답변: 결과가 없는 섹션을 자리표시 문장으로 채웠다."""
        answer = ("## 주가 동향\nTIGER 미국나스닥100의 최근 주가는 181,530.0원입니다.\n\n"
                  "## 뉴스·공시 근거\n(이 섹션은 tool 호출 결과가 없으므로 빈칸입니다.)\n\n"
                  "## 위험요인\n(이 섹션은 tool 호출 결과에서 유의미한 신호가 없으므로 빈칸입니다.)")
        out = remove_empty_sections(answer)
        assert out == "## 주가 동향\nTIGER 미국나스닥100의 최근 주가는 181,530.0원입니다."

    @pytest.mark.parametrize("body", ["해당 없음", "결과가 없습니다.", "", "N/A", "관련 정보가 없어 생략합니다."])
    def test_various_placeholders(self, body):
        out = remove_empty_sections(f"## 주가 동향\n종가는 1,000원입니다.\n## 위험요인\n{body}")
        assert "## 위험요인" not in out and "## 주가 동향" in out

    def test_real_sections_and_plain_answers_are_kept(self):
        real = "## 주가 동향\n종가는 1,000원입니다.\n## 위험요인\n하락 폭이 커서 변동성에 유의할 필요가 있습니다."
        assert remove_empty_sections(real) == real
        assert remove_empty_sections("삼성전자 종가는 263,000원입니다.") == "삼성전자 종가는 263,000원입니다."
        long_line = "## 위험요인\n" + "최근 공시가 없어 실적 확인은 어렵지만 주가 하락 폭이 커서 변동성에 유의할 필요가 있습니다." * 2
        assert remove_empty_sections("## 주가 동향\n종가\n" + long_line).count("## ") == 2  # 긴 실제 문장은 자리표시가 아니다

    def test_never_returns_empty(self):
        only = "## 위험요인\n해당 없음"
        assert remove_empty_sections(only) == only


# ---------------- 에이전트 ETF 경로(가짜 LLM/tool) ----------------

def call(name, **args):
    return {"function": {"name": name, "arguments": args}}


NEWS_RESULT = {
    "company_name": "KODEX 레버리지", "found": True, "asset_type": "etf",
    "news": [{"title": "반도체 레버리지 20% 급락…KODEX 레버리지는 12.45% 하락", "content": "인버스는 20% 상승", "url": "https://n/1",
              "source": "s", "published_at": "2026-10-14T00:00:00", "company_name": "KODEX 레버리지"}],
}


@pytest.fixture
def fake_agent(monkeypatch):
    """1차 호출 tool_calls, 종목 판별, tool 결과, 최종 답변을 정해 둔 가짜. 기록(state)을 돌려준다."""
    state = {"first": [], "subjects": [], "final": "답변", "messages": None, "ran": [], "prices": {}}

    def first_call(model, messages, tools=None):
        calls = [json.loads(json.dumps(c)) for c in state["first"]]
        if not calls:
            return {"role": "assistant", "content": "개념 설명입니다."}, None
        return {"role": "assistant", "content": "", "tool_calls": calls}, calls

    def run_tool(name, args):
        state["ran"].append((name, dict(args)))
        if name == "stock_tool":
            return {**STOCK_RESULT, **state["prices"].get(args.get("ticker"), {}), "ticker": args.get("ticker"),
                    "company_name": args.get("ticker"), "period_days": args.get("period_days")}
        if name == "news_tool":
            return json.loads(json.dumps(NEWS_RESULT))
        return {"found": False, "message": "x"}

    def chat(model, messages, **kw):
        state["messages"] = messages
        return {"message": {"content": state["final"]}}

    monkeypatch.setattr(agent, "_first_call", first_call)
    monkeypatch.setattr(agent, "_run_tool", run_tool)
    monkeypatch.setattr(agent.ollama, "chat", chat)
    monkeypatch.setattr(agent, "_repair_company_args", lambda calls, q, prev=None: {})
    monkeypatch.setattr(agent, "_market_comparison_available", lambda c: False)
    monkeypatch.setattr(agent, "_question_subjects", lambda q, calls: state["subjects"])
    monkeypatch.setattr(
        agent, "_called_companies",
        lambda calls, tool: {c["function"]["arguments"].get(agent._COMPANY_ARG[tool]) for c in calls if c["function"]["name"] == tool},
    )
    return state


class TestAgentEtfPath:
    def test_etf_question_gets_stock_and_news_with_20_days(self, fake_agent):
        fake_agent.update(first=[call("stock_tool", ticker="TIGER 미국나스닥100", period_days=1)],
                          subjects=[("TIGER 미국나스닥100", "etf")])
        resp, records = agent._answer("TIGER 미국나스닥100 어때?", "m")
        assert [r["tool_name"] for r in records] == ["stock_tool", "news_tool"]
        assert records[0]["arguments"]["period_days"] == 20
        assert records[0]["result"]["period_days_corrected_via"] == "etf_default_period"
        assert records[1]["result"]["called_via"] == "question_keywords"

    def test_period_named_in_question_wins(self, fake_agent):
        fake_agent.update(first=[call("stock_tool", ticker="KODEX 200TR", period_days=3)], subjects=[("KODEX 200TR", "etf")])
        _, records = agent._answer("KODEX 200TR 3개월 추이", "m")
        assert records[0]["arguments"]["period_days"] not in (3, 20)  # 파서가 인식한 3개월(약 60~66거래일)
        assert records[0]["result"]["period_days_corrected_via"] == "period_parser"

    def test_etf_and_stock_comparison_uses_the_same_period(self, fake_agent):
        fake_agent.update(first=[call("stock_tool", ticker="KODEX 200", period_days=1)],
                          subjects=[("KODEX 200", "etf"), ("삼성전자", "stock")])
        _, records = agent._answer("KODEX 200이랑 삼성전자 중에 최근 어느 게 더 올랐어?", "m")
        stock = [r for r in records if r["tool_name"] == "stock_tool"]
        assert [r["arguments"]["ticker"] for r in stock] == ["KODEX 200", "삼성전자"]
        assert {r["arguments"]["period_days"] for r in stock} == {20}
        assert [r["arguments"]["company_name"] for r in records if r["tool_name"] == "news_tool"] == ["KODEX 200"]

    def test_no_tool_answer_for_etf_still_queries(self, fake_agent):
        fake_agent.update(first=[], subjects=[("KODEX 인버스", "etf")], final="KODEX 인버스는 ETF입니다.")
        resp, records = agent._answer("KODEX 인버스는 어떤 상품이야?", "m")
        assert [r["tool_name"] for r in records] == ["stock_tool", "news_tool"]
        assert "복리 효과" in resp["answer"]  # 인버스 고정 문구

    def test_news_numbers_are_masked_for_llm_and_rules_added(self, fake_agent):
        fake_agent.update(first=[call("news_tool", company_name="KODEX 레버리지")], subjects=[("KODEX 레버리지", "etf")],
                          final="## 주가 동향\nKODEX 레버리지의 주가가 최근 20% 급락했다. 20거래일간 27.21% 내렸습니다.\n"
                                "## 위험요인\n(이 섹션은 결과가 없으므로 빈칸입니다.)")
        resp, records = agent._answer("KODEX 레버리지 위험해?", "m")
        tool_msgs = [m["content"] for m in fake_agent["messages"] if m["role"] == "tool"]
        news_msg = next(t for t in tool_msgs if "news" in t)
        assert "20%" not in news_msg and "12.45%" not in news_msg and NUMBER_MASK in news_msg
        assert records[0]["result"]["news"][0]["title"].startswith("반도체 레버리지 20%")  # 기록·출처는 원문
        assert fake_agent["messages"][-1]["content"].endswith(ETF_ANSWER_RULES)
        answer = resp["answer"]
        # LLM이 기사 속 다른 상품의 수치를 이 ETF의 것으로 쓴 문장은 빠지고, 가격은 코드가 쓴 주가 동향, 기사는 제목 그대로 인용된다
        assert "KODEX 레버리지의 주가가 최근 20% 급락했다" not in answer and "빈칸" not in answer and "## 위험요인" not in answer
        assert answer.startswith("## 주가 동향\n" + STOCK_RESULT["summary"])
        assert "## 관련 뉴스\n- 반도체 레버리지 20% 급락…KODEX 레버리지는 12.45% 하락 (s, 2026-10-14)" in answer
        assert answer.rstrip().endswith("커질 수 있습니다.") and "KODEX 레버리지는 이름으로 보아 레버리지 ETF" in answer
        assert resp["sources"]  # 출처는 그대로

    def test_non_leveraged_etf_has_no_notice(self, fake_agent):
        fake_agent.update(first=[call("stock_tool", ticker="KODEX 200", period_days=1)], subjects=[("KODEX 200", "etf")],
                          final="KODEX 200의 20거래일 등락률은 -27.21%입니다.")
        resp, _ = agent._answer("KODEX 200 최근 흐름 알려줘", "m")
        assert "복리" not in resp["answer"] and "-27.21%" in resp["answer"]

    def test_everything_dropped_falls_back_to_price_summary(self, fake_agent):
        fake_agent.update(first=[call("stock_tool", ticker="KODEX 200", period_days=1)], subjects=[("KODEX 200", "etf")],
                          final="KODEX 200은 50% 올랐습니다.")
        resp, _ = agent._answer("KODEX 200 어때?", "m")
        assert resp["answer"].startswith("## 주가 동향\nKODEX 레버리지의 2026-10-14 종가는")  # 코드가 쓴 가격 섹션
        assert "50%" not in resp["answer"]

    def test_stock_question_path_is_unchanged(self, fake_agent):
        """주식만 나오는 질문: tool 덧붙이기·기간 변경·뉴스 가리기·ETF 규칙·후처리가 없다(빈 섹션 정리만 공통)."""
        fake_agent.update(first=[call("stock_tool", ticker="삼성전자", period_days=1)], subjects=[("삼성전자", "stock")],
                          final="삼성전자는 20% 올랐습니다.")
        resp, records = agent._answer("삼성전자 주가 알려줘", "m")
        assert [r["tool_name"] for r in records] == ["stock_tool"] and records[0]["arguments"]["period_days"] == 1
        assert fake_agent["messages"][-1]["content"] == agent.ANSWER_STRUCTURE_INSTRUCTION
        assert resp["answer"] == "삼성전자는 20% 올랐습니다."

    def test_stock_news_is_not_masked(self, fake_agent):
        fake_agent.update(first=[call("news_tool", company_name="삼성전자")], subjects=[("삼성전자", "stock")])
        agent._answer("삼성전자 뉴스", "m")
        assert "20%" in [m["content"] for m in fake_agent["messages"] if m["role"] == "tool"][-1]

    def test_stock_no_tool_answer_is_unchanged(self, fake_agent):
        fake_agent.update(first=[], subjects=[], final="x")
        resp, records = agent._answer("투자를 처음 시작하려면 뭐부터 해야 해?", "m")  # 개념 질문은 용어 사전 경로
        assert resp["answer"] == "개념 설명입니다." and records == []


class TestAddEtfToolsAndPeriod:
    def test_add_tools_only_missing(self, monkeypatch):
        monkeypatch.setattr(agent, "_called_companies", lambda calls, tool: {
            c["function"]["arguments"].get(agent._COMPANY_ARG[tool]) for c in calls if c["function"]["name"] == tool})
        calls = [call("news_tool", company_name="KODEX 200")]
        added = agent._add_etf_tools(calls, [("KODEX 200", "etf")])
        assert added == [1] and calls[1] == call("stock_tool", ticker="KODEX 200", period_days=20)
        assert agent._add_etf_tools(calls, [("KODEX 200", "etf")]) == []

    def test_etf_period_rules(self):
        calls = [call("stock_tool", ticker="A", period_days=1), call("stock_tool", ticker="B", period_days=30)]
        assert agent._apply_etf_period(calls, "A랑 B 비교") == {0: 1, 1: 30}  # 기간 언급 없음 -> 20
        calls = [call("stock_tool", ticker="A", period_days=22), call("stock_tool", ticker="B", period_days=30)]
        assert agent._apply_etf_period(calls, "A랑 B 한 달 비교") == {0: 22}  # 언급했지만 값이 다르면 긴 쪽
        calls = [call("stock_tool", ticker="A", period_days=30)]
        assert agent._apply_etf_period(calls, "A 한 달 흐름") == {}  # 언급했고 하나면 그대로
        assert agent._apply_etf_period([call("news_tool", company_name="A")], "A") == {}


# ---------------- 진단 ----------------

def H(ticker, name, market="KOSPI", qty=10, avg=1000.0, cur=1000.0, ret=None, asset_type="stock"):
    return HoldingInput(ticker, name, market, qty, avg, cur, True, ret, asset_type)


class TestDiagnosisEtf:
    def test_etf_only_has_no_market_skew_or_few_holdings(self):
        items, m = compute_portfolio(9_000_000.0, [H("069500", "KODEX 200", None, qty=10, cur=96_000.0, asset_type="etf")])
        flags = compute_flags(items, m)
        assert items[0]["market"] == "ETF" and items[0]["asset_type"] == "etf"
        assert m["market_weights_pct"] == {}
        assert {f["code"] for f in flags} == {"CASH_HIGH"}
        text = rule_based_text(items, m, flags)
        assert any("ETF가 1개 포함돼 있습니다" in s for s in text["strengths"])
        assert not any("나눠 보유하고 있습니다" in s for s in text["strengths"])

    def test_market_weights_use_stocks_only(self):
        items, m = compute_portfolio(0.0, [H("005930", "삼성전자", qty=30), H("069500", "KODEX 200", None, qty=70, asset_type="etf"),
                                          H("247540", "에코프로비엠", "KOSDAQ", qty=10)])
        assert m["market_weights_pct"] == {"KOSDAQ": 25.0, "KOSPI": 75.0}  # 주식 40,000 중(ETF 70,000 제외)
        assert "MARKET_SKEW" not in {f["code"] for f in compute_flags(items, m)}

    def test_stock_and_etf_mix_below_three_is_not_few_holdings(self):
        items, m = compute_portfolio(5000.0, [H("005930", "삼성전자"), H("069500", "KODEX 200", None, asset_type="etf")])
        assert "FEW_HOLDINGS" not in {f["code"] for f in compute_flags(items, m)}

    def test_leveraged_inverse_flag(self):
        items, m = compute_portfolio(70_000.0, [H("005930", "삼성전자"),
                                                H("122630", "KODEX 레버리지", None, qty=10, asset_type="etf"),
                                                H("114800", "KODEX 인버스", None, qty=10, asset_type="etf")])
        flags = [f for f in compute_flags(items, m) if f["code"] == "LEVERAGED_INVERSE_ETF"]
        assert [(f["ticker"], f["company_name"], f["value"], f["threshold"]) for f in flags] == [
            ("122630", "KODEX 레버리지", 10.0, 0.0), ("114800", "KODEX 인버스", 10.0, 0.0)]
        assert flags[0]["message"] == ("KODEX 레버리지는 레버리지 ETF로 총자산의 10.0%입니다. 하루 수익률을 기준으로 움직여 오래 "
                                       "보유하면 기초지수 수익률과 달라질 수 있습니다.")
        assert set(flags[0]) == {"code", "message", "value", "threshold", "ticker", "company_name"}
        text = rule_based_text(items, m, compute_flags(items, m))
        assert flags[0]["message"] in text["risks"]
        assert "레버리지·인버스 ETF는 보유 기간과 비중을 따로 점검해볼 수 있습니다." in text["suggestions"]

    def test_leverage_flag_without_price(self):
        items, m = compute_portfolio(1000.0, [H("122630", "KODEX 레버리지", None, cur=None, asset_type="etf")])
        (f,) = [f for f in compute_flags(items, m) if f["code"] == "LEVERAGED_INVERSE_ETF"]
        assert f["value"] == 0.0 and "총자산의" not in f["message"]

    def test_stock_named_like_leverage_is_not_flagged(self):
        items, m = compute_portfolio(1000.0, [H("000001", "레버리지주식", qty=1)])
        assert "LEVERAGED_INVERSE_ETF" not in {f["code"] for f in compute_flags(items, m)}

    def test_particles_in_sentences(self):
        items, m = compute_portfolio(1000.0, [H("069500", "KODEX 200", None, qty=1, ret=-13.85, asset_type="etf")])
        assert holding_fact(items[0]).startswith("KODEX 200은 총자산의")

    def test_notes_particle(self, monkeypatch):
        import app.diagnosis as d

        monkeypatch.setattr(d, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError()))
        inputs = [HoldingInput("069500", "KODEX 200", None, 1, 1000.0, 1000.0, False, None, "etf")]
        notes = build_diagnosis(1000.0, inputs)["notes"]
        assert notes[0].startswith("KODEX 200은 실시간 시세 대신") and notes[1].startswith("KODEX 200은 저장된 주가가 부족해")

    def test_stock_only_result_is_unchanged(self):
        """주식만 보유: 지표·플래그·문장이 ETF 도입 전과 같다(asset_type 필드만 추가)."""
        inputs = [H("005930", "삼성전자", qty=2, cur=263_000.0, avg=263_000.0, ret=-2.59),
                  H("035720", "카카오", qty=3, cur=32_400.0, avg=32_400.0, ret=-11.35),
                  H("247540", "에코프로비엠", "KOSDAQ", qty=5, cur=126_500.0, avg=126_500.0, ret=18.56)]
        items, m = compute_portfolio(8_744_300.0, inputs)
        flags = compute_flags(items, m)
        assert m["market_weights_pct"] == {"KOSDAQ": 50.37, "KOSPI": 49.63}
        assert [f["code"] for f in flags] == ["CASH_HIGH"]
        assert rule_based_text(items, m, flags)["strengths"] == [
            "상위 1종목 비중이 6.33%로 한 종목에 크게 쏠려 있지 않습니다.", "3개 종목에 나눠 보유하고 있습니다."]
        assert {i["market"] for i in items} == {"KOSPI", "KOSDAQ"} and {i["asset_type"] for i in items} == {"stock"}

    def test_few_holdings_still_applies_to_stock_only(self):
        items, m = compute_portfolio(5000.0, [H("005930", "삼성전자")])
        assert "FEW_HOLDINGS" in {f["code"] for f in compute_flags(items, m)}


class TestHistoryCompareNewFlag:
    def _result(self, holdings):
        items, m = compute_portfolio(80_000.0, holdings)
        flags = [{**f, "ticker": f.get("ticker"), "company_name": f.get("company_name"), "market": f.get("market")}
                 for f in compute_flags(items, m)]
        return {"metrics": m, "holdings": items, "flags": flags}

    def test_added_then_resolved(self):
        stock_only = self._result([H("005930", "삼성전자"), H("035720", "카카오"), H("000660", "SK하이닉스")])
        with_lev = self._result([H("005930", "삼성전자"), H("035720", "카카오"), H("000660", "SK하이닉스"),
                                 H("122630", "KODEX 레버리지", None, asset_type="etf")])
        added = compute_changes(with_lev, stock_only)["flags"]["added"]
        assert [(f["code"], f["ticker"]) for f in added] == [("LEVERAGED_INVERSE_ETF", "122630")]
        resolved = compute_changes(stock_only, with_lev)["flags"]["resolved"]
        assert [(f["code"], f["ticker"]) for f in resolved] == [("LEVERAGED_INVERSE_ETF", "122630")]

    def test_two_leveraged_etfs_are_two_flags(self):
        a = self._result([H("122630", "KODEX 레버리지", None, asset_type="etf")])
        b = self._result([H("122630", "KODEX 레버리지", None, asset_type="etf"), H("114800", "KODEX 인버스", None, asset_type="etf")])
        assert [(f["code"], f["ticker"]) for f in compute_changes(b, a)["flags"]["added"]] == [("LEVERAGED_INVERSE_ETF", "114800")]


# ---------------- 수집: ETF 뉴스 후보 ----------------

class TestEtfNewsCandidates:
    def test_merges_two_sorts_filters_by_name_and_sorts_latest_first(self, monkeypatch):
        def item(link, title, day):
            return {"link": link, "title": title, "description": "", "pubDate": f"Tue, {day} Oct 2026 09:00:00 +0900"}

        pages = {
            "date": [item("1", "<b>KODEX 200</b> 순매수", "13"), item("2", "ETF 시장 전반", "14"), item("3", "KODEX 200TR 분배", "14")],
            "sim": [item("1", "KODEX 200 순매수", "13"), item("4", "코덱스 200 거래대금", "10")],
        }
        monkeypatch.setattr(collectors, "fetch_naver_news", lambda q, display=10, sort="date": pages[sort])

        class S:
            def query(self, *a):
                return self

            def filter(self, *a):
                return [("KODEX 200TR",)]

            def close(self):
                pass

        monkeypatch.setattr(collectors, "SessionLocal", lambda: S())
        related, unrelated = collectors._etf_news_candidates("KODEX 200", 10)
        assert [it["link"] for it in related] == ["1", "4"] and unrelated == 2


# ---------------- 종목별 대조(2단계 프로브에서 실제로 나온 오류 문장) ----------------

from app.etf import check_etf_answer, comparison_sentence, compose_etf_answer  # noqa: E402

KODEX200 = {"company_name": "KODEX 200", "found": True, "change_pct": 1.18, "period_change_pct": -13.85, "trading_days": 20,
            "volume": 18117620, "period_start_date": "2026-09-10", "period_end_date": "2026-10-14",
            "summary": "KODEX 200의 2026-10-14 종가는 96,370원으로, 2026-09-10 종가 111,865원 대비 20거래일간 -13.85%입니다. "
                       "이 기간 종가 최고는 113,145원, 최저는 95,250원입니다. 거래량은 18,117,620주입니다."}
SAMSUNG = {"company_name": "삼성전자", "found": True, "change_pct": -2.23, "period_change_pct": -2.59, "trading_days": 20,
           "volume": 20157893, "period_start_date": "2026-09-07", "period_end_date": "2026-10-08",
           "summary": "삼성전자의 2026-10-08 종가는 263,000원으로, 2026-09-07 종가 270,000원 대비 20거래일간 -2.59%입니다. "
                      "거래량은 20,157,893주입니다."}
KQ150 = {"company_name": "KODEX 코스닥150레버리지", "found": True, "period_change_pct": -4.26, "trading_days": 20,
         "summary": "종가는 6,922원으로 20거래일간 -4.26%입니다. 거래량은 56,805,680주입니다."}
INVERSE = {"company_name": "KODEX 인버스", "found": True, "period_change_pct": 15.0, "trading_days": 20, "volume": 623319609,
           "summary": "KODEX 인버스의 종가는 1,127원으로 20거래일간 +15.00%입니다. 거래량은 623,319,609주입니다."}


class TestCheckEtfAnswer:
    def run(self, answer, *results):
        return check_etf_answer(answer, {r["company_name"]: r for r in results})

    @pytest.mark.parametrize("sentence", [
        "KODEX 200의 2026-10-14 종가는 96,370원으로, 2026-09-10 종가 111,865원 대비 20거래일간 -2.59%입니다.",  # 삼성전자의 등락률
        "KODEX 200의 20거래일 동안 평균 종가는 100,750원입니다.",                                       # 결과에 없는 계산값
        "KODEX 200는 20거래일간 거래량이 20,157,893주입니다.",                                         # 삼성전자의 거래량
    ])
    def test_number_of_another_subject_or_made_up_is_dropped(self, sentence):
        out, dropped = self.run(f"## 주가 동향\n{sentence} 삼성전자는 20거래일간 -2.59%입니다.", KODEX200, SAMSUNG)
        assert [d[0] for d in dropped] == [sentence] and "조회 결과에 없는 수치" in dropped[0][1]
        assert "삼성전자는 20거래일간 -2.59%입니다." in out

    def test_correct_sentences_stay(self):
        text = ("KODEX 200의 2026-10-14 종가는 96,370원으로, 2026-09-10 종가 111,865원 대비 20거래일간 -13.85%입니다. "
                "삼성전자의 종가는 263,000원입니다. 거래량은 18,117,620주입니다.")
        assert self.run(text, KODEX200, SAMSUNG) == (text, [])

    def test_wrong_direction_is_dropped(self):
        out, dropped = self.run("## 원인 분석\n코스닥 150 레버리지 ETF의 20거래일간 전반적인 추세는 상승하였습니다. "
                                "KODEX 코스닥150레버리지는 20거래일간 -4.26% 감소했습니다.", KQ150)
        assert [d[1] for d in dropped] == ["기간 등락 방향이 조회 결과와 반대"]
        assert "-4.26% 감소" in out and "상승하였습니다" not in out

    def test_volume_rewritten_as_won_is_dropped(self):
        for s in ("KODEX 인버스는 거래대금 약 623,319,609원으로 7위를 기록했다.", "거래대금은 약 6,233,196,090원으로 7위를 기록했다."):
            assert self.run(s, INVERSE)[1], s

    def test_unsourced_product_claim_is_dropped_but_news_attributed_is_kept(self):
        out, dropped = self.run("코스피 200 지수를 추종하는 KODEX 200의 20거래일간 등락률은 -13.85%입니다. "
                                "뉴스 기사에서는 이 ETF를 코스피 200지수를 추종하는 상품으로 설명하고 있습니다. "
                                "KODEX 200의 변동은 주로 코스피200 선물지수의 변동에 따라 움직입니다.", KODEX200)
        # 첫 문장은 "코스피 200 지수"에 수치가 붙어 시장 지수 규칙에 먼저 걸린다(어느 쪽이든 지워진다)
        assert [d[1] for d in dropped] == ["시장 지수에 수치를 붙임", "출처 없는 상품 설명"]
        assert out == "뉴스 기사에서는 이 ETF를 코스피 200지수를 추종하는 상품으로 설명하고 있습니다."

    def test_ellipsis_is_not_a_sentence_break(self):
        text = "미국 증시 선전에도 서학개미 원화 강세에 '우울', 환노출형 ETF 수익률... 등이 전해졌습니다."
        assert self.run(text, KODEX200) == (text, [])

    def test_small_week_counts_are_not_volumes(self):
        assert self.run("최근 2주 동안 KODEX 200의 20거래일 등락률은 -13.85%입니다.", KODEX200)[1] == []


class TestComparisonSentence:
    def test_two_subjects_with_different_end_dates(self):
        s = comparison_sentence([KODEX200, SAMSUNG])
        assert s == ("※ 조회 결과로 계산한 비교: KODEX 200은 2026-09-10~2026-10-14 20거래일간 -13.85%, 삼성전자는 "
                     "2026-09-07~2026-10-08 20거래일간 -2.59%입니다. 등락률은 삼성전자가 더 높습니다. 다만 종목마다 저장된 마지막 "
                     "거래일이 달라 같은 날짜 구간의 비교는 아닙니다.")

    def test_same_end_date_has_no_caveat_and_single_is_none(self):
        other = {**SAMSUNG, "period_end_date": "2026-10-14"}
        assert "마지막 거래일이 달라" not in comparison_sentence([KODEX200, other])
        assert comparison_sentence([KODEX200]) is None
        assert comparison_sentence([KODEX200, {"found": False}]) is None


class TestPlaceholdersSeenInProbes:
    @pytest.mark.parametrize("body", ["없음.", "최근 관련 뉴스는 없습니다."])
    def test_removed(self, body):
        out = remove_empty_sections(f"## 주가 동향\n종가는 1,000원입니다.\n\n## 위험요인\n{body}")
        assert out == "## 주가 동향\n종가는 1,000원입니다."


class TestComposeAndStricterChecks:
    """2단계 재측정에서 나온 오류: 다른 상품 기사를 이 ETF 것으로 씀, 질문에 없는 종목에 수치를 붙임, 조회한 주가를 없다고 함,
    가린 수치 자리 노출, 한글 단위 수치가 덜 가려짐, 레버리지 구조를 거꾸로 설명."""

    def test_other_product_after_name_is_not_this_etf(self):
        assert mentions_etf_name("미래에셋 TIGER 미국S&P500타겟데일리커버드콜 10월 분배금", "TIGER 미국S&P500") is False
        assert mentions_etf_name("TIGER 미국S&P500은 순매수 1위", "TIGER 미국S&P500") is True
        assert mentions_etf_name("TIGER 미국S&P500 ETF 인기", "TIGER 미국S&P500") is True
        assert mentions_etf_name("TIGER 미국S&P500ETF 인기", "TIGER 미국S&P500") is True
        assert mentions_etf_name("KODEX 레버리지가 하락", "KODEX 레버리지") is True
        assert mentions_etf_name("KODEX 레버리지인버스 혼동", "KODEX 레버리지") is False

    def test_mask_covers_korean_units_and_ranks(self):
        masked = mask_news_numbers("거래대금은 약 9천억원으로 8위, 순매수 1조2000억원, 10월 분배금")
        assert not any(ch.isdigit() for ch in masked) and "천억" not in masked

    def test_number_on_a_company_not_in_question_is_dropped(self):
        tiger = {"company_name": "TIGER 200", "found": True, "period_change_pct": -13.77, "trading_days": 20,
                 "summary": "종가 최고는 113,360원, 최저는 95,610원, 20거래일간 -13.77%, 거래량은 9,382,453주입니다."}
        out, dropped = check_etf_answer("삼성전자는 113,360원에서 95,610원까지 하락했고, 거래량은 9,382,453주입니다. "
                                        "TIGER 200은 20거래일간 -13.77%입니다.", {"TIGER 200": tiger}, ["삼성전자", "TIGER 200"])
        assert [d[1] for d in dropped] == ["질문에 없는 종목에 수치를 붙임"] and out == "TIGER 200은 20거래일간 -13.77%입니다."

    def test_subject_name_containing_another_name_is_not_flagged(self):
        r = {"company_name": "KODEX 200TR", "found": True, "period_change_pct": -11.46, "trading_days": 58,
             "summary": "58거래일간 -11.46%"}
        assert check_etf_answer("KODEX 200TR은 58거래일간 -11.46%입니다.", {"KODEX 200TR": r}, ["KODEX 200", "KODEX 200TR"])[1] == []

    def test_mask_placeholder_and_denial_are_dropped(self):
        out, dropped = check_etf_answer("KODEX 200TR의 최근 주가는 조회하지 못했습니다. 순매수는 [수치 생략]원입니다. 기사 제목이 있습니다.",
                                        {"KODEX 200TR": {"company_name": "KODEX 200TR", "found": True}})
        assert [d[1] for d in dropped] == ["조회한 주가를 없다고 함", "가린 수치 자리"] and out == "기사 제목이 있습니다."

    def test_reversed_structure_claim_is_dropped(self):
        text = "KODEX 레버리지는 레버리지 상품으로, 주가가 하락할 때도 수익을 내도록 설계된 펀드 상품입니다."
        assert check_etf_answer(text, {})[1] == [(text, "출처 없는 상품 설명")]

    def test_compose_replaces_price_and_news_sections_and_keeps_the_rest(self):
        news = {"found": True, "news": [{"title": "KODEX 200 순매수 1위", "source": "a.com", "published_at": "2026-10-13T09:00:00"}]}
        llm = "## 주가 동향\nLLM이 쓴 가격 문장\n\n## 뉴스·공시 근거\nLLM이 옮긴 뉴스\n\n## 위험요인\n하락 폭이 큽니다."
        out = compose_etf_answer(llm, [KODEX200], [news, {"found": False}])
        assert out == ("## 주가 동향\n" + KODEX200["summary"] + "\n\n## 관련 뉴스\n- KODEX 200 순매수 1위 (a.com, 2026-10-13)"
                       "\n\n## 위험요인\n하락 폭이 큽니다.")

    def test_compose_without_data_keeps_llm_text(self):
        assert compose_etf_answer("요약 문장입니다.", [], []) == "요약 문장입니다."


class TestMarketAndKoreanNumbers:
    """최종 프로브에서 남았던 두 오류."""

    def test_number_attached_to_market_index_is_dropped(self):
        out, dropped = check_etf_answer("## 시장 상황\n이 기간 동안 코스피는 -4.26%로 하락했습니다.", {"KODEX 코스닥150레버리지": KQ150})
        assert [d[1] for d in dropped] == ["시장 지수에 수치를 붙임"] and out == "## 시장 상황\n"

    def test_index_word_inside_the_subject_name_is_fine(self):
        text = "KODEX 코스닥150레버리지는 20거래일간 -4.26% 하락했습니다."
        assert check_etf_answer(text, {"KODEX 코스닥150레버리지": KQ150}) == (text, [])

    def test_korean_unit_number_is_dropped(self):
        out, dropped = check_etf_answer("ETF는 2일간의 거래량이 1만 8천만 주로 기록되었다.", {"KODEX 200": KODEX200})
        assert [d[1] for d in dropped] == ["조회 결과에 없는 한글 단위 수치"]

    def test_cannot_analyse_filler_section_is_removed(self):
        out = remove_empty_sections("## 주가 동향\n종가는 1,000원입니다.\n\n## 시장 상황\n현재 시장 상황을 분석할 수 없습니다.")
        assert out == "## 주가 동향\n종가는 1,000원입니다."


class TestFinalProbeResidue:
    """최종 프로브(30회)에서 남았던 문장들."""

    TIGER200 = {"company_name": "TIGER 200", "found": True, "period_change_pct": -13.77, "trading_days": 20, "volume": 9382453,
                "summary": "종가 최고는 113,360원, 최저는 95,610원입니다. 거래량은 9,382,453주입니다."}

    def test_index_direction_without_data_is_dropped(self):
        text = "코스피 지수와 KODEX 200의 상관관계를 분석할 때, 코스피 지수는 상승했지만, KODEX 200의 주가는 하락했습니다."
        assert check_etf_answer(text, {"KODEX 200": KODEX200})[1] == [(text, "시장 지수 등락을 근거 없이 단정")]

    def test_index_movement_from_news_is_kept(self):
        text = "뉴스 기사에서 설명하는 바와 같이, 코스닥과 반도체 대형주가 약세를 보였습니다."
        assert check_etf_answer(text, {"KODEX 코스닥150레버리지": KQ150})[1] == []

    def test_trading_value_and_unsupported_extreme_are_dropped(self):
        a = "TIGER 200의 경우 거래대금이 약 9,382,453주로 2026-10-14에 20거래일간 최고를 기록했습니다."
        b = "거래량도 20거래일간 최고를 기록했습니다."
        out, dropped = check_etf_answer(f"{a} {b}", {"TIGER 200": self.TIGER200})
        assert [d[1] for d in dropped] == ["수집하지 않는 거래대금 수치", "근거 없는 최고·최저 주장"] and out == ""

    def test_extreme_with_price_from_result_is_kept(self):
        text = "TIGER 200의 이 기간 종가 최고는 113,360원입니다."
        assert check_etf_answer(text, {"TIGER 200": self.TIGER200}) == (text, [])
