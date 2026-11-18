"""49차 최우선: 뉴스 기사 속 과거 가격을 현재 주가 설명과 분리한다(주식·ETF 같은 원칙).

수정 전 실측(48차 holdout D4, 2026-11-13, llama3.1:8b): "LG電子の最近の状況を分析して"의 한국어 답 "## 주가 동향"이
기사(2026-10-30)의 마감가를 현재 시세처럼 썼고, 끝에는 DB 기준일 줄(2026-11-12)이 붙었다.
  "LG전자 주가는 최근 10일간 16만원대에서 하락세를 기록했다. 30일 KRX 마감 기준, LG전자 주가가 147,800원으로 거래를 마쳤다.
   이날 주가는 전일 대비 7,400원 하락하며 4.77%의 낙폭을 기록, 이틀 연속 하락세를 보였다."
숫자 검증(answer_guard)은 기사 본문에 있는 값이라 통과시켰고, 기존 "날짜 없는 기사 시세" 규칙은 "30일"을 날짜로 보고 통과시켰다.
"""

import copy

import pytest

from app import agent
from app.answer_guard import strip_article_prices
from app.etf import news_result_for_llm

DB_SUMMARY = ("LG전자의 2026-11-12 종가는 152,300원으로, 2026-10-15 종가 171,000원 대비 20거래일간 -10.94%입니다. "
              "이 기간 종가 최고는 175,000원(2026-10-17), 최저는 147,800원(2026-10-30)입니다.")


def stock_rec(name="LG전자", close=152300.0, summary=DB_SUMMARY):
    prices = [{"price_date": "2026-10-30", "close_price": 147800.0, "change_pct": -4.77, "volume": 900000},
              {"price_date": "2026-11-11", "close_price": 150000.0, "change_pct": 0.5, "volume": 500000},
              {"price_date": "2026-11-12", "close_price": close, "change_pct": 1.53, "volume": 612345}]
    return {"tool_name": "stock_tool", "arguments": {"ticker": name, "period_days": 20},
            "result": {"company_name": name, "ticker": "066570", "found": True, "prices": prices, "summary": summary,
                       "period_end_date": "2026-11-12", "change_pct": 1.53, "volume": 612345}}


ARTICLE = ("LG전자 주가는 최근 10일간 16만원대에서 하락세를 기록했다. 30일 KRX 마감 기준, LG전자 주가가 146,900원으로 거래를 마쳤다. "
           "이날 주가는 전일 대비 7,300원 하락하며 4.73%의 낙폭을 기록했다. 3분기 매출은 21조 8,751억원, 영업이익은 6,889억원으로 "
           "전년 대비 영업이익이 8.4% 줄었다.")


def news_rec(published="2026-10-30T16:10:00+09:00", content=ARTICLE, name="LG전자"):
    return {"tool_name": "news_tool", "arguments": {"company_name": name},
            "result": {"company_name": name, "found": True,
                       "news": [{"title": f"{name}, 이틀 연속 하락…146,900원 마감", "content": content, "published_at": published,
                                 "url": "https://news.example/lg-1030", "source": "예시일보", "company_name": name}]}}


D4 = ("## 주가 동향\nLG전자 주가는 최근 10일간 16만원대에서 하락세를 기록했다. 30일 KRX 마감 기준, LG전자 주가가 146,900원으로 거래를 마쳤다. "
      "이날 주가는 전일 대비 7,300원 하락하며 4.73%의 낙폭을 기록, 이틀 연속 하락세를 보였다.\n\n"
      "## 뉴스·공시 근거\nLG전자는 3분기 매출 21조 8,751억원, 영업이익 6,889억원을 기록했다.")


