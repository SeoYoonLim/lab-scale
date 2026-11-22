"""2026-11-23 최종 답변 품질 보완(단위, DB·Ollama 없음).

1. 기업 직접 원인과 업종·시장 상황 구분(app/evidence_scope, app/answer_cause.evidence_lines, app/agent LLM 경로 보조 함수)
2. 종목만 바꾼 후속 질문 판정(app/followup_subject: 조사·연결어 판정, 조사 보정, 직전 질문 치환)
3. 번역 검증 실패 요약의 한국어 원문 제목 안내, 번역 머리말 검증(app/quote_i18n, app/i18n_translate)
4. 번역 검증 실패 시 정량 결과 유지(app/quote_i18n.fallback_summary, app/answer_i18n.localize_answer)
5. 근거 없는 근거 절·일반 문장 제거(app/answer_tidy)
DB가 필요한 종목 교체 경로(실제 종목 표·질문 처리)는 tests/test_answer_quality_followup_20261123_devdb.py.
"""
import re
from datetime import datetime
from types import SimpleNamespace as NS

import pytest

from app import agent, answer_cause, answer_i18n, answer_tidy, evidence_scope, followup_subject, i18n_translate, quote_i18n
from app.i18n_translate import Result
from tests.test_answer_cause_round71 import KAKAO, NOW, SAMSUNG, answer, idx_ok, rows_until, window

HYNIX = NS(id=16, name="SK하이닉스", ticker="000660", market="KOSPI", asset_type="stock")
HYNIX_ALIASES = ["SK hynix", "하닉", "SK하닉", "하이닉스", "에스케이하이닉스"]
OTHERS = ["삼성전자", "삼전", "SK스퀘어", "SK", "셀트리온제약", "마이크론", "보령"]
HANGUL = re.compile(r"[가-힣]")


def classify(title, content=None, name="SK하이닉스", aliases=HYNIX_ALIASES, others=OTHERS):
    return evidence_scope.classify(name, title, content, aliases, others)


# ---------- 1. 기사 범위 판정 ----------

@pytest.mark.parametrize("title, content, scope", [
    # 이 기업 하나를 주어로 다룬 기사
    ("SK하이닉스, 美 매출 1년 새 102조원 급증…전체 매출 60%", "SK하이닉스의 미국 매출이 1년 새 102조원 늘었다.", evidence_scope.COMPANY),
    ("\"SK하이닉스 ADR 반등, 엔비디아 실적에 달렸다…관건은 HBM 수요\"", None, evidence_scope.COMPANY),
    # 여러 기업을 함께 다룬 업종 기사(제목에 기업명이 있어도 직접 원인 근거가 아니다)
    ("삼성전자·SK하이닉스, '현금 329조 돌파'...AI 슈퍼사이클 힘입은 사상...", None, evidence_scope.SECTOR),
    ("AI 수요 폭발로 삼성전자·SK하이닉스 실적 급등, 나머지 기업은?", None, evidence_scope.SECTOR),
    ("한투운용 ACE 글로벌반도체 ETF, 비중 1위 교체…SK하닉 대신 삼전", None, evidence_scope.SECTOR),
    ("500대 기업 3분기 영업익 236%↑…삼전닉스 빼면 1.7%↑그쳐", None, evidence_scope.SECTOR),
    # 시장 기사
    ("코스피, 美 AI주 급락에 하락 출발…삼전닉스 동반 약세", None, evidence_scope.MARKET),
    ("美 AI주 급락에 국내 반도체주도 출렁…삼성전자·SK하이닉스 프리마켓...", None, evidence_scope.MARKET),
    ("젠슨 황 사임 여파에 뉴욕증시 일제히 하락…나스닥 2.49%↓", "SK하이닉스 ADR(-7.29%) 등도 함께 하락했다.", evidence_scope.MARKET),
    # 제목에 이름이 있어도 본문 요약이 이 기업을 다루지 않으면 직접 근거가 아니다
    ("SK하이닉스 협력사 행사 성료", "지역 상공회의소가 주최한 행사가 열렸다. 참가 기업은 모두 지역 중소기업이다.", evidence_scope.UNCONFIRMED),
    ("신한카드, 포인트 자동투자 서비스 출시", "애플, 아마존 등 34개 종목에 투자할 수 있다.", evidence_scope.UNCONFIRMED),
])
def test_classify_scope(title, content, scope):
    assert classify(title, content) == scope


