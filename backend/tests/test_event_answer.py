"""사건 이후 주가 반응 질문(순수 로직): 라우팅 게이트, 사건 종류·키워드 추출, 코드가 쓰는 섹션, LLM 문장 검증기."""

import copy

import pytest

from app.event_answer import (
    DISCLOSURE_TIME_NOTE,
    EVENT_ANSWER_RULES,
    NEWS_DATE_ONLY_NOTE,
    SECTION_TITLE,
    check_event_answer,
    compose_event_answer,
    display_items,
    display_summary,
    event_result_for_llm,
    format_event_section,
    reason_text,
    summary_sentence,
)
from app.event_reaction import DISCLAIMER
from app.routing import event_keyword_for, event_type_for, needs_event_reaction_tool
from app.tools.event_reaction_tool import TITLE_KEYWORDS
from scripts.benchmark_routing import CASES
from tests.test_period_override import SYNTHETIC_NO_PERIOD_QUESTIONS

# ---------- 라우팅 게이트 ----------

GATE_PASS = [
    "삼성전자 최근 공시 이후 주가 반응은?",
    "삼성전자 실적 발표 후 주가 어땠어?",
    "SK하이닉스 공시 나온 뒤 주가 움직임 알려줘",
    "KT 뉴스 이후 주가 반응은?",
    "알테오젠 공시 이후 한 달 흐름",
    "KODEX 200 뉴스 이후 주가 반응",
    "금호타이어 기사 나온 뒤 주가",
    "삼성전자 자기주식취득 공시 이후 주가",
    "삼성전자 공시 직후 주가 어땠어?",
    "카카오 실적 발표 직후 주가는?",
    "현대차 공시 나오고 나서 주가 어떻게 됐어? 공시 나온 뒤 기준으로",
    "삼성전자 뉴스 뜬 뒤에 주가 어땠어",
    "삼성전자 공시 올라온 후 주가",
    "삼성전자 실적 발표한 다음날 주가",
    "삼성전자 공시 나온 다음에 주가 어땠어",
    "삼성전자 공시 다음 날 주가",
    "삼성전자 공시 후 주가 흐름",
    "삼성전자 발표 뒤 주가 움직임",
    "삼성전자 뉴스 후에 주가가 어떻게 됐어",
    "삼성전자 유상증자 했을 때 주가",
    "삼성전자 배당 발표됐을 때 주가 어땠어",
    "삼성전자 공시에 대한 주가 반응",
    "삼성전자 주가 반응 알려줘, 최근 공시 기준으로",
    "삼성전자 최근 뉴스에 시장 반응 어땠어?",
    "LG에너지솔루션 공급계약 공시 이후 주가",
    "셀트리온 소송 뉴스 이후 주가",
    "한화에어로스페이스 수주 소식 나온 뒤 주가는?",
    "삼성전자 실적 이후 주가 변화",
    "네이버 기사 보도된 후 주가",
    "삼성전자 합병 공시 이후 반응",
    "삼성전자 무상증자 공시 나온 뒤",
    "그 공시 이후 주가는 어땠어?",
]

GATE_FAIL = [
    # 단순 뉴스·공시 조회(관계어 없음) - 기존 도구와 충돌하면 안 된다
    "삼성전자 최근 뉴스 알려줘",
    "삼성전자 공시 알려줘",
    "삼성전자 최근 공시 뭐 있어?",
    "삼성전자 뉴스랑 주가 움직임 같이 알려줘",
    "삼성전자 최근 상황을 종합해서 알려줘. 주가, 뉴스, 공시를 같이 확인해줘.",
    "삼성전자 실적 발표 일정 알려줘",
    "삼성전자 배당 정책이 어떻게 돼?",
    "삼성전자 공시 내용 자세히 알려줘",
    "삼성전자 관련 소식 있어?",
    # 기간 표현이 사건어 앞에 있다(순서가 반대)
    "삼성전자 9월 이후 뉴스 알려줘",
    "지난주 이후 공시 목록",
    "10월 이후 삼성전자 기사",
    # 사건어가 없다
    "삼성전자 주가 알려줘",
    "삼성전자 최근 주가 흐름 알려줘",
    "삼성전자 왜 올랐어?",
    "삼성전자 코스피 대비 어때?",
    "요즘 환율 어때?",
    "오늘 급등한 종목 알려줘",
    "PER이 뭐야?",
    "주가 반응이 뭐야?",
    "시장 반응이 좋다는 게 무슨 뜻이야?",
    # 사건어는 있지만 시간 관계가 아니다
    "뉴스 뒤져줘",
    "공시 후속 조치가 뭐야?",
    "실적 개선한 후보 종목",
    "뉴스 다음 기사 보여줘",
    "공시 후보 목록",
    "삼성전자 뉴스 뒤쪽 내용 알려줘",
    "실적 좋은 종목 추천",
    "배당 많이 주는 종목",
    "삼성전자 공시와 뉴스 차이가 뭐야?",
    "",
    "공모주 청약이 뭔지 쉽게 설명해줘.",
]


