"""2026-11-11(43차): 번역 보호 문자열(종목명)을 단어 안의 부분 문자열로 가리지 않는다.

42차 실데이터 검증에서 중국어 답이 "半导体테스的基础设施"처럼 한국어 조각을 남긴 채 translated로 통과했다.
원인: app.answer_i18n이 등록 종목명 중 답변에 부분 문자열로 들어 있는 것(`n in answer`)을 보호 대상으로 넘기고,
app.i18n_translate.mask가 경계 없이 가렸다. "반도체 테스트"의 '테스'(등록 종목 095610)가 ⟦n⟧로 가려져 "⟦n⟧트"가 되고,
LLM이 나머지를 번역한 뒤 되돌리면 '테스'만 남는다. 검증(check)은 자리표시자 밖만 보므로 잡지 못했다.
"""
import re

import pytest

from app import i18n_translate as T

HANGUL = re.compile(r"[가-힣]")


def _fake_zh(messages):
    """자리표시자는 그대로 두고 한글 덩어리만 중국어 글자로 바꾸는 가짜 번역기(규칙은 모두 지킨다)."""
    src = messages[-1]["content"]
    body = src.split("\n\n", 1)[-1] if "⟦" in src else src
    return re.sub(r"[가-힣]+", "中文", body)


@pytest.fixture
def fake_llm(monkeypatch):
    seen = []

    def chat(messages):
        seen.append(messages[-1]["content"])
        return _fake_zh(messages)

    monkeypatch.setattr(T, "_chat", chat)
    return seen


def test_name_inside_a_longer_word_is_not_masked():
    m = T.mask("반도체 테스트 인프라 고도화", "zh-CN", ["테스"])
    assert "⟦" not in m.text and m.values == []


@pytest.mark.parametrize("text_, name", [
    ("테스는 반도체 장비 기업이다", "테스"),
    ("삼성전자의 주가", "삼성전자"),
    ("삼성전자(005930) 종가", "삼성전자"),
    ("SK하이닉스와 삼성전자가 올랐다", "삼성전자"),
    ("종목: 테스", "테스"),
    ("\"삼성전자, AMD와 협력\"라는 기사", "삼성전자, AMD와 협력"),
])
def test_standalone_names_and_titles_are_still_masked(text_, name):
    m = T.mask(text_, "zh-CN", [name])
    assert name in m.values and name not in T._TOKEN_RE.sub("", m.text)


def test_translation_leaves_no_korean_fragment(fake_llm):
    src = "## 뉴스\n삼성전자는 AMD와 반도체 테스트 인프라 고도화 협력을 시작했다."
    res = T.translate(src, "zh-CN", ["삼성전자", "테스"])
    assert res.status == T.STATUS_TRANSLATED
    body = res.text.rsplit("\n\n", 1)[0]  # 끝의 번역 안내 문구 제외
    leftovers = [w for w in re.findall(r"[가-힣]+", body) if w != "삼성전자"]
    assert leftovers == [], leftovers  # 전에는 ['테스']가 남았다
    assert "삼성전자" in body  # 종목명 보호는 그대로