def test_longer_other_company_name_is_not_this_company():
    # '셀트리온제약' 안의 '셀트리온'은 셀트리온 기사가 아니다
    assert evidence_scope.classify("셀트리온", "보령-셀트리온제약, 당뇨병 치료제 공동판매", None, [], OTHERS) != evidence_scope.COMPANY
    assert evidence_scope.classify("셀트리온", "셀트리온 '허쥬마', 7년 연속 브라질 연방정부 입찰 수주", None, [], OTHERS) == evidence_scope.COMPANY
    # 'SK하이닉스' 안의 'SK'는 다른 기업이 아니다
    assert classify("SK하이닉스, HBM4 양산 돌입", "SK하이닉스가 HBM4 양산을 시작했다.") == evidence_scope.COMPANY


def test_annotate_keeps_existing_scope_and_fills_missing():
    items = [{"title": "SK하이닉스, HBM4 양산 돌입"}, {"title": "코스피 급락", "scope": evidence_scope.COMPANY}]
    evidence_scope.annotate(items, "SK하이닉스", HYNIX_ALIASES, OTHERS)
    assert [i["scope"] for i in items] == [evidence_scope.COMPANY, evidence_scope.COMPANY]


def _news(title, scope, day="2026-11-17", source="www.example.kr"):
    return {"title": title, "scope": scope, "date": day, "source": source, "published_at": f"{day}T10:00:00+09:00"}


SECTOR_NEWS = _news("삼성전자·SK하이닉스, '현금 329조 돌파'", evidence_scope.SECTOR)
MARKET_NEWS = _news("美 AI주 급락에 국내 반도체주도 출렁", evidence_scope.MARKET)
DIRECT_NEWS = _news("SK하이닉스, HBM4 양산 돌입", evidence_scope.COMPANY)


def test_evidence_lines_sector_only_does_not_name_a_company_cause():
    found = {"news": [SECTOR_NEWS, MARKET_NEWS], "disclosures": [], "context_candidates": 5, "unconfirmed_news": 7}
    lines = answer_cause.evidence_lines("SK하이닉스", "기사·공시", "2026-11-13 장 마감 뒤~2026-11-17 장 마감 15:30 전", found, -9.7)
    text = "\n".join(lines)
    assert "SK하이닉스를 직접 다룬 기사·공시를 찾지 못했습니다" in text
    assert "SK하이닉스의 개별적인 하락 원인을 특정하기 어렵습니다" in text
    assert "업종·시장 참고" in text and "SK하이닉스의 개별 원인으로 보지 않습니다" in text
    assert "[업종·산업] 뉴스 2026-11-17 (www.example.kr) \"삼성전자·SK하이닉스, '현금 329조 돌파'\"" in text
    assert "[시장] 뉴스 2026-11-17" in text
    assert "업종·시장 기사 3건과" in text and "확인되지 않은 기사 7건은 원인 근거로 쓰지 않았습니다" in text
    assert "직접 다룬 기사·공시입니다" not in text  # 업종 기사를 직접 근거로 부르지 않는다


def test_evidence_lines_direct_evidence_is_cited_separately():
    found = {"news": [DIRECT_NEWS, SECTOR_NEWS], "disclosures": [{"title": "분기보고서 (2026.09)", "date": "2026-11-16"}]}
    text = "\n".join(answer_cause.evidence_lines("SK하이닉스", "기사·공시", "창", found, -9.7))
    assert "SK하이닉스를 직접 다룬 기사·공시입니다. 주가 변동의 직접 원인이라고 단정할 수는 없습니다." in text
    assert "  - 뉴스 2026-11-17 (www.example.kr) \"SK하이닉스, HBM4 양산 돌입\"" in text
    assert "  - 공시 2026-11-16 접수 \"분기보고서 (2026.09)\"" in text
    # 업종 기사는 직접 근거 목록이 아니라 참고 목록에만 있다
    direct_part, ref_part = text.split("업종·시장 참고")
    assert "현금 329조" not in direct_part and "현금 329조" in ref_part
    assert "특정하기 어렵습니다" not in text