@pytest.mark.parametrize("q", GATE_PASS)
def test_gate_passes(q):
    assert needs_event_reaction_tool(q), q


@pytest.mark.parametrize("q", GATE_FAIL)
def test_gate_does_not_pass(q):
    assert not needs_event_reaction_tool(q), q


def test_gate_case_count():
    assert len(GATE_PASS) + len(GATE_FAIL) >= 60


def test_no_false_positive_on_benchmark_questions():
    """라우팅 벤치마크 질문(모든 그룹)은 사건 반응 질문이 아니다."""
    questions = [q for qs in CASES.values() for q in qs]
    assert len(questions) >= 50
    assert [q for q in questions if needs_event_reaction_tool(q)] == []


def test_no_false_positive_on_synthetic_corpus():
    """주식 일반 질문 합성 말뭉치(12개 템플릿 x 별칭 종목, 기간 처리 회귀 검사와 같은 것)에 오탐이 없다."""
    assert len(SYNTHETIC_NO_PERIOD_QUESTIONS) >= 500
    assert [q for q in SYNTHETIC_NO_PERIOD_QUESTIONS if needs_event_reaction_tool(q)] == []


@pytest.mark.parametrize(
    "q, expected",
    [
        ("삼성전자 최근 공시 이후 주가 반응은?", "disclosure"),
        ("KT 뉴스 이후 주가 반응은?", "news"),
        ("금호타이어 기사 나온 뒤 주가", "news"),
        ("삼성전자 실적 발표 후 주가 어땠어?", "all"),
        ("삼성전자 공시랑 뉴스 이후 주가", "all"),
        ("삼성전자 자기주식취득 이후 주가", "disclosure"),
    ],
)
def test_event_type(q, expected):
    assert event_type_for(q) == expected


@pytest.mark.parametrize(
    "q, expected",
    [
        ("삼성전자 자기주식취득 공시 이후 주가", "자기주식"),
        ("삼성전자 자사주 공시 이후 주가", "자기주식"),
        ("삼성전자 실적 발표 후 주가 어땠어?", "실적"),
        ("삼성전자 최근 공시 이후 주가 반응은?", None),
        ("삼성전자 유상증자랑 배당 공시 이후", "유상증자"),
        ("삼성전자 수주 공시 이후", None),  # 허용 목록 밖
    ],
)
def test_keyword(q, expected):
    assert event_keyword_for(q, TITLE_KEYWORDS) == expected


# ---------- 답변 구성 ----------


def _h(pct=None, idx=None, ex=None, reason=None):
    return {"pct": pct, "index_pct": idx, "excess_pct": ex, "available": pct is not None, "reason": reason}


def _event(kind="disclosure", day="2026-07-30", at=None, title="특수관계인에대한출자", d1=(-0.72, -1.23, 0.51),
           d5=(17.99, 16.51, 1.48), d20=(27.58, 22.06, 5.52), others=0, note=None, vol=1.55, start=None):
    def h(v, n):
        return _h(*v) if v else _h(reason=f"{n}거래일 경과 전")

    return {
        "event_type": kind, "title": title, "url": f"https://x/{title}", "event_at": at,
        "event_date": None if at else day, "time_known": at is not None,
        "base_date": "2026-07-29", "base_close": 208500.0, "reaction_start_date": start or day,
        "returns": {"d1": h(d1, 1), "d5": h(d5, 5), "d20": h(d20, 20)},
        "volume_ratio": vol, "volume_sample_days": 20, "other_events_in_window": others, "note": note,
    }


