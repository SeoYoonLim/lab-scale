"""(C) 용어 사전과 개념 질문 판정(app/glossary.py)과 에이전트 연결(종목 질문은 기존 흐름)."""

import re

import pytest

from app import agent
from app.glossary import GLOSSARY, concept_answer, find_term, term_list
from evals.metrics import forbidden_hits


def test_glossary_has_about_30_terms():
    assert 30 <= len(GLOSSARY) <= 40


@pytest.mark.parametrize("term", list(GLOSSARY))
def test_definitions_are_short_and_safe(term):
    _, definition = GLOSSARY[term]
    sentences = [s for s in re.split(r"(?<=[.])\s+", definition) if s.strip()]
    assert 1 <= len(sentences) <= 4, term
    assert forbidden_hits(definition) == []
    assert not re.search(r"추천|사세요|매수하세요|유망", definition)
    # 숫자 예시는 "예를 들어"로만(계산식의 100, 제도의 ±30%·기준 지수 100, 결제 2영업일은 사실)
    if re.search(r"\d[\d,]*원", definition):
        assert "예를 들어" in definition, term


@pytest.mark.parametrize("q, term", [
    ("PER이 뭐야?", "PER(주가수익비율)"), ("per이 뭐야", "PER(주가수익비율)"), ("주가수익비율이 뭐야", "PER(주가수익비율)"),
    ("PBR이 뭐야?", "PBR(주가순자산비율)"), ("ROE가 무슨 뜻이야?", "ROE(자기자본이익률)"), ("배당락이 뭐야?", "배당락"),
    ("공모주가 뭔지 알려줘", "공모주"), ("시가총액이 뭐야?", "시가총액"), ("공매도가 뭐야?", "공매도"),
    ("배당수익률이 뭔지 설명해줘", "배당수익률"), ("레버리지가 뭐야?", "레버리지 ETF"), ("신용융자란?", "신용융자(신용거래융자)"),
    ("상한가 뜻이 뭐야", "상한가"), ("코스피 지수가 뭐야?", "코스피(KOSPI)"),
])
def test_definition_questions_use_the_glossary(q, term):
    answer = concept_answer(q)
    assert answer is not None and answer.startswith(f"**{term}**")
    assert GLOSSARY[term][1] in answer and "투자 권유가 아닙니다" in answer


@pytest.mark.parametrize("q", [
    "PER이 높으면 좋은 거야?", "PER 몇 배면 싸?", "코스피 알려줘", "오늘 코스피 어때?", "PER과 PBR 차이가 뭐야?",
    "공모주 청약 일정 알려줘", "배당수익률 높은 종목 추천해줘", "요즘 원달러 환율 어때?", "오늘 많이 오른 종목 뭐야?",
    "최근 공시 뭐 있어?", "삼성전자 PER은 얼마야?",
])
def test_non_definition_questions_are_left_to_the_existing_flow(q):
    assert concept_answer(q) is None


def test_unknown_term_says_it_is_not_in_the_glossary():
    answer = concept_answer("급등주가 뭐야?")
    assert answer.startswith("'급등주'은(는) 내장 용어 사전에 없는 용어")
    assert "PER" in answer  # 있는 용어 목록을 안내


def test_find_term_respects_ascii_boundaries():
    assert find_term("PERFORMANCE가 뭐야") is None
    assert find_term("PER이 뭐야") == "PER(주가수익비율)"
    assert find_term("주가수익비율") == "PER(주가수익비율)"


def test_term_list_is_headwords():
    assert "PER" in term_list() and "공매도" in term_list()


class Co:
    name, asset_type = "삼성전자", "stock"


def test_agent_concept_only_without_company():
    assert agent._answer_concept("PER이 뭐야?", []) is not None
    assert agent._answer_concept("삼성전자 PER이 뭐야?", [Co()]) is None  # 종목 질문은 기존 흐름
    assert agent._answer_concept("PER이 뭐야?", None) is None  # DB 오류면 기존 흐름
    resp, records = agent._answer_concept("PER이 뭐야?", [])
    assert resp["used_tools"] == [] and resp["sources"] == [] and records == []


@pytest.mark.integration
@pytest.mark.parametrize("q", ["삼성전자 PER은 얼마야?", "삼성전자 PER이 뭐야?", "KODEX 레버리지가 뭐야?"])
def test_company_questions_have_companies_so_glossary_is_skipped(q):
    companies = agent._question_companies(q)
    assert companies and agent._answer_concept(q, companies) is None