def test_evidence_lines_nothing_found_makes_no_titles():
    text = "\n".join(answer_cause.evidence_lines("SK하이닉스", "기사·공시", "창", {"news": [], "disclosures": []}, -1.0))
    assert text == "- 같은 기간(창) SK하이닉스를 직접 다룬 기사·공시를 찾지 못해 움직임의 원인을 특정할 수 없습니다."


def test_period_cause_answer_with_sector_only_news():
    """시나리오 1: 업종 뉴스만 있는 SK하이닉스 하락 원인 → 업종 상황과 기업 직접 원인을 구분."""
    rows = rows_until(price=lambda i: 2000.0 - i * 3)
    win = window("SK하이닉스 지난주 왜 떨어졌어?", rows)
    found = {"news": [SECTOR_NEWS], "disclosures": [], "news_candidates": 4, "direct_candidates": 0, "context_candidates": 3,
             "unconfirmed_news": 1, "cutoff": "15:30"}
    text = answer(HYNIX, "SK하이닉스 지난주 왜 떨어졌어?", win, idx_ok(win, -6.0), found)
    assert "SK하이닉스(종목) 주가:" in text and "같은 기간 코스피 지수:" in text
    assert "개별적인 하락 원인을 특정하기 어렵습니다" in text
    assert "[업종·산업] 뉴스" in text


def test_period_cause_answer_with_direct_news_quotes_it():
    """시나리오 2: 직접 기사·공시가 있으면 실제 근거를 인용한다(지어낸 인과는 없다)."""
    win = window("셀트리온 지난주 급등 이유")
    found = {"news": [_news("셀트리온 '허쥬마', 7년 연속 브라질 연방정부 입찰 수주", evidence_scope.COMPANY)],
             "disclosures": [], "news_candidates": 3, "direct_candidates": 2, "cutoff": "15:30"}
    cell = NS(id=12, name="셀트리온", ticker="068270", market="KOSPI", asset_type="stock")
    text = answer(cell, "셀트리온 지난주 급등 이유", win, None, found)
    assert "셀트리온을 직접 다룬 기사·공시입니다(직접 다룬 기사 2건 중 1건)" in text
    assert "\"셀트리온 '허쥬마', 7년 연속 브라질 연방정부 입찰 수주\"" in text
    assert "단정할 수는 없습니다" in text


def test_llm_path_scope_helpers():
    records = [
        {"tool_name": "stock_tool", "result": {"company_name": "SK하이닉스", "found": True}},
        {"tool_name": "news_tool", "result": {"company_name": "SK하이닉스", "found": True, "news": [dict(SECTOR_NEWS), dict(MARKET_NEWS)]}},
    ]
    assert agent._has_direct_evidence(records) is False
    lines = agent._reference_lines(records)
    assert lines[0] == "- [업종·산업] 2026-11-17 \"삼성전자·SK하이닉스, '현금 329조 돌파'\" (www.example.kr)"
    llm = agent._news_scope_for_llm(records[1]["result"])
    assert all("scope" not in n for n in llm["news"]) and llm["news"][0]["관련성"].startswith("업종·산업")
    assert "이 종목의 개별 원인은 특정할 수 없다" in llm["note"]
    assert records[1]["result"]["news"][0]["scope"] == evidence_scope.SECTOR  # 기록 원본은 그대로
    records.append({"tool_name": "disclosure_tool", "result": {"found": True, "disclosures": [{"title": "주요사항보고서"}]}})
    assert agent._has_direct_evidence(records) is True
    # 범위를 정하지 못한 기록(판정 실패)은 예전 규칙(None)
    assert agent._has_direct_evidence([{"tool_name": "news_tool", "result": {"news": [{"title": "x"}]}}]) is None


# ---------- 5. 근거 없는 근거 절·일반 문장 ----------