def _result(events, **kw):
    from app.event_reaction import summarize

    return {
        "found": True, "company_name": "삼성전자", "ticker": "005930", "asset_type": "stock",
        "market_index": "KS11", "price_as_of": "2026-10-14", "since_days": 120, "event_type": "all",
        "events": events, "summary": summarize(events), "disclaimer": DISCLAIMER, **kw,
    }


class TestReasonText:
    def test_mapping(self):
        assert reason_text("20거래일 경과 전") == "아직 20거래일이 지나지 않아 계산할 수 없음"
        assert reason_text("반응 시작일 전(다음 거래일 데이터 없음)") == "다음 거래일 주가가 아직 없어 계산할 수 없음"
        assert reason_text("기준 종가 없음(시세 데이터 시작 전)") == "사건 이전 주가 데이터가 없어 계산할 수 없음"


class TestSection:
    def test_values_and_reason_shown(self):
        r = _result([_event(d20=None)])
        s = format_event_section(r)
        assert s.startswith(SECTION_TITLE)
        assert "기준 종가 208,500원(2026-07-29)" in s
        assert "1거래일 -0.72%(코스피 -1.23%, 초과 +0.51%p)" in s
        assert "5거래일 +17.99%" in s
        assert "20거래일: 아직 20거래일이 지나지 않아 계산할 수 없음" in s
        assert "평균의 1.55배" in s

    def test_disclosure_time_note_once(self):
        s = format_event_section(_result([_event(), _event(day="2026-08-01", title="b")]))
        assert s.count(DISCLOSURE_TIME_NOTE) == 1

    def test_no_disclosure_note_for_news_only(self):
        s = format_event_section(_result([_event(kind="news", at="2026-08-13T18:24:00+09:00", title="n")]))
        assert DISCLOSURE_TIME_NOTE not in s and NEWS_DATE_ONLY_NOTE not in s
        assert "2026-08-13 · 뉴스 1건" in s and "  · n" in s and "18:24" not in s  # 묶음 항목은 시각 대신 날짜만

    def test_news_without_time_note(self):
        s = format_event_section(_result([_event(kind="news", title="n")]))
        assert NEWS_DATE_ONLY_NOTE in s

    def test_other_events_shown(self):
        s = format_event_section(_result([_event(others=12)]))
        assert "(같은 기간 다른 사건 12건)" in s
        assert "다른 사건" not in format_event_section(_result([_event(others=0)]))

    def test_adjustment_warning_hides_returns(self):
        e = _event(note="가격 조정(분할 등) 가능성: 구간 안에 일간 등락률이 30%를 넘는 날이 있습니다.")
        s = format_event_section(_result([e]))
        assert "수익률을 표시하지 않습니다" in s and "17.99" not in s and "-0.72" not in s
        # 요약에서도 빠진다
        assert "모두 아직 수익률을 계산할 수 없습니다" in s

    def test_etf_excess_reason_once(self):
        reason = "ETF는 시장 지수 대비 초과수익률을 계산하지 않습니다"
        e = _event(kind="news", title="n", d1=(1.0, None, None), d5=None, d20=None)
        e["returns"]["d1"]["reason"] = reason
        s = format_event_section(_result([e, {**e, "title": "m"}], asset_type="etf", market_index=None))
        assert s.count(reason) == 1 and "초과" not in s.split(reason)[1].split("요약")[0]

    def test_not_found(self):
        s = format_event_section({"found": False, "message": "최근 120일 안에 삼성전자의 공시 사건이 없습니다. 대상 사건이 없습니다."})
        assert s == SECTION_TITLE + "\n최근 120일 안에 삼성전자의 공시 사건이 없습니다. 대상 사건이 없습니다."


def _news(i, day="2026-08-13", hour=10, start=None, **kw):
    return _event(kind="news", day=day, at=f"{day}T{hour:02d}:00:00+09:00", title=f"기사{i}", start=start, **kw)


