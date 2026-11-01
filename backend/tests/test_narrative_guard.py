"""설명형 문장 검증(app/answer_guard.strip_narrative_mismatch). DB·Ollama 없이 돈다."""

import pytest

from app import answer_guard as G

PRICES = {"tool_name": "stock_tool", "arguments": {}, "result": {
    "found": True, "company_name": "기아", "summary": "기아의 2026-10-30 종가는 70,900원입니다.", "change_pct": -1.0, "period_change_pct": -2.0,
    "prices": [{"price_date": "2026-10-27", "close_price": 92200.0, "change_pct": 0.5, "volume": 1000},
               {"price_date": "2026-10-28", "close_price": 89800.0, "change_pct": -2.6, "volume": 2000},
               {"price_date": "2026-10-29", "close_price": 71600.0, "change_pct": -20.27, "volume": 3000},
               {"price_date": "2026-10-30", "close_price": 70900.0, "change_pct": -0.98, "volume": 4000}]}}
NEWS = {"tool_name": "news_tool", "arguments": {}, "result": {"found": True, "company_name": "LG에너지솔루션", "news": [
    {"title": "LG에너지솔루션 3분기 영업이익 7560억원", "content": "LG에너지솔루션은 3분기 매출 9조6434억원, 영업이익 7560억원을 기록했다.", "url": "https://a/1"},
    {"title": "태성 주가, 9월 22일 장중 47,600원 4.62% 상승", "content": "", "url": "https://a/2"}]}}
TAESUNG = {"tool_name": "news_tool", "arguments": {}, "result": {"found": True, "company_name": "태성", "news": [
    {"title": "태성자산운용 제재", "content": "태성자산운용이 과징금·과태료 총 6100만원의 제재를 받았다.", "url": "https://a/3"},
    {"title": "태성 주가, 9월 22일 장중 47,600원 4.62% 상승", "content": "태성은 PCB 장비 업체다.", "url": "https://a/4"}]}}
OTHER_DOC = {"tool_name": "news_tool", "arguments": {}, "result": {"found": True, "company_name": "기아", "news": [
    {"title": "현대모비스 실적", "content": "현대모비스 영업이익 8500억원", "url": "https://a/5"}]}}
ETF = {"tool_name": "stock_tool", "arguments": {}, "result": {
    "found": True, "company_name": "KODEX 레버리지", "summary": "KODEX 레버리지의 2026-10-30 종가는 72,545원입니다.",
    "prices": [{"price_date": "2026-10-30", "close_price": 72545.0, "change_pct": -7.64, "volume": 23223639}]}}


def strip(answer, records, question=""):
    return G.strip_narrative_mismatch(answer, records, question)


# ---------- 지우는 문장 ----------


@pytest.mark.parametrize("answer, records, reason", [
    ("태성자산운용이 과징금·과태료 총 6100만원의 제재를 받았다.", [TAESUNG], "other_company"),
    ("태성의 최근 뉴스에 나타난 사실은 태성자산운용이 총 6100만원의 제재를 받았다는 점입니다.", [TAESUNG], "other_company"),
    ("또한, 태성의 주가는 장중 4.62% 상승하며 47,600원을 기록하였습니다.", [TAESUNG], "undated_article_quote"),
    ("KODEX 레버리지 ETF에 들어온 자금은 2026-10-30일 23,223,639주입니다.", [ETF], "money_as_shares"),
    ("기아의 거래량은 4,000 건으로 기록되었습니다.", [PRICES], "volume_unit"),
    ("LG에너지솔루션의 주가는 최근 7560억원의 영업이익을 보였다.", [NEWS], "financial_in_price"),
    ("기아의 영업이익은 8500억원입니다.", [PRICES, OTHER_DOC], "other_company_value"),
    ("기아는 10월 30일 2,400원 하락한 89,800원에 거래를 마쳤습니다.", [PRICES], "change_pair"),  # 그날 종가는 70,900원
    ("기아는 2,400원 하락한 71,600원에 거래를 마쳤습니다.", [PRICES], "change_pair"),  # 71,600원의 직전 행 차이는 18,200원
    ("기아는 10월 27일 대비 700원 하락한 70,900원입니다.", [PRICES], "change_pair"),  # 기준 차이는 21,300원
    ("주가는 47,600원으로 거래되고 있습니다.", [TAESUNG], "undated_article_quote"),
    ("최근 주가가 47,600원에 거래되고 있습니다.", [TAESUNG], "undated_article_quote"),
    ("등락률은 -4.62%입니다.", [TAESUNG], "undated_article_quote"),
    ("또한, 이ETF의 종목 중 일부를 매도하세요.", [ETF], "direct_order"),
    ("지금은 매수 적기입니다.", [], "direct_order"),
])
def test_mismatched_sentence_is_dropped(answer, records, reason):
    out, reasons = strip(answer, records)
    assert out == ""
    assert reasons and reasons[0].startswith(reason)


def test_only_the_mismatched_sentence_is_dropped():
    answer = "## 주가 동향\n태성은 PCB 장비 업체입니다. 태성의 주가는 장중 4.62% 상승했습니다.\n* 태성자산운용이 제재를 받았다.\n\n## 정리\n뉴스를 더 확인해 보세요."
    out, reasons = strip(answer, [TAESUNG])
    assert "PCB" in out and "뉴스를 더" in out
    assert "4.62%" not in out and "자산운용" not in out
    assert len(reasons) == 2


