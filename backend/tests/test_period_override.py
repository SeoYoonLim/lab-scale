"""app.agent._apply_period_override 단위 테스트 + 회귀 검증.

회귀 검증 방법: `git diff`로 보면 이번 변경은 agent.py에 (1) import 한 줄, (2) `_apply_period_override`
함수 신설, (3) `_answer()`에 그 함수를 부르는 한 줄과 결과를 쓰는 조건문 세 줄을 더한 것뿐이고 기존 줄은 단 한
줄도 고치지 않은 순수 추가 diff다. `_apply_period_override`는 `parse_period(question)`이 None이면 즉시
`{}`를 돌려주고 calls를 전혀 건드리지 않으므로, "기간 표현을 인식하지 못하는 질문"에서는 이 모듈이 없던
이전 코드와 **완전히 같은 동작**이라는 것이 코드 구조만으로 증명된다. 아래 테스트는 그 불변식(인식 못 하면
무변경)이 실제로 성립하는지를 충분히 많은(600개 이상) 합성 입력으로 확인한다 - 이전에 company_resolver
변경을 2,292개 입력으로, market_tool 키워드 게이트를 44개 비시장 질문으로 검증한 것과 같은 방식이다."""

import copy

import pytest

import app.agent as agent
from app.company_aliases import COMPANY_ALIASES
from scripts.benchmark_routing import CASES


def stock_call(period_days=1, ticker="삼성전자"):
    return {"function": {"name": "stock_tool", "arguments": {"ticker": ticker, "period_days": period_days}}}


def market_call(period_days=5, ticker="삼성전자"):
    return {"function": {"name": "market_tool", "arguments": {"ticker": ticker, "period_days": period_days}}}


def news_call(company_name="삼성전자"):
    return {"function": {"name": "news_tool", "arguments": {"company_name": company_name}}}


class TestApplyPeriodOverride:
    def test_no_recognized_period_leaves_calls_untouched(self):
        calls = [stock_call(period_days=1)]
        before = copy.deepcopy(calls)
        assert agent._apply_period_override(calls, "삼성전자 주가 알려줘") == {}
        assert calls == before

    def test_recognized_period_overrides_stock_tool_period_days(self):
        calls = [stock_call(period_days=1)]
        overridden = agent._apply_period_override(calls, "삼성전자 지난주 주가 알려줘")
        assert overridden == {0: 1}
        assert calls[0]["function"]["arguments"]["period_days"] == 5  # 지난주 = 평일 5일

    def test_recognized_period_overrides_market_tool_period_days(self):
        calls = [market_call(period_days=5)]
        overridden = agent._apply_period_override(calls, "삼성전자 지난달 코스피 대비 어때?")
        assert overridden == {0: 5}
        assert calls[0]["function"]["arguments"]["period_days"] == 22  # 지난달(2026-09) = 평일 22일

    def test_tools_without_a_period_argument_are_left_alone(self):
        calls = [news_call()]
        assert agent._apply_period_override(calls, "삼성전자 지난주 뉴스") == {}
        assert calls[0]["function"]["arguments"] == {"company_name": "삼성전자"}

    def test_multiple_calls_only_period_bearing_ones_are_touched(self):
        calls = [stock_call(period_days=1), news_call(), market_call(period_days=5)]
        overridden = agent._apply_period_override(calls, "삼성전자 지난주 주가랑 뉴스, 시장 대비도 알려줘")
        assert set(overridden) == {0, 2}
        assert calls[0]["function"]["arguments"]["period_days"] == 5
        assert calls[1]["function"]["arguments"] == {"company_name": "삼성전자"}
        assert calls[2]["function"]["arguments"]["period_days"] == 5

    def test_missing_period_days_argument_records_none_as_original(self):
        calls = [{"function": {"name": "stock_tool", "arguments": {"ticker": "삼성전자"}}}]
        overridden = agent._apply_period_override(calls, "삼성전자 지난주 주가")
        assert overridden == {0: None}
        assert calls[0]["function"]["arguments"]["period_days"] == 5

    def test_json_string_arguments_are_normalized_to_dict(self):
        calls = [
            {"function": {"name": "stock_tool", "arguments": '{"ticker": "삼성전자", "period_days": 1}'}}
        ]
        agent._apply_period_override(calls, "삼성전자 어제 주가")
        assert calls[0]["function"]["arguments"] == {"ticker": "삼성전자", "period_days": 1}