LLM_NO_SOURCE = """## 주가 동향
SK하이닉스는 최근 5거래일 -9.41%입니다.

## 뉴스·공시 근거
뉴스나 공시에서 이유를 찾을 수 있습니다. 관련 기사를 참고하면 원인을 파악할 수 있습니다.

## 위험요인
변동성이 큽니다."""


def test_no_sources_drops_evidence_section_and_notes_no_cause():
    """시나리오 3: 뉴스·공시가 없는 원인 질문 → 빈 근거 절과 일반적인 가짜 설명 없음."""
    text, changed = answer_tidy.enforce_cause_section(LLM_NO_SOURCE, False, False, cause_question=True)
    assert changed
    assert "## 뉴스·공시 근거" not in text and "이유를 찾을 수 있습니다" not in text
    assert text.endswith(answer_tidy.NO_CAUSE_EVIDENCE_NOTE)
    assert "SK하이닉스는 최근 5거래일 -9.41%입니다." in text and "## 위험요인" in text


def test_no_sources_old_call_still_only_touches_cause_section():
    """예전 호출(새 인자 없음)은 원인 절만 바꾼다(2026-11-21 동작 유지)."""
    src = "## 주가 동향\n-3%입니다.\n\n## 원인 분석\n수요 둔화 때문입니다."
    text, changed = answer_tidy.enforce_cause_section(src, False, False)
    assert changed and "수요 둔화" not in text and answer_tidy.NO_CAUSE_EVIDENCE_NOTE in text


def test_no_sources_evidence_section_saying_none_is_kept():
    """뉴스 질문에 결과가 없다고 밝힌 근거 절은 사실이라 지우지 않는다."""
    src = "## 뉴스·공시 근거\n최근 7일 동안 SK하이닉스 관련 뉴스가 없습니다."
    assert answer_tidy.enforce_cause_section(src, False, False) == (src, False)


def test_no_sources_only_generic_evidence_section_leaves_short_note():
    text, changed = answer_tidy.enforce_cause_section("## 뉴스·공시 근거\n뉴스나 공시에서 이유를 찾을 수 있습니다.", False, False)
    assert changed and text == answer_tidy.NO_EVIDENCE_NOTE


def test_no_sources_existing_no_cause_sentence_is_not_repeated():
    src = "## 주가 동향\n-3%입니다. 관련 뉴스·공시가 없어 원인을 특정할 수 없습니다."
    text, changed = answer_tidy.enforce_cause_section(src, False, False, cause_question=True)
    assert not changed and text == src


def test_sector_only_sources_replace_cause_and_evidence_sections():
    src = ("## 주가 동향\nSK하이닉스는 -9.70%입니다.\n\n## 원인 분석\n반도체 업종 전반의 조정과 코스피 지수 -6.09% 하락으로 SK하이닉스가 내렸습니다.\n\n"
           "## 뉴스·공시 근거\n미국 AI 관련주 하락이 원인입니다.")
    refs = ["- [업종·산업] 2026-11-17 \"삼성전자·SK하이닉스, '현금 329조 돌파'\" (www.example.kr)"]
    text, changed = answer_tidy.enforce_cause_section(src, True, True, direct_evidence=False, references=refs, cause_question=True,
                                                      subject="SK하이닉스")
    assert changed
    assert "미국 AI 관련주 하락이 원인입니다" not in text and "## 원인 분석" not in text
    assert answer_tidy.NO_DIRECT_EVIDENCE_NOTE.format(name="SK하이닉스") in text
    assert "## 시장과 비교" in text and "-6.09%" in text  # 조회된 지수 수치 문장은 남는다
    assert "## 업종·시장 참고" in text and refs[0] in text


def test_direct_sources_keep_llm_answer():
    src = "## 원인 분석\nHBM4 양산 기사가 있었습니다.\n\n## 뉴스·공시 근거\n- SK하이닉스, HBM4 양산 돌입"
    assert answer_tidy.enforce_cause_section(src, True, False, direct_evidence=True, cause_question=True) == (src, False)
    assert answer_tidy.enforce_cause_section(src, True, False) == (src, False)