class TestGrouping:
    def test_same_start_news_become_one_item_with_three_titles_and_one_set_of_numbers(self):
        evs = [_news(i, hour=9 + i) for i in range(5)]
        s = format_event_section(_result(evs))
        assert s.count("· 뉴스 5건") == 1
        assert [f"  · 기사{i}" in s for i in (4, 3, 2)] == [True] * 3  # 최신순 3개
        assert "  · 기사1" not in s and "  · 기사0" not in s and "  · 외 2건" in s
        assert s.count("기준 종가 208,500원") == 1 and s.count("1거래일 -0.72%") == 1
        assert "사건 5건(날짜 1개)" in s

    def test_disclosures_are_never_grouped(self):
        evs = [_event(title=f"공시{i}") for i in range(3)]
        items = display_items(_result(evs))
        assert [i["kind"] for i in items] == ["disclosure"] * 3
        s = format_event_section(_result(evs))
        assert s.count("· 공시 ·") == 3 and s.count("기준 종가") == 3

    def test_news_on_different_start_dates_stay_separate_and_sorted_newest_first(self):
        evs = [_news(1, day="2026-08-10"), _news(2, day="2026-08-12"), _news(3, day="2026-08-11")]
        s = format_event_section(_result(evs))
        assert s.index("2026-08-12 · 뉴스 1건") < s.index("2026-08-11 · 뉴스 1건") < s.index("2026-08-10 · 뉴스 1건")

    def test_group_label_spans_event_days_when_start_date_is_shared(self):
        # 금요일 장후, 토요일, 월요일 장 시작 전 기사는 모두 월요일에 반응이 시작된다
        evs = [_news(1, day="2026-08-14", hour=18, start="2026-08-18"), _news(2, day="2026-08-15", start="2026-08-18"),
               _news(3, day="2026-08-18", hour=8, start="2026-08-18")]
        s = format_event_section(_result(evs))
        assert "2026-08-14~2026-08-18 · 뉴스 3건" in s

    def test_news_and_disclosure_are_listed_together_by_date(self):
        evs = [_news(1, day="2026-08-10"), _event(day="2026-08-12", title="공시a"), _news(2, day="2026-08-14")]
        s = format_event_section(_result(evs))
        assert s.index("2026-08-14 · 뉴스") < s.index("2026-08-12 · 공시") < s.index("2026-08-10 · 뉴스")

    def test_item_limit_is_per_type_after_grouping(self):
        evs = [_news(i, day=f"2026-08-{10 + i:02d}") for i in range(8)] + [_event(day=f"2026-07-{10 + i:02d}", title=f"공시{i}") for i in range(8)]
        r = _result(evs, limit=5)
        items = display_items(r)
        assert [i["kind"] for i in items].count("news") == 5 and [i["kind"] for i in items].count("disclosure") == 5
        # 최신 5개 뉴스 날짜만 남는다
        assert {i["events"][0]["title"] for i in items if i["kind"] == "news"} == {f"기사{i}" for i in (3, 4, 5, 6, 7)}

    def test_many_same_day_news_still_show_five_dates_when_fetched_more(self):
        evs = [_news(f"{d}-{k}", day=f"2026-08-{10 + d:02d}", hour=9 + k) for d in range(6) for k in range(3)]
        assert len(display_items(_result(evs, limit=5))) == 5

    def test_other_events_shown_on_group(self):
        s = format_event_section(_result([_news(1, others=4), _news(2, others=4)]))
        assert s.count("(같은 기간 다른 사건 4건)") == 1


class TestSummary:
    def test_small_sample_sentence(self):
        r = _result([_event(), _event(day="2026-08-01", title="b", d20=None)])
        s = summary_sentence(display_summary(r))
        assert "조회한 사건 2건(날짜 2개) 중" in s and "5거래일 수익률은 2개 날짜에서" in s
        assert "표본(날짜)이 3개 미만이라 일반화하기 어렵습니다" in s

    def test_same_day_events_count_as_one_date_sample(self):
        r = _result([_news(i, hour=9 + i) for i in range(5)])
        sm = display_summary(r)
        assert sm["event_count"] == 5 and sm["date_count"] == 1 and sm["with_d1"] == 1
        s = summary_sentence(sm)
        assert "사건 5건(날짜 1개)" in s and "1개 날짜에서" in s and "일반화하기 어렵습니다" in s

    def test_enough_samples_no_warning(self):
        evs = [_event(day=f"2026-08-0{i}", title=str(i)) for i in range(1, 4)]
        assert "일반화하기 어렵" not in summary_sentence(display_summary(_result(evs)))

    def test_nothing_computed(self):
        e = _event(d1=None, d5=None, d20=None)
        assert "모두 아직 수익률을 계산할 수 없습니다" in summary_sentence(display_summary(_result([e])))