def test_list_marker_is_not_left_behind():
    out, reasons = strip("1. 태성자산운용이 제재를 받았다.", [TAESUNG])
    assert out == "" and reasons


# ---------- 통과해야 하는 문장 ----------


@pytest.mark.parametrize("answer, records, question", [
    # 시세 행의 값과 맞는 짝
    ("기아는 10월 28일 2,400원 하락한 89,800원에 거래를 마쳤습니다.", [PRICES], ""),
    ("기아는 700원 하락한 70,900원에 거래를 마쳤습니다.", [PRICES], ""),
    ("기아는 10월 27일 대비 21,300원 하락한 70,900원입니다.", [PRICES], ""),
    ("기아의 2026-10-30 종가는 70,900원으로, 2026-10-27 종가 92,200원 대비 3거래일간 -23.1%입니다.", [PRICES], ""),
    ("기아의 거래량은 4,000주입니다.", [PRICES], ""),
    # 수준이 시세 행이 아니면(기사 값) 짝을 보지 않는다
    ("태성은 전일 대비 400원 상승한 47,600원이었다고 9월 22일 기사가 전했습니다.", [TAESUNG], ""),
    # 날짜를 함께 쓴 기사 속 시세
    ("태성의 주가는 9월 22일 장중 4.62% 상승하며 47,600원을 기록했습니다.", [TAESUNG], ""),
    # 같은 회사 재무 숫자, 주가가 주어가 아닌 문장
    ("LG에너지솔루션은 3분기 영업이익 7560억원을 기록했습니다.", [NEWS], ""),
    ("LG에너지솔루션은 영업이익 7560억원 발표 뒤 주가는 하락했습니다.", [NEWS], ""),
    ("LG에너지솔루션의 주가는 영업이익 7560억원 발표 영향을 받았습니다.", [NEWS], ""),
    # 사용자가 물은 이름, 숫자 없는 일반 문장
    ("태성자산운용이 제재를 받았습니다.", [TAESUNG], "태성자산운용 제재 내용 알려줘"),
    ("태성은 PCB 장비 업체입니다.", [TAESUNG], ""),
    ("기아 주가는 최근 하락 흐름입니다.", [PRICES], ""),
    # 다른 대상의 기사 수치
    ("구글 이어 인텔 28% 폭락으로 AI 투자 불안이 커졌습니다.", [TAESUNG], ""),
    ("인텔 주가는 28% 폭락했습니다.", [TAESUNG], ""),
    # 이름이 이어진 다른 종목의 기사 값(grd_13), 계열사 소식
    ("기아글로벌은 10월 30일 전일 대비 2,200원 하락한 89,800원에 거래를 마쳤습니다.", [PRICES], ""),
    ("기아파트너스 등이 신규 투자자로 참여했습니다.", [PRICES], ""),
    # 판단을 넘기는 표현
    ("사야 할지 팔아야 할지는 본인이 판단할 일입니다.", [PRICES], ""),
])
def test_normal_sentence_passes(answer, records, question):
    assert strip(answer, records, question) == (answer, [])


def test_no_records_has_no_names_to_compare():
    answer = "태성자산운용이 제재를 받았다."
    assert strip(answer, []) == (answer, [])


def test_direct_order_rule_matches_the_evaluator():
    from evals import metrics as M

    assert G._DIRECT_ORDER_RE.pattern == M._FORBIDDEN_RE.pattern


def test_thin_body_detection():
    assert G.narrative_body_is_thin("## 주가 동향\n짧다.\n※ 안내 문구는 본문으로 세지 않습니다. 길게 써도 그렇습니다. 정말로 그렇습니다.")
    assert not G.narrative_body_is_thin("## 원인 분석\n" + "반도체 업황 둔화 우려가 뉴스에서 반복되고 있으며 투자 심리에 영향을 준 것으로 보입니다.")


# ---------- agent._guard 연결 ----------


def test_agent_guard_drops_sentence_and_records_reason():
    from app import agent

    agent._reset_llm_state()
    body = "태성은 PCB 장비를 만드는 회사이고 임시주주총회 소식과 공급계약 체결 소식이 최근 뉴스에 나왔습니다."
    out = agent._guard(body + " 태성의 주가는 장중 4.62% 상승했습니다.", [TAESUNG], "태성은 어떤 회사야?")
    log = agent._meta()["answer_guard"]
    assert out == body
    assert log["replaced"] is False and log["narrative_sentences"] == 1
    assert log["narrative_reasons"][0].startswith("undated_article_quote")
    assert "4.62%" in log["narrative_original"]


def test_agent_guard_uses_code_summary_when_too_little_remains():
    from app import agent

    agent._reset_llm_state()
    out = agent._guard("태성자산운용이 총 6100만원의 제재를 받았다.", [TAESUNG], "태성 리스크 알려줘")
    assert "## 뉴스·공시 근거" in out and "태성 주가" in out
    assert agent._meta()["answer_guard"]["narrative_sentences"] == 1


def test_agent_guard_leaves_normal_answer_unchanged():
    from app import agent

    agent._reset_llm_state()
    answer = "## 주가 동향\n기아의 2026-10-30 종가는 70,900원입니다.\n\n## 원인 분석\n뉴스에서 뚜렷한 원인은 확인되지 않습니다."
    assert agent._guard(answer, [PRICES], "기아 왜 떨어졌어?") == answer
    assert agent._meta()["answer_guard"]["narrative_sentences"] == 0