@pytest.mark.parametrize("sentence", [
    "뉴스나 공시에서 이유를 찾을 수 있습니다.",
    "관련 뉴스와 공시를 확인해 보시기 바랍니다.",
    "공시를 통해 원인을 확인할 수 있습니다.",
])
def test_generic_evidence_sentences_are_dropped(sentence):
    text, removed = answer_tidy.strip_generic(f"SK하이닉스는 -3.10% 하락했습니다. {sentence}")
    assert removed == 1 and text == "SK하이닉스는 -3.10% 하락했습니다."


def test_specific_evidence_sentence_is_kept():
    s = "2026-11-17 \"SK하이닉스, HBM4 양산 돌입\" 기사가 있었습니다."
    assert answer_tidy.strip_generic(s) == (s, 0)


# ---------- 2. 종목만 바꾼 후속 질문 ----------

def _spans(text, *names):
    return [(text.index(n), text.index(n) + len(n)) for n in names]


@pytest.mark.parametrize("text, name, expected", [
    ("SK하이닉스는?", "SK하이닉스", True),
    ("그럼 SK하이닉스는?", "SK하이닉스", True),
    ("SK하이닉스도 분석해줘", "SK하이닉스", True),
    ("LG에너지솔루션은 어때?", "LG에너지솔루션", True),
    ("What about SK하이닉스 ?", "SK하이닉스", True),
    ("SK하이닉스 は?", "SK하이닉스", True),
    ("SK하이닉스 呢?", "SK하이닉스", True),
    ("SK하이닉스 전망은?", "SK하이닉스", False),  # 새 의도가 있으면 새 질문이 우선
    ("SK하이닉스 뉴스는?", "SK하이닉스", False),
    ("SK하이닉스는 5거래일?", "SK하이닉스", False),
    ("SK하이닉스는 왜 떨어졌어?", "SK하이닉스", False),
    ("Show me news about SK하이닉스", "SK하이닉스", False),
])
def test_is_subject_only(text, name, expected):
    assert followup_subject.is_subject_only(text, _spans(text, name)) is expected


def test_is_subject_only_needs_a_mention():
    assert followup_subject.is_subject_only("그럼?", []) is False


@pytest.mark.parametrize("name, after, expected", [
    ("LG에너지솔루션", "는 왜?", "은 왜?"), ("SK하이닉스", "은?", "는?"), ("카카오", "을 분석", "를 분석"), ("셀트리온", "가 왜", "이 왜"),
    ("NAVER", "는?", "는?"),  # 한글로 끝나지 않으면 그대로
    ("셀트리온", "이번 주", "이번 주"),  # 조사가 아니라 낱말의 첫 글자
    ("셀트리온", " 최근", " 최근"),
])
def test_fix_josa(name, after, expected):
    assert followup_subject.fix_josa(name, after) == expected


class _Hits:
    """followup_subject._mentions를 대신하는 가짜: 등록 종목 이름이 글에 나오는 위치(DB 없이)."""

    def __init__(self, companies):
        self.companies = companies

    def __call__(self, db, text):
        hits = []
        for c in self.companies:
            st = text.find(c.name)
            while st >= 0:
                hits.append((st, st + len(c.name), c))
                st = text.find(c.name, st + 1)
        return text, sorted(hits, key=lambda h: h[0])


@pytest.fixture
def fake_mentions(monkeypatch):
    class _DB:
        def close(self):
            pass

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _DB())
    monkeypatch.setattr(followup_subject, "_mentions", _Hits([SAMSUNG, HYNIX, KAKAO]))
    return monkeypatch


def test_rewrite_swaps_only_the_company(fake_mentions):
    """시나리오 4: 삼성전자 20거래일 분석 후 "SK하이닉스는?" → 같은 분석 의도·기간의 SK하이닉스 질문."""
    prev = {"question": "삼성전자 최근 20거래일 수익률 분석해 줘.", "company_name": "삼성전자"}
    out = followup_subject.rewrite("SK하이닉스는?", prev)
    assert out == {"question": "SK하이닉스 최근 20거래일 수익률 분석해 줘.", "from": "삼성전자", "to": "SK하이닉스"}
    assert "삼성전자" not in out["question"]