class TestCompose:
    def test_disclaimer_at_end_and_interpretation(self):
        a = compose_event_answer("공시 이후 1거래일간 -0.72% 움직였습니다.", _result([_event()]))
        assert a.endswith("※ " + DISCLAIMER)
        assert "## 해석\n공시 이후 1거래일간" in a

    def test_without_interpretation_still_complete(self):
        a = compose_event_answer("", _result([_event()]))
        assert "## 해석" not in a and a.startswith(SECTION_TITLE) and a.endswith(DISCLAIMER)

    def test_notices_before_disclaimer(self):
        a = compose_event_answer("", _result([_event()]), ["※ 레버리지 안내"])
        assert a.index("레버리지 안내") < a.index(DISCLAIMER)


class TestResultForLlm:
    def test_no_urls_no_disclaimer_and_adjusted_returns_hidden(self):
        e = _event(note="가격 조정(분할 등) 가능성")
        out = event_result_for_llm(_result([e, _event(day="2026-08-01", title="b")]))
        assert all("url" not in x for x in out["events"]) and "disclaimer" not in out
        adjusted = [x for x in out["events"] if isinstance(x["returns"], str)]
        assert len(adjusted) == 1 and adjusted[0]["title"] == e["title"]
        assert out["summary"]["with_d1"] == 1

    def test_input_not_modified(self):
        r = _result([_event()])
        before = copy.deepcopy(r)
        event_result_for_llm(r)
        assert r == before

    def test_rules_have_no_example_values(self):
        # 예시 수치가 있으면 모델이 답변에 베낀다(agent.FEW_SHOT_MESSAGES 주석)
        import re

        assert not re.search(r"\d+(?:\.\d+)?\s?(?:%|원|배)", EVENT_ANSWER_RULES)


# ---------- 검증기 ----------