class TestAnswerAppliesPeriodOverride:
    """_answer()의 실행 경로에서 실제로 period_days가 덮어써지는지(LLM이 추론한 값보다 우선하는지) 확인한다."""

    def _run(self, monkeypatch, question, llm_period_days=999):
        seen = {}

        def fake_first_call(model, messages, tools=None):
            calls = [stock_call(period_days=llm_period_days)]
            return {"role": "assistant", "content": "", "tool_calls": calls}, calls

        def fake_run_tool(name, args):
            seen["args"] = dict(args)
            return {"found": True}

        monkeypatch.setattr(agent, "_first_call", fake_first_call)
        monkeypatch.setattr(agent, "_run_tool", fake_run_tool)
        monkeypatch.setattr(agent.ollama, "chat", lambda **kw: {"message": {"content": "답변"}})
        agent._answer(question, "m")
        return seen["args"]

    def test_llm_guess_is_overridden_when_question_has_a_recognized_period(self, monkeypatch):
        args = self._run(monkeypatch, "삼성전자 지난주 주가 알려줘", llm_period_days=999)
        assert args["period_days"] == 5

    def test_llm_guess_is_kept_when_question_has_no_recognized_period(self, monkeypatch):
        args = self._run(monkeypatch, "삼성전자 주가 알려줘", llm_period_days=7)
        assert args["period_days"] == 7

    def test_correction_metadata_is_attached_to_the_tool_result(self, monkeypatch):
        captured = {}

        def fake_first_call(model, messages, tools=None):
            calls = [stock_call(period_days=1)]
            return {"role": "assistant", "content": "", "tool_calls": calls}, calls

        monkeypatch.setattr(agent, "_first_call", fake_first_call)
        monkeypatch.setattr(agent, "_run_tool", lambda name, args: {"found": True})
        monkeypatch.setattr(agent.ollama, "chat", lambda **kw: {"message": {"content": "답변"}})
        _, records = agent._answer("삼성전자 지난주 주가 알려줘", "m")
        assert records[0]["result"]["period_days_corrected_from"] == 1
        assert records[0]["result"]["period_days_corrected_via"] == "period_parser"


# --- 회귀 검증: "인식 못 하면 손대지 않는다"가 충분히 많은 합성 입력에서 유지되는지 ----------------------

_TEMPLATES = [
    "{c} 주가 알려줘",
    "{c} 최근 뉴스 알려줘",
    "{c} 공시 내용 자세히 알려줘",
    "{c} 최근 왜 올랐어? 뉴스도 같이 확인해줘",
    "{c} 거래량 얼마야?",
    "{c} 최근 공시 뭐 있어?",
    "{c}가 코스피보다 더 올랐어?",
    "{c} 반도체 업황 관련 근거 찾아줘",
    "{c} 배당 정책이 어떻게 돼?",
    "{c} 시가총액이 얼마야?",
    "{c} PER이 얼마야?",
    "{c} 관련 소식 있어?",
]
_COMPANIES = sorted(set(COMPANY_ALIASES.values()))  # 등록명 기준(별칭 자체는 기간 표현과 무관)
# 위 템플릿 + 종목명 조합에는 기간 표현을 전혀 넣지 않았다(의도적인 "미매칭이어야 하는" 합성 입력).
SYNTHETIC_NO_PERIOD_QUESTIONS = [t.format(c=c) for c in _COMPANIES for t in _TEMPLATES]

# 실제 라우팅 벤치마크 질문(보지 않고 쓴 hold-out 포함). 일부("최근 5일" 등 명시적 숫자+단위)는 의도적으로
# 인식 대상이라 "무변경 집합"에서는 제외하고 따로 센다(아래 테스트가 그 구분을 자동으로 한다).
ALL_BENCHMARK_QUESTIONS = [q for qs in CASES.values() for q in qs]


class TestNoOverrideRegressionOnSyntheticCorpus:
    def test_corpus_is_large_enough_to_be_a_meaningful_regression_check(self):
        assert len(SYNTHETIC_NO_PERIOD_QUESTIONS) >= 500
        assert len(ALL_BENCHMARK_QUESTIONS) >= 50

    def test_synthetic_no_period_questions_are_all_left_completely_unchanged(self):
        # 템플릿에 기간 표현을 넣지 않았으므로 전부 parse_period가 None이어야 한다(아니면 템플릿/별칭이 겹친 것).
        for q in SYNTHETIC_NO_PERIOD_QUESTIONS:
            calls = [stock_call(period_days=1)]
            before = copy.deepcopy(calls)
            overridden = agent._apply_period_override(calls, q)
            assert overridden == {}, f"예상치 못하게 기간 표현으로 인식됨: {q!r}"
            assert calls == before, f"무변경이어야 하는데 바뀜: {q!r}"

    def test_benchmark_questions_without_a_recognized_period_are_unchanged(self):
        """실제 라우팅 벤치마크 질문 전체를 돌려, 파서가 인식하지 못하는 질문은 전부 기존과 동일(무변경)함을 확인한다.
        인식하는 질문(예: "최근 5일")이 몇 개나 있는지도 같이 세어, 그 개수가 비정상적으로 크지 않은지 본다
        (너무 많으면 패턴이 지나치게 공격적이라는 신호)."""
        recognized = 0
        for q in ALL_BENCHMARK_QUESTIONS:
            calls = [stock_call(period_days=1)]
            before = copy.deepcopy(calls)
            overridden = agent._apply_period_override(calls, q)
            if overridden == {}:
                assert calls == before
            else:
                recognized += 1
        # "최근 5일/10일" 같은 명시적 숫자+단위가 실제로 섞여 있어 0은 아니지만, 전체의 절반을 넘지는 않는다.
        assert 0 < recognized < len(ALL_BENCHMARK_QUESTIONS) // 2

    @pytest.mark.parametrize("question", ALL_BENCHMARK_QUESTIONS)
    def test_each_benchmark_question_is_a_pure_noop_or_touches_only_period_days(self, question):
        """인식되든 안 되든, stock_tool 호출의 ticker 인자나 다른 키는 절대 건드리지 않는다(period_days만 바뀔 수 있음)."""
        calls = [stock_call(period_days=1, ticker="삼성전자")]
        agent._apply_period_override(calls, question)
        args = calls[0]["function"]["arguments"]
        assert args["ticker"] == "삼성전자"
        assert set(args) == {"ticker", "period_days"}