def test_rewrite_keeps_new_intent_and_same_company(fake_mentions):
    prev = {"question": "삼성전자 최근 20거래일 수익률 분석해 줘."}
    assert followup_subject.rewrite("SK하이닉스는 왜 떨어졌어?", prev) is None  # 다른 의도를 밝혔다
    assert followup_subject.rewrite("삼성전자는?", prev) is None  # 종목이 그대로다
    assert followup_subject.rewrite("SK하이닉스는?", None) is None  # 후속 질문이 아니다
    assert followup_subject.rewrite("SK하이닉스는?", {"question": "삼성전자와 카카오 비교해 줘"}) is None  # 직전 종목이 둘


def test_rewrite_follows_chain_of_subject_only_questions(fake_mentions):
    """"삼성전자 20거래일" → "SK하이닉스는?" → "카카오는?": 두 번째 후속 질문도 처음 분석 의도를 잇는다(같은 사용자의 리포트만)."""
    older = {"report_id": 1, "question": "삼성전자 최근 20거래일 수익률 분석해 줘.", "previous_report_id": None}
    calls = []

    def get_report(report_id, user_id=None):
        calls.append((report_id, user_id))
        return older if report_id == 1 else None

    fake_mentions.setattr("app.reports.get_report", get_report)
    prev = {"report_id": 2, "question": "SK하이닉스는?", "previous_report_id": 1}
    assert followup_subject.rewrite("카카오는?", prev, user_id=7)["question"] == "카카오 최근 20거래일 수익률 분석해 줘."
    assert calls == [(1, 7)]
    assert followup_subject.rewrite("카카오는?", prev, user_id=None) is None  # 소유자를 모르면 거슬러 가지 않는다