class TestChecker:
    R = _result([
        _event(),
        _event(kind="news", at="2026-09-24T18:48:00+09:00", title="성과급 기사", d1=(-5.76, -2.7, -3.06),
               d5=(-3.66, -1.09, -2.57), d20=None, others=3, start="2026-09-28"),
    ])

    def check(self, text, other=("SK하이닉스", "대상")):
        return check_event_answer(text, self.R, other)

    @pytest.mark.parametrize(
        "sent",
        [
            # 새 비교나 주의할 점이 있는 문장(수치는 결과에 있는 것)
            "같은 기간 다른 사건이 3건 있어 그 사건만의 움직임으로 볼 수는 없습니다.",
            "이 공시 때문이라고 단정할 수 없습니다.",
            "공시 이후 1거래일간 -0.72% 움직였지만 다른 사건과 겹쳐 있어 함께 봐야 합니다.",
            "2026-07-30 공시 이후 5거래일간 17.99% 상승했지만 코스피보다 크게 오르지는 않았습니다.",
            "조회한 사건 2건 중 1건은 코스피보다 낮았습니다.",
            "조회한 대상 사건 2건 중 1거래일 수익률이 계산된 사건은 2건입니다.",  # 건수 비교(\"N건 중\")는 새 관찰로 본다
            "시장 전체 흐름과 함께 봐야 합니다.",
            "표본이 적어 일반화하기 어렵습니다.",
        ],
    )
    def test_allowed_sentences_are_kept(self, sent):
        text, dropped = self.check(sent)
        assert dropped == [] and text == sent

    @pytest.mark.parametrize(
        "sent",
        [
            "공시 이후 1거래일간 주가는 -0.72% 움직였습니다.",
            "2026-07-30 공시 이후 5거래일간 17.99% 상승했습니다.",
            "7월 30일 공시 이후 20거래일간 27.58% 올랐습니다.",
            "코스피 대비 초과수익률은 +1.48%p였습니다.",
            "뉴스 이후 20거래일 수익률은 아직 계산할 수 없습니다.",
            "5거래일과 20거래일 값은 아직 계산할 수 없습니다.",
            "기준 종가는 208,500원이었습니다.",
            "거래량은 평균의 1.55배였습니다.",
        ],
    )
    def test_table_repeating_sentences_dropped(self, sent):
        """코드가 쓴 섹션의 수치·사유를 되풀이할 뿐 새 비교·관찰이 없는 문장(검증기 규칙 a)."""
        text, dropped = self.check(sent)
        assert text == "" and dropped[0][1] == "표의 수치·사유를 되풀이", dropped

    @pytest.mark.parametrize(
        "sent",
        [
            "최근 5일간 주가는 20거래일 경과 전이므로 수치를 제공할 수 없습니다.",
            "20일 동안은 20거래일 경과 전입니다.",
            "5거래일간의 수익률은 경과 전이라 알 수 없습니다.",
        ],
    )
    def test_period_and_reason_mixed_dropped(self, sent):
        """'N일간 주가는 20거래일 경과 전이므로…'처럼 기간과 사유를 섞어 쓴 문장(규칙 b)."""
        text, dropped = self.check(sent)
        assert text == "" and dropped[0][1] == "기간과 사유 문구를 섞어 씀", dropped

    @pytest.mark.parametrize(
        "sent, why",
        [
            ("시장 지수 대비 초과수익률은 5.16%였습니다.", "%"),  # 결과는 5.16%p
            ("지수 대비 초과 수익은 1.48%였습니다.", "%"),
            ("5거래일 수익률은 17.99%p였습니다.", "%p"),  # 결과는 17.99%
            ("코스피는 -1.23%p 움직였지만 다른 사건과 겹칩니다.", "%p"),
        ],
    )
    def test_percent_vs_percent_point_confusion_dropped(self, sent, why):
        """%p(초과수익률)를 %로, %(수익률)를 %p로 쓴 문장(규칙 c)."""
        r = _result([_event(d1=(-0.72, -1.23, 0.51), d5=(17.99, 16.51, 1.48), d20=(27.58, 22.06, 5.52)), _news(1, day="2026-08-12", d1=(-0.3, -5.46, 5.16))])
        text, dropped = check_event_answer(sent, r, ())
        assert text == "" and dropped and dropped[0][1].startswith("%와 %p를 혼동"), dropped

    def test_percent_and_percent_point_used_correctly_are_kept(self):
        r = _result([_event()])
        sent = "초과수익률 +1.48%p는 코스피 +16.51%와 함께 봐야 하고 다른 사건과 겹칩니다."
        assert check_event_answer(sent, r, ()) == (sent, [])

    @pytest.mark.parametrize(
        "sent",
        [
            "위와 같이 나타났습니다.",
            "주가 움직임은 공시 이후의 시간 순서를 따랐습니다.",
            "이상입니다.",
            "위 표를 참고하세요.",
            "사건들은 위 내용과 같이 나타났습니다.",
        ],
    )
    def test_empty_closing_sentences_dropped(self, sent):
        """'위와 같이 나타났습니다'처럼 내용 없는 마무리 문장(규칙 d)."""
        text, dropped = self.check(sent)
        assert text == "" and dropped[0][1] == "내용 없는 안내·마무리 문장", dropped

    def test_everything_dropped_gives_empty_text_and_no_interpretation_heading(self):
        llm = "공시 이후 1거래일간 -0.72% 움직였습니다.\n\n위와 같이 나타났습니다.\n- 5거래일: 아직 계산할 수 없습니다."
        text, dropped = self.check(llm)
        assert text == "" and len(dropped) == 3
        answer = compose_event_answer(text, self.R)
        assert "## 해석" not in answer and answer.startswith(SECTION_TITLE) and answer.endswith(DISCLAIMER)

    def test_partial_survival_keeps_heading(self):
        llm = "공시 이후 1거래일간 -0.72% 움직였습니다.\n다른 사건과 겹쳐 있어 단정할 수 없습니다."
        text, dropped = self.check(llm)
        assert text == "다른 사건과 겹쳐 있어 단정할 수 없습니다." and len(dropped) == 1
        assert "## 해석\n다른 사건과 겹쳐" in compose_event_answer(text, self.R)

    @pytest.mark.parametrize(
        "sent",
        [
            "공시 때문에 주가가 17.99% 올랐습니다.",
            "실적 공시의 영향으로 주가가 상승했습니다.",
            "자금 투입 소식 덕분에 주가가 올랐습니다.",
            "뉴스 탓에 주가가 빠졌습니다.",
            "주가 하락의 원인은 성과급 기사입니다.",
            "공시로 인해 주가가 상승했습니다.",
            "주가는 뉴스에 반응했습니다.",
            "공시 이후 1거래일간 -0.72% 반영되었습니다.",
            "- 2026년 7월 30일: 주가 -0.72% 반영",  # slow 2차 재측정 사례(명사형)
            "2026년 7월 30일, 특수관계인에대한출자가 공시되어, 1거래일간 0.72% 하락했습니다.",  # 연결어미로 인과처럼 잇기
            "실적이 발표되자 주가가 올랐습니다.",
            "호재로 작용해 주가가 올랐습니다.",
        ],
    )
    def test_causal_sentences_dropped(self, sent):
        text, dropped = self.check(sent)
        assert text == "" and dropped[0][1] == "인과 단정"

    @pytest.mark.parametrize(
        "sent",
        [
            "공시 이후 5거래일간 18.5% 올랐습니다.",
            "기준 종가는 210,000원이었습니다.",
            "거래량은 평균의 2.3배였습니다.",
            "공시 이후 주가는 25만 원대로 올라섰습니다.",
            "같은 기간 다른 사건이 7건 있었습니다.",
            "SK하이닉스는 같은 기간 3.5% 올랐습니다.",
        ],
    )
    def test_unsupported_numbers_dropped(self, sent):
        text, dropped = self.check(sent)
        assert text == "" and dropped, sent

    @pytest.mark.parametrize(
        "sent",
        [
            "공시 이후 한 달간 주가가 올랐습니다.",
            "일주일 동안 17.99% 움직였습니다.",
            "8월 15일 이후 주가는 27.58% 올랐습니다.",
            "공시 이후 3개월 동안 오름세였습니다.",
        ],
    )
    def test_unsupported_dates_and_periods_dropped(self, sent):
        text, dropped = self.check(sent)
        assert text == "" and dropped

    @pytest.mark.parametrize(
        "sent",
        [
            "공시 이후 5거래일간 17.99% 하락했습니다.",
            "뉴스 이후 1거래일간 5.76% 상승했습니다.",
            "뉴스 이후 5거래일간 주가는 올랐습니다.",
        ],
    )
    def test_wrong_direction_dropped(self, sent):
        r = _result([_event(kind="news", at="2026-09-24T18:48:00+09:00", title="t", d1=(-5.76, -2.7, -3.06),
                            d5=(-3.66, -1.09, -2.57), d20=None)]) if "뉴스" in sent else self.R
        text, dropped = check_event_answer(sent, r, ())
        assert text == "" and dropped and dropped[0][1] == "방향이 결과와 반대", dropped

    def test_null_estimation_dropped(self):
        r = _result([_event(d5=None, d20=None)])
        text, dropped = check_event_answer("20거래일 뒤에는 주가가 올랐을 것입니다.", r, ())
        assert text == "" and dropped
        text, dropped = check_event_answer("20거래일 수익률은 3% 수준입니다.", r, ())
        assert text == "" and dropped

    @pytest.mark.parametrize(
        "sent",
        [
            "앞으로도 공시 이후 주가가 오를 가능성이 있습니다.",
            "이번에도 비슷한 흐름이 예상됩니다.",
            "매수를 고려해 볼 만합니다.",
        ],
    )
    def test_future_and_advice_dropped(self, sent):
        assert self.check(sent)[0] == ""

    @pytest.mark.parametrize(
        "sent",
        ["삼성전자 최근 공시 이후 주가 반응은 다음과 같습니다.", "상세한 뉴스 내용은 아래와 같습니다.", "[수치 생략]만큼 올랐습니다.",
         "주가가 market index와 비슷했습니다.", "excess_pct 값은 양수입니다."],
    )
    def test_residue_filler_and_foreign_dropped(self, sent):
        assert self.check(sent)[0] == ""

    def test_headers_removed_and_good_sentences_kept(self):
        text, dropped = self.check(
            "## 사건 이후 주가 반응\n다른 사건과 겹쳐 있지만 1거래일간 -0.72% 움직였습니다. 공시 때문에 올랐습니다.\n- 거래량은 평균의 1.55배였지만 다른 사건과 겹칩니다."
        )
        assert text == "다른 사건과 겹쳐 있지만 1거래일간 -0.72% 움직였습니다.\n- 거래량은 평균의 1.55배였지만 다른 사건과 겹칩니다."
        assert len(dropped) == 1

    def test_quoted_title_numbers_are_not_checked(self):
        r = _result([_event(title="2분기 영업이익 7조원 돌파")])
        text, dropped = check_event_answer("'2분기 영업이익 7조원 돌파' 공시는 다른 사건과 겹치지만 1거래일간 -0.72% 움직였습니다.", r, ())
        assert dropped == []

    def test_event_scoped_numbers(self):
        # 7월 30일 공시의 수치에 9월 24일 뉴스의 수치를 붙이면 지운다
        text, dropped = self.check("7월 30일 공시 이후 1거래일간 -5.76% 움직였습니다.")
        assert text == "" and dropped

    @pytest.mark.parametrize(
        "sent",
        ["* 뉴스 3: 코스피 기사", "- 공시 1. 특수관계인에대한출자", "두 번째 뉴스는 반도체 기사입니다.",
         '- 뉴스 타이틀 4: "외인 매도 기사"', "- 뉴스 제목 2: 기사", "3번 뉴스는 반도체 기사입니다."],
    )
    def test_enumerated_relisting_dropped(self, sent):
        # slow 측정에서 나온 사례: 결과에 없는 번호("뉴스 4")로 사건 제목을 다시 나열
        text, dropped = self.check(sent)
        assert text == "" and dropped[0][1] == "임의 번호로 사건 나열"

    @pytest.mark.parametrize(
        "sent, reason",
        [
            # slow 1차 재측정에서 나온 사례들
            ("금호타이어의 주가 움직임은 이 뉴스와 관련이 없습니다.", "인과 단정"),
            ("이 뉴스는 주가와 무관합니다.", "인과 단정"),
            ("- d1: 현재 반응 데이터가 없으므로 반응이 없다고 할 수 있습니다.", None),
            ("뉴스 이후 주가 변화가 없었습니다.", "계산되지 않은 값을 추정"),
        ],
    )
    def test_relation_claims_and_null_as_no_reaction_dropped(self, sent, reason):
        text, dropped = self.check(sent)
        assert text == "" and dropped
        if reason:
            assert dropped[0][1] == reason

    def test_relation_hedge_kept(self):
        sent = "이 공시와 주가 움직임이 관련이 있는지는 알 수 없습니다."  # 규칙 a에도 걸리지 않는다(알 수 없다는 주의 문장)
        assert self.check(sent) == (sent, [])

    def test_numbered_list_relisting_titles(self):
        r = _result([_event(title="가평군 재즈페스티벌 성료"), _event(title="b기사", day="2026-08-01")])
        text, dropped = check_event_answer(
            "다음과 같습니다.\n\n1.\n\n2. \"가평군 재즈페스티벌 성료\"\n3. 공시 이후 1거래일간 -0.72% 움직였지만 다른 사건과 겹칩니다.", r, ()
        )
        assert text == "- 공시 이후 1거래일간 -0.72% 움직였지만 다른 사건과 겹칩니다."
        assert ("2. \"가평군 재즈페스티벌 성료\"", "사건 제목만 다시 나열") in dropped

    def test_decimal_after_event_word_is_not_enumeration(self):
        r = _result([_event(d1=(0.72, 0.1, 0.62))])
        sent = "공시 이후 0.72% 움직였지만 다른 사건과 겹칩니다."
        assert check_event_answer(sent, r, ()) == (sent, [])

    def test_company_name_digits_are_not_numbers(self):
        r = _result([_event()], company_name="KODEX 200")
        text, dropped = check_event_answer("KODEX 200 뉴스는 다른 사건과 겹치지만 1거래일간 -0.72% 움직였습니다.", r, ())
        assert dropped == []

    def test_not_found_result_drops_numbers(self):
        text, dropped = check_event_answer("주가는 3% 올랐습니다.", {"found": False, "message": "x"}, ())
        assert text == ""