class TestStripArticlePrices:
    def test_article_prices_are_removed_from_the_current_price_section(self):
        out, reasons = strip_article_prices(D4, [stock_rec(), news_rec()])
        section = out.split("## 뉴스·공시 근거")[0]
        for v in ("146,900", "7,300", "4.73%", "16만원"):
            assert v not in section, v
        assert reasons and all(r.startswith("article_price") for r in reasons)

    def test_empty_current_section_is_filled_with_the_db_summary(self):
        out, _ = strip_article_prices(D4, [stock_rec(), news_rec()])
        assert out.startswith("## 주가 동향\n" + DB_SUMMARY)
        assert "152,300원" in out  # 현재 시세는 DB 값

    def test_financial_figures_from_the_article_are_kept(self):
        out, _ = strip_article_prices(D4, [stock_rec(), news_rec()])
        assert "매출 21조 8,751억원, 영업이익 6,889억원" in out
        out2, r2 = strip_article_prices("3분기 영업이익이 전년 대비 8.4% 줄었다는 기사가 있습니다.", [stock_rec(), news_rec()])
        assert "8.4%" in out2 and r2 == []

    def test_dated_historical_price_outside_the_current_section_is_kept(self):
        text = "## 원인 분석\n2026-10-30 기사에 따르면 그날 LG전자는 146,900원에 마감했습니다."
        out, reasons = strip_article_prices(text, [stock_rec(), news_rec()])
        assert out == text and reasons == []
        korean_date = "## 원인 분석\n10월 30일 기사에 따르면 LG전자 주가는 146,900원으로 마감했습니다."
        assert strip_article_prices(korean_date, [stock_rec(), news_rec()])[0] == korean_date

    def test_day_only_or_other_date_is_not_the_article_date(self):
        for text in ("## 원인 분석\n30일 LG전자 주가는 146,900원으로 마감했다.", "## 원인 분석\n11월 12일 LG전자 주가는 146,900원이었다."):
            out, reasons = strip_article_prices(text, [stock_rec(), news_rec()])
            assert "146,900" not in out and reasons

    def test_article_without_a_date_is_never_used_for_prices(self):
        text = "## 원인 분석\n2026-10-30 기사에 따르면 LG전자는 146,900원에 마감했습니다."
        out, reasons = strip_article_prices(text, [stock_rec(), news_rec(published=None)])
        assert "146,900" not in out and reasons

    def test_db_supported_values_are_untouched(self):
        text = "## 주가 동향\nLG전자의 2026-11-12 종가는 152,300원이고 하루 등락률은 +1.53%입니다. 2026-10-30에는 147,800원까지 내려갔습니다."
        assert strip_article_prices(text, [stock_rec(), news_rec()]) == (text, [])

    def test_mixed_sentence_with_current_and_article_price_is_removed(self):
        text = "## 주가 동향\nLG전자는 2026-11-12 종가 152,300원으로, 기사에 나온 146,900원보다 올랐습니다."
        out, reasons = strip_article_prices(text, [stock_rec(), news_rec()])
        assert "146,900" not in out and reasons

    def test_etf_follows_the_same_rule(self):
        etf_news = news_rec(name="KODEX 레버리지", content="KODEX 레버리지는 30일 70,635원으로 7.88% 상승 마감했다.")
        etf_stock = stock_rec(name="KODEX 레버리지", close=61550.0, summary="KODEX 레버리지의 2026-11-12 종가는 61,550원입니다.")
        text = "## 주가 동향\nKODEX 레버리지는 70,635원으로 7.88% 상승했습니다."
        out, reasons = strip_article_prices(text, [etf_stock, etf_news])
        assert "70,635" not in out and "61,550원" in out and reasons

    def test_no_news_no_change(self):
        assert strip_article_prices(D4, [stock_rec()])[1] == []


class TestNewsForLlm:
    def test_stock_news_marks_price_numbers_with_the_article_date_and_keeps_financials(self):
        rec = news_rec()
        original = copy.deepcopy(rec["result"])
        out = news_result_for_llm(rec["result"], etf=False)
        body = out["news"][0]["content"]
        assert "146,900원[기사 2026-10-30 시점]" in body and "4.73%[기사 2026-10-30 시점]" in body
        assert "매출은 21조 8,751억원" in body and "영업이익은 6,889억원" in body and "8.4% 줄었다" in body  # 실적 수치는 그대로
        assert rec["result"] == original  # 원본(기록·출처)은 바뀌지 않는다
        assert out["news"][0]["url"] == original["news"][0]["url"]

    def test_undated_article_prices_are_masked(self):
        out = news_result_for_llm(news_rec(published=None)["result"], etf=False)
        assert "146,900" not in out["news"][0]["content"] and "날짜 미상" in out["news"][0]["content"]

    def test_etf_keeps_masking_all_numbers(self):
        out = news_result_for_llm(news_rec(name="KODEX 레버리지")["result"])
        assert "146,900" not in out["news"][0]["content"] and "21조" not in out["news"][0]["content"]


def test_agent_guard_applies_it_to_stock_answers(monkeypatch):
    agent._reset_llm_state()
    out = agent._guard(D4, [stock_rec(), news_rec()], "LG전자 최근 상황 분석해줘")
    section = out.split("## 뉴스·공시 근거")[0]
    assert "146,900" not in section and "152,300원" in section
    assert agent._meta()["answer_guard"]["article_price_sentences"] >= 3


def test_sources_still_link_the_article(monkeypatch):
    from app.sources import build_sources

    recs = [stock_rec(), news_rec()]
    strip_article_prices(D4, recs)
    srcs = build_sources((r["tool_name"], r["result"]) for r in recs)
    assert [s["url"] for s in srcs if s["type"] == "news"] == ["https://news.example/lg-1030"]


@pytest.mark.parametrize("text", [
    "## 주가 동향\nLG전자의 2026-11-12 종가는 152,300원입니다.",
    "LG전자는 3분기 매출 21조 8,751억원을 기록했다는 보도가 2026-10-30에 나왔습니다.",
])
def test_clean_answers_unchanged(text):
    assert strip_article_prices(text, [stock_rec(), news_rec()]) == (text, [])


def test_quoted_article_title_is_a_citation_not_a_price_claim():
    text = "## 뉴스·공시 근거\n- LG전자, 이틀 연속 하락…146,900원 마감 (2026-10-30)"
    assert strip_article_prices(text, [stock_rec(), news_rec()]) == (text, [])
