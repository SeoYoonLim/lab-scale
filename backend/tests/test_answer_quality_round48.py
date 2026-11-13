"""48차 문제 2·3: 종목 분석 답의 기준일 누락 방지, 근거 없는 일반론·반복 문구 제거.

수정 전 실측(2026-11-13, llama3.1:8b, dev DB 읽기 전용) — 아래 SAMPLE_* 는 실제 답 원문(일부)이다.
- 기준일 누락: "셀트리온의 주가는 최근 1일 동안 1.21% 상승하였습니다."(d07), g01은 20거래일 수치만 있고 마지막 데이터 날짜가 없다.
- 분석 질문인데 주가를 조회하지 않음: "삼성전자 분석해줘" -> news_tool만(d01).
- 일반론: "주가는 뉴스와 공시를 통해 변동합니다."(g01), "…수많은 요인에 의해 영향을 받습니다", "disclosure_tool 결과에 있습니다"(d05).
- 반복: "## 원인 분석\n거래량은 21,638,160주로 마지막 거래일(2026-11-12) 하루 거래량은 21,638,160주입니다."(e01, 앞 절과 같은 값만 되풀이)
- 빈 제목: "## 관련 뉴스"만 남음(e09).
"""

from datetime import datetime, timezone

import pytest

from app import answer_basis, answer_tidy
from app.routing import is_analysis_question

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)  # 19:00 KST, 11-13 종가 확정 전이면 기대 거래일은 11-12


def stock_record(name="셀트리온", end="2026-11-12", change=1.21):
    prices = [{"price_date": "2026-11-11", "close_price": 200000.0, "change_pct": 0.5, "volume": 500000},
              {"price_date": end, "close_price": 202420.0, "change_pct": change, "volume": 569041}]
    return {"tool_name": "stock_tool", "arguments": {"ticker": name},
            "result": {"ticker": "068270", "company_name": name, "found": True, "period_days": 2, "prices": prices,
                       "period_end_date": end, "change_pct": change, "volume": 569041}}


def news_record(dates=("2026-11-03T09:00:00+09:00", "2026-10-31T15:00:00+09:00")):
    return {"tool_name": "news_tool", "arguments": {"company_name": "LG에너지솔루션"},
            "result": {"company_name": "LG에너지솔루션", "found": True,
                       "news": [{"title": f"기사 {i}", "published_at": d, "url": f"https://n.example/{i}"} for i, d in enumerate(dates)]}}


# ---------- 문제 2: 기준일 ----------

class TestBaseDate:
    def test_price_answer_without_date_gets_the_standard_basis_line(self):
        ans = "## 주가 동향\n셀트리온의 주가는 최근 1일 동안 1.21% 상승하였습니다."
        out = answer_basis.ensure_basis(ans, [stock_record()], NOW)
        assert out.startswith(ans) and "데이터 기준일: 2026-11-12 종가" in out

    @pytest.mark.parametrize("ans", [
        "셀트리온의 2026-11-12 종가는 202,420원입니다.",
        "셀트리온의 주가는 2026년 11월 12일 기준으로 202,420원입니다.",
        "셀트리온은 11월 12일 1.21% 올랐습니다.",
        "셀트리온은 2026.11.12 기준 202,420원입니다.",
    ])
    def test_answer_that_states_the_date_is_unchanged(self, ans):
        assert answer_basis.ensure_basis(ans, [stock_record()], NOW) == ans

    def test_a_different_date_does_not_count(self):
        ans = "셀트리온은 2026-11-05에 최고가를 기록했고 1.21% 올랐습니다."
        assert "데이터 기준일: 2026-11-12" in answer_basis.ensure_basis(ans, [stock_record()], NOW)

    def test_two_companies_with_different_dates_get_named_lines(self):
        recs = [stock_record("삼성전자", "2026-11-12"), stock_record("셀트리온", "2026-11-10")]
        out = answer_basis.ensure_basis("둘 다 올랐습니다.", recs, NOW)
        assert "삼성전자" in out.split("둘 다 올랐습니다.")[1] and "2026-11-12" in out and "2026-11-10" in out

    def test_news_only_answer_without_any_date_gets_the_news_period(self):
        out = answer_basis.ensure_basis("## 뉴스·공시 근거\n- 기사 0\n- 기사 1", [news_record()], NOW)
        assert "2026-10-31~2026-11-03" in out

    def test_news_answer_with_dates_is_unchanged(self):
        ans = "- 기사 0 (2026-11-03)\n- 기사 1 (2026-10-31)"
        assert answer_basis.ensure_basis(ans, [news_record()], NOW) == ans

    def test_no_tool_results_no_line(self):
        assert answer_basis.ensure_basis("PER은 주가수익비율입니다.", [], NOW) == "PER은 주가수익비율입니다."

    def test_not_found_stock_result_is_ignored(self):
        rec = {"tool_name": "stock_tool", "result": {"found": False, "message": "없음"}}
        assert answer_basis.ensure_basis("찾지 못했습니다.", [rec], NOW) == "찾지 못했습니다."

    @pytest.mark.parametrize("q", ["삼성전자 분석해줘", "SK하이닉스 지금 어때?", "카카오 투자 포인트는?", "Analyze 현대차 stock",
                                   "How is Samsung Electronics doing?", "기아の株価を分析して", "分析一下셀트리온的股票", "三星电子怎么样？"])
    def test_analysis_questions(self, q):
        assert is_analysis_question(q)

    @pytest.mark.parametrize("q", ["LG에너지솔루션 최근 뉴스 요약해줘", "삼성전자 공시 알려줘", "PER이 뭐야?", "최근 뉴스 정리해줘"])
    def test_not_analysis_questions(self, q):
        assert not is_analysis_question(q)

    def test_analysis_question_adds_stock_tool_when_model_fetched_only_news(self):
        from app import agent

        calls = [{"function": {"name": "news_tool", "arguments": {"company_name": "삼성전자"}}}]
        added = agent._add_requested_tools(calls, "삼성전자 분석해줘")
        assert [calls[i]["function"]["name"] for i in added] == ["stock_tool"]
        assert calls[added[0]]["function"]["arguments"] == {"ticker": "삼성전자"}


