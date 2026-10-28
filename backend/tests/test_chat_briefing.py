"""(D) 채팅에서 개인화 브리핑: 라우팅 게이트(app/routing.needs_briefing), 텍스트 렌더링(app/briefing_text), 에이전트 연결.
DB·Ollama 없이 돈다(브리핑은 app.briefing.build_briefing으로 만든 가짜 입력)."""

from datetime import date, datetime, timedelta

import pytest

from app import agent, briefing_text, routing
from app.briefing import Bar, BriefingInputs, Instrument, KST, build_briefing
from app.disclaimer import DISCLAIMER
from evals import metrics as M

NOW = datetime(2026, 10, 28, 16, 0, tzinfo=KST)


def closes(prev, last, n=21, volume=1000):
    rows = [Bar(date(2026, 10, 28) - timedelta(days=n - 1 - i), prev, volume) for i in range(n - 1)]
    return rows + [Bar(date(2026, 10, 28), last, volume * 3)]


BRIEF = build_briefing(BriefingInputs(
    has_account=True, cash_balance=5_000_000.0,
    holdings=[Instrument("005930", "삼성전자", "stock", 10, 60_000.0)],
    watchlist=[Instrument("000660", "SK하이닉스", "stock"), Instrument("122630", "KODEX 레버리지", "etf")],
    bars={"005930": closes(70_000.0, 74_200.0), "000660": closes(100_000.0, 101_000.0), "122630": closes(20_000.0, 19_000.0)},
    kospi=[(date(2026, 10, 28), 2550.0), (date(2026, 10, 27), 2500.0)], usd_krw=[(date(2026, 10, 28), 1400.0)],
), 3, NOW)
EMPTY = build_briefing(BriefingInputs(kospi=[(date(2026, 10, 28), 2550.0)]), 3, NOW)


class TestGate:
    @pytest.mark.parametrize("q", [
        "오늘 브리핑 해줘", "브리핑", "내 보유종목 오늘 어때?", "관심종목 뭐 달라졌어?", "오늘 내 포트폴리오 상황 요약해줘",
        "내 계좌 현황 알려줘", "보유 종목 정리해줘", "관심 종목 변화 보여줘", "제 포트폴리오 어때요?",
    ])
    def test_briefing_questions(self, q):
        assert routing.needs_briefing(q) is True

    @pytest.mark.parametrize("q", [
        "삼성전자 오늘 어때?", "SK하이닉스 주가 알려줘", "포트폴리오 분산 투자가 뭐야?", "포트폴리오 이론 설명해줘",
        "관심종목에 삼성전자 추가해줘", "보유종목 삼성전자 매도해줘", "내 포트폴리오 진단해줘", "KODEX 200 어떤 상품이야?",
        "오늘 많이 오른 종목 알려줘", "계좌 개설은 어떻게 해?",
    ])
    def test_other_questions_are_not_briefing(self, q):
        assert routing.needs_briefing(q) is False

    @pytest.mark.parametrize("q,scope", [
        ("오늘 브리핑 해줘", "all"), ("내 보유종목 오늘 어때?", "holdings"), ("관심종목 뭐 달라졌어?", "watchlist"),
        ("내 포트폴리오 요약해줘", "holdings"), ("보유종목이랑 관심종목 브리핑", "all"),
    ])
    def test_scope(self, q, scope):
        assert routing.briefing_scope(q) == scope

    def test_existing_gates_are_unchanged_for_briefing_questions(self):
        for q in ("오늘 브리핑 해줘", "내 보유종목 오늘 어때?", "관심종목 뭐 달라졌어?"):
            assert not routing.needs_market_tool(q) and not routing.needs_fx_tool(q) and not routing.needs_discovery_tool(q)


class TestRender:
    @pytest.mark.parametrize("scope", briefing_text.SCOPES)
    def test_every_number_comes_from_the_briefing_and_disclaimer_is_included(self, scope):
        text = briefing_text.render(BRIEF, scope)
        matched, unmatched = M.match_numbers(text, [BRIEF])
        assert unmatched == [] and matched
        assert DISCLAIMER in text and M.has_disclaimer(text)
        assert M.forbidden_hits(text) == []

    def test_scopes_show_the_right_lists(self):
        hold, watch = briefing_text.render(BRIEF, "holdings"), briefing_text.render(BRIEF, "watchlist")
        assert "## 보유종목" in hold and "## 관심종목" not in hold and "총자산 5,742,000원" in hold
        assert "## 관심종목" in watch and "## 보유종목" not in watch and "총자산" not in watch
        assert "SK하이닉스" in watch and "레버리지 ETF입니다" in watch

    def test_header_has_data_date_and_market(self):
        text = briefing_text.render(BRIEF)
        assert text.startswith("2026-10-28 종가 기준 브리핑입니다.")
        assert "코스피 2,550.00(2026-10-28, 직전 대비 +2.00%)" in text and "원/달러 1,400.00원(2026-10-28)" in text

    def test_empty_user(self):
        text = briefing_text.render(EMPTY)
        assert "관심종목을 추가하면 개인화된 브리핑을 볼 수 있습니다." in text and "모의투자 계좌가 아직 없습니다" in text
        assert M.match_numbers(text, [EMPTY])[1] == []

    def test_highlights_are_listed(self):
        text = briefing_text.render(BRIEF)
        assert all(f["text"] in text for f in BRIEF["highlights"])


class TestAgent:
    @pytest.fixture
    def calls(self, monkeypatch):
        seen = []
        import app.briefing_db as bdb

        monkeypatch.setattr(bdb, "get_briefing", lambda db, owner_key, news_limit: seen.append(owner_key) or BRIEF)

        def no_llm(**kw):
            raise AssertionError("브리핑 질문에 LLM을 부르면 안 된다")

        monkeypatch.setattr(agent, "_llm_chat", no_llm)
        return seen

    def test_logged_in_user_gets_code_briefing_for_own_key(self, calls):
        r = agent.ask_question("오늘 브리핑 해줘", save_report=False, user_id=42)
        assert calls == ["user:42"]
        assert r["answer"] == briefing_text.render(BRIEF, "all")
        assert r["used_tools"] == ["briefing"]

    def test_scope_follows_the_question(self, calls):
        r = agent.ask_question("관심종목 뭐 달라졌어?", save_report=False, user_id=7)
        assert calls == ["user:7"] and r["answer"] == briefing_text.render(BRIEF, "watchlist")

    def test_anonymous_request_gets_login_notice_only(self, calls):
        r = agent.ask_question("내 보유종목 오늘 어때?", save_report=False, user_id=None)
        assert r["answer"] == "로그인하면 개인화 브리핑을 볼 수 있습니다." and calls == []

    def test_user_is_not_carried_over_between_requests(self, calls):
        agent.ask_question("오늘 브리핑 해줘", save_report=False, user_id=42)
        r = agent.ask_question("오늘 브리핑 해줘", save_report=False)
        assert r["answer"] == briefing_text.LOGIN_REQUIRED and calls == ["user:42"]