def test_rewrite_survives_db_error(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.session.SessionLocal", boom)
    assert followup_subject.rewrite("SK하이닉스는?", {"question": "삼성전자 분석"}) is None


def test_ask_question_uses_rewritten_question_without_previous_context(monkeypatch):
    """종목 교체 질문은 직전 대화(previous)를 넘기지 않고 바꾼 질문으로 답한다(직전 종목 수치·기사 혼입 방지)."""
    seen = {}

    def fake_answer(question, model, previous=None):
        seen.update(question=question, previous=previous)
        return {"answer": "답", "used_tools": [], "sources": []}, []

    monkeypatch.setattr(agent.followup_subject, "rewrite",
                        lambda q, prev, uid, normalize=None: {"question": "SK하이닉스 최근 20거래일 수익률 분석해 줘.", "from": "삼성전자",
                                                              "to": "SK하이닉스"})
    monkeypatch.setattr(agent, "_answer", fake_answer)
    monkeypatch.setattr(agent, "_instrument_fields", lambda q, r, p: {"resolved_instruments": [], "clarification_required": False,
                                                                       "clarification_reason": None})
    monkeypatch.setattr(agent.answer_i18n, "_company_names", lambda: ())
    prev = {"report_id": 3, "question": "삼성전자 최근 20거래일 수익률 분석해 줘.", "answer": "삼성전자 -10.02%", "company_name": "삼성전자"}
    r = agent.ask_question("SK하이닉스는?", save_report=False, previous=prev)
    assert seen == {"question": "SK하이닉스 최근 20거래일 수익률 분석해 줘.", "previous": None}
    assert r["meta"]["subject_swap"]["to"] == "SK하이닉스" and r["previous_report_id"] == 3


# ---------- 4. 번역 검증 실패 시 정량 결과 유지 ----------

def _cause_period_records(pct=-7.62, idx_pct=-6.09):
    win = {"status": "ok", "from_date": "2026-11-13", "end_date": "2026-11-17", "from_close": 105000.0, "end_close": 97000.0,
           "pct": pct, "trading_days": 2, "last_stored": "2026-11-17", "missing_tail": ["2026-11-18", "2026-11-19", "2026-11-20"]}
    stock = {"company_name": "삼성전자", "ticker": "005930", "found": True, "called_via": "cause_period", "window": win,
             "period_start_date": "2026-11-13", "period_end_date": "2026-11-17", "period_change_pct": pct}
    market = {"company_name": "삼성전자", "found": True, "called_via": "cause_period", "index_name": "코스피", "index_code": "KS11",
              "start_date": "2026-11-13", "end_date": "2026-11-17", "market_change_pct": idx_pct, "stock_change_pct": pct}
    news = {"company_name": "삼성전자", "found": True, "called_via": "cause_period",
            "news": [_news("삼성전자·SK하이닉스, '현금 329조 돌파'", evidence_scope.SECTOR)]}
    return [{"tool_name": "stock_tool", "result": stock}, {"tool_name": "market_tool", "result": market},
            {"tool_name": "news_tool", "result": news}]


NOW_KST = datetime(2026, 11, 22, 12, 0, tzinfo=NOW.tzinfo)


@pytest.mark.parametrize("locale, pct, idx_text", [
    ("en-US", "-7.62%", "KOSPI over the same dates: -6.09%"),
    ("ja-JP", "-7.62%", "同じ期間のKOSPI指数：-6.09%"),
    ("zh-CN", "-7.62%", "同期KOSPI指数：-6.09%"),
])
def test_fallback_summary_keeps_period_change_and_index(locale, pct, idx_text):
    """시나리오 6: 영어(·일본어·중국어) 원인 답의 번역 검증 실패 → 등락률·기간·지수 비교가 남는다."""
    text = quote_i18n.fallback_summary(_cause_period_records(), locale, now=NOW_KST)
    assert "2026-11-13" in text and "2026-11-17" in text and pct in text and idx_text in text
    assert quote_i18n._CAUSE_NOTE[locale] in text
    assert quote_i18n._NO_DIRECT[locale].format(name="삼성전자") in text  # 업종 기사뿐이라 개별 원인 없음
    assert quote_i18n._TITLES_REF[locale] in text
    assert quote_i18n._TAIL[locale].format(n=3) in text
    # 한국어 문장을 내보내지 않는다(한글은 종목명과 원문 기사 제목뿐)
    body = text.replace("삼성전자", "").replace("SK하이닉스", "")
    body = "\n".join(line for line in body.splitlines() if not line.startswith("- ") or not HANGUL.search(line.split("(")[0]))
    assert not HANGUL.search(body.replace("'현금 329조 돌파'", ""))


def test_fallback_summary_numbers_match_korean_answer():
    """한국어 답과 번역 실패 요약의 종목·기간·수치가 같다(같은 기록에서 만든다)."""
    rows = rows_until(price=lambda i: 2000.0 - i * 3)
    win = window("삼성전자 지난주 왜 떨어졌어?", rows)
    idx = idx_ok(win, -2.0)
    korean = answer(SAMSUNG, "삼성전자 지난주 왜 떨어졌어?", win, idx, {"news": [], "disclosures": []})
    stock = {"company_name": "삼성전자", "found": True, "called_via": "cause_period", "window": win,
             "period_start_date": win["from_date"], "period_end_date": win["end_date"], "period_change_pct": win["pct"]}
    market = {"company_name": "삼성전자", "found": True, "called_via": "cause_period", "index_name": idx["name"],
              "start_date": idx["from_date"], "end_date": idx["end_date"], "market_change_pct": idx["pct"], "stock_change_pct": win["pct"]}
    english = quote_i18n.fallback_summary([{"tool_name": "stock_tool", "result": stock}, {"tool_name": "market_tool", "result": market}],
                                          "en-US", now=NOW_KST)
    pct = f"{win['pct']:+.2f}%"
    for value in (pct, win["from_date"], win["end_date"], f"{idx['pct']:+.2f}%", f"{win['end_close']:,.0f}"):
        assert value in korean and value in english, value


def test_fallback_summary_price_records_keep_range_and_old_sentence():
    prices = [{"price_date": "2026-11-13", "close_price": 100.0, "change_pct": 1.0, "volume": 10},
              {"price_date": "2026-11-14", "close_price": 120.0, "change_pct": 20.0, "volume": 11},
              {"price_date": "2026-11-17", "close_price": 90.0, "change_pct": -25.0, "volume": 12}]
    stock = {"company_name": "삼성전자", "found": True, "prices": prices, "period_start_date": "2026-11-13",
             "period_end_date": "2026-11-17", "trading_days": 2, "period_change_pct": -10.0}
    text = quote_i18n.fallback_summary([{"tool_name": "stock_tool", "result": stock}], "en-US", now=NOW_KST)
    assert "삼성전자: close" in text  # 기존 마지막 시세 문장
    assert "삼성전자: 2026-11-13 close → 2026-11-17 close, -10.00% (2 trading days)." in text
    assert "Closing high in this period:" in text and "(2026-11-14)" in text and "(2026-11-17)" in text
    assert quote_i18n._CAUSE_NOTE["en-US"] not in text  # 원인 답이 아니면 원인 안내를 붙이지 않는다


def test_fallback_summary_without_data_invents_nothing():
    assert quote_i18n.fallback_summary([], "en-US", now=NOW_KST).startswith("※ We couldn't verify an accurate translation of this answer")
    text = quote_i18n.fallback_summary([{"tool_name": "stock_tool", "result": {"found": False, "company_name": "삼성전자"}}], "en-US",
                                       now=NOW_KST)
    assert "%" not in text and "close" not in text


def test_localize_answer_failure_and_success_paths(monkeypatch):
    """시나리오 6·8: 번역 검증 실패면 정량 요약(fallback_summary), 성공이면 기존 번역 그대로."""
    records = _cause_period_records()
    monkeypatch.setattr(answer_i18n, "_company_names", lambda: ("삼성전자",))
    monkeypatch.setattr(answer_i18n, "translate", lambda src, loc, terms: Result("※ 원문", i18n_translate.STATUS_FALLBACK, ["hangul left"]))
    text, status = answer_i18n.localize_answer("Why did Samsung Electronics fall this week?", "삼성전자 -7.62% ...", records, "en-US")
    assert status == answer_i18n.STATUS_FALLBACK_SUMMARY and "-7.62%" in text and "KOSPI over the same dates: -6.09%" in text
    monkeypatch.setattr(answer_i18n, "translate",
                        lambda src, loc, terms: Result("Samsung Electronics fell -7.62%.", i18n_translate.STATUS_TRANSLATED))
    assert answer_i18n.localize_answer("q", "삼성전자 -7.62%", records, "en-US") == ("Samsung Electronics fell -7.62%.", "translated")


# ---------- 3. 원문 제목 안내·번역 머리말 ----------

@pytest.mark.parametrize("locale, words", [
    ("en-US", "original Korean headlines"), ("ja-JP", "韓国語の原文見出し"), ("zh-CN", "韩语原文标题")])
def test_fallback_titles_say_original_korean(locale, words):
    """시나리오 7: 번역 검증 실패 요약의 기사 제목은 한국어 원문이라고 밝힌다(제목은 번역하지 않는다)."""
    news = {"company_name": "SK하이닉스", "found": True, "news": [_news("SK하이닉스, HBM4 양산 돌입", evidence_scope.COMPANY)]}
    text = quote_i18n.fallback_summary([{"tool_name": "news_tool", "result": news}], locale, now=NOW_KST)
    assert words in text and "- SK하이닉스, HBM4 양산 돌입 (2026-11-17)" in text


@pytest.mark.parametrize("translated", [
    "I'd be happy to help with the translation. Here is the translation of the given Korean text into English:\n\nSorry.",
    "Here is the translation:\nSamsung fell ⟦0⟧.",
    "以下は翻訳です。\nサムスン電子は⟦0⟧下落しました。",
    "以下是翻译：\n三星电子下跌⟦0⟧。",
])
def test_translator_preamble_is_rejected(translated):
    locale = "ja-JP" if "サムスン" in translated else "zh-CN" if "三星" in translated else "en-US"
    src = "삼성전자는 ⟦0⟧ 하락했습니다." if "⟦0⟧" in translated else "죄송합니다."
    assert "translator preamble" in i18n_translate.check(src, translated, locale)


def test_normal_translation_still_passes_check():
    assert i18n_translate.check("삼성전자는 ⟦0⟧ 하락했습니다.", "Samsung Electronics fell ⟦0⟧.", "en-US") == []
    assert i18n_translate.check("번역 결과를 확인했습니다.", "We checked the translation results.", "en-US") == []