# ---------- 문제 3: 일반론·반복 ----------

SAMPLE_D05 = """## 주가 동향

최근 1일 동안 현대차의 주가는 2.2% 하락하였으며, 거래량은 658,751주가 거래되었습니다.

## 뉴스·공시 근거

최신 뉴스나 공시 정보는 다음과 같습니다.

최신 공시 내용은 disclosure_tool 결과에 있습니다.

## 원인 분석

* 현대차의 주가는 일반적인 기업의 주가와 마찬가지로 수많은 요인에 의해 영향을 받습니다. 대표적인 요인은 현대차의 재무 상태, 경영 성과, 업계 동향, 경제 상황 등입니다.
* 이 외에도 수많은 요인이 주가를 결정할 수 있습니다.

따라서, 현대차의 주가 움직임을 분석할 때는 이러한 요인을 함께 고려해야 합니다."""

SAMPLE_E01 = """## 주가 동향
KODEX 200의 2026-11-12 종가는 84,240원으로, 2026-10-15 종가 86,901원 대비 20거래일간 -3.06%입니다. 마지막 거래일(2026-11-12) 하루 등락률은 -0.35%, 거래량은 21,638,160주입니다.

## 원인 분석
거래량은 21,638,160주로 마지막 거래일(2026-11-12) 하루 거래량은 21,638,160주입니다."""

SAMPLE_G01_CAUSE = """## 주가 동향
셀트리온의 2026-11-12 종가는 202,420원입니다.

## 원인 분석
주가는 뉴스와 공시를 통해 변동합니다. 셀트리온의 경우 삼성바이오로직스와 항체 치료제 생산 계약을 맺었기 때문에 주가가 상승한 것으로 보입니다."""


class TestGenericAndRepeats:
    def test_generic_sentences_and_tool_name_leaks_are_removed(self):
        out, n = answer_tidy.strip_generic(SAMPLE_D05)
        assert "수많은 요인" not in out and "disclosure_tool" not in out and "이러한 요인을 함께 고려" not in out
        assert "최신 뉴스나 공시 정보는 다음과 같습니다" not in out  # 뒤에 아무 근거도 없는 안내 문장
        assert "2.2% 하락" in out and "658,751주" in out  # 데이터 문장은 남는다
        assert "## 원인 분석" not in out and "## 뉴스·공시 근거" not in out  # 비어 버린 절은 제목도 지운다
        assert n >= 5

    def test_cause_section_that_only_repeats_earlier_numbers_is_removed(self):
        out, n = answer_tidy.strip_generic(SAMPLE_E01)
        assert "## 원인 분석" not in out and out.count("21,638,160") == 1 and n == 1

    def test_specific_cause_is_kept_but_platitude_is_dropped(self):
        out, _ = answer_tidy.strip_generic(SAMPLE_G01_CAUSE)
        assert "주가는 뉴스와 공시를 통해 변동합니다" not in out
        assert "항체 치료제 생산 계약" in out and "## 원인 분석" in out

    def test_empty_heading_is_removed_even_without_repeats(self):
        out, _ = answer_tidy.tidy("## 주가 동향\nKODEX 200의 2026-11-12 종가는 84,240원입니다.\n\n## 관련 뉴스\n")
        assert "## 관련 뉴스" not in out and "84,240원" in out

    @pytest.mark.parametrize("keep", [
        "삼성전자의 2026-11-12 종가는 208,000원입니다.",
        "3분기 영업이익이 전년 대비 59% 늘었다는 기사가 있습니다.",
        "환율 상승이 수출주에 유리하다는 분석 기사가 2026-11-03에 나왔습니다.",
        "조회한 기간에는 관련 공시가 없습니다.",
        "레버리지·인버스 ETF는 여러 날 보유하면 복리 효과 때문에 결과가 달라질 수 있습니다.",
    ])
    def test_data_and_specific_sentences_are_kept(self, keep):
        out, n = answer_tidy.strip_generic(keep)
        assert out == keep and n == 0

    def test_nothing_left_returns_empty(self):
        out, n = answer_tidy.strip_generic("주가는 다양한 요인에 의해 변동합니다. 투자 결정은 신중하게 하시기 바랍니다.")
        assert out == "" and n == 2


def test_bullet_only_lines_are_removed():
    # 48차 holdout D3: "최근 공시 목록은 다음과 같습니다." 뒤에 글머리 기호만 있는 줄이 남아 번역 검증에도 떨어졌다
    text = ("## 주가 동향\nSK하이닉스의 2026-11-12 종가는 1,254,000원입니다.\n\n"
            "## 뉴스·공시 근거\n- 최근 공시 목록은 다음과 같습니다.\n    -   -   -   -\n-   -   -")
    out, n = answer_tidy.strip_generic(text)
    assert out == "## 주가 동향\nSK하이닉스의 2026-11-12 종가는 1,254,000원입니다." and n == 3
