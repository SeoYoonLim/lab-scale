"""48차 문제 5: 숫자가 많은 문단을 번역할 때 수치·날짜·기업명을 보존하고, 번역 실패로 답 전체가 데이터 요약으로 바뀌는 일을 줄인다.

수정 전 실측(2026-11-13, llama3.1:8b, 36문항 중 10건이 fallback_summary): 실패 원인은 숫자를 바꾼 것이 아니라 형식이었다.
- 자리표시자 괄호 훼손: "⟦2⟧원" -> "⟦2⟩円" (자리표시자 4/5 + 밖의 숫자) — n01
- 제목 미번역: "## 주가 동향", "## 관련ニュース" (한글 남음) — e07·d06·n01 …
- '하루'를 "1日"로 옮겨 원문에 없는 숫자가 생김 — e07
- 한 문단이 실패하면 답 전체가 데이터 요약이 됐다(문단·줄 단위로 다시 시도하지 않았다).
숫자·날짜·이름은 여전히 코드가 되돌린다(LLM은 자리표시자만 본다). 아래 테스트는 LLM을 가짜 함수로 바꿔 끼운다.
"""

import re

import pytest

from app import i18n_translate as T

SRC = ("## 주가 동향\n"
       "SK하이닉스의 2026-11-12 종가는 1,254,000원입니다. 전일 대비 등락률은 -1.80%, 거래량은 4,238,276주입니다.\n\n"
       "## 원인 분석\n"
       "마지막 거래일(2026-11-12) 하루 등락률은 -1.80%입니다.")
NAMES = ["SK하이닉스"]


def _nums(s):
    return sorted(x.replace(",", "") for x in re.findall(r"\d[\d,]*(?:\.\d+)?", s))


def fake_translator(convert):
    """마스킹된 입력의 한국어를 일본어 비슷한 글로 바꾸는 가짜 LLM. convert(masked)->out 으로 오류를 흉내 낸다."""
    def chat(messages):
        return convert(messages[1]["content"])  # 재시도 때도 원래 입력(messages[1])을 다시 옮긴다
    return chat


def to_ja(masked: str) -> str:
    # 한글을 가나로 바꾼다(번역 흉내). 자리표시자와 줄 구조는 그대로
    return re.sub(r"[가-힣]+", "テキスト", masked)


def test_bracket_variants_are_repaired_not_rejected(monkeypatch):
    # 모델이 닫는 괄호를 ⟩, ], 】로 바꿔 쓴 경우: 번호가 유효하면 같은 자리표시자로 본다(값은 코드가 되돌린다)
    def broken(masked):
        out = to_ja(masked)
        out = out.replace("⟦2⟧", "⟦2⟩").replace("⟦3⟧", "[3]").replace("⟦4⟧", "【4】")
        return out
    monkeypatch.setattr(T, "_chat", fake_translator(broken))
    r = T.translate(SRC, "ja-JP", NAMES)
    assert r.status == T.STATUS_TRANSLATED, r.failures
    assert _nums(r.text.split("※")[0]) == _nums(SRC)


def test_known_headings_are_translated_by_code(monkeypatch):
    # 모델이 제목을 옮기지 않아도(한글 남음) 정해진 섹션 제목은 코드가 옮긴다
    def keep_headings(masked):
        return "\n".join(line if line.startswith("#") else to_ja(line) for line in masked.split("\n"))
    monkeypatch.setattr(T, "_chat", fake_translator(keep_headings))
    r = T.translate(SRC, "ja-JP", NAMES)
    assert r.status == T.STATUS_TRANSLATED, r.failures
    assert "## 株価動向" in r.text and "## 原因分析" in r.text and "주가 동향" not in r.text


@pytest.mark.parametrize("locale, heading", [("en-US", "## Price Trend"), ("zh-CN", "## 股价走势"), ("ja-JP", "## 株価動向")])
def test_heading_dictionary(locale, heading):
    m = T.mask("## 주가 동향\n본문", locale, [])
    assert heading in T.unmask(m.text, m.values)


def test_day_wording_hint_is_in_the_prompt():
    prompt = T._prompt("마지막 거래일(⟦0⟧) 하루 등락률은 ⟦1⟧입니다.", "ja-JP")[0]["content"]
    assert "하루 등락률 → 当日の騰落率" in prompt


def test_failed_paragraph_is_retried_line_by_line(monkeypatch):
    # 문단 전체로는 매번 자리표시자 하나를 빠뜨리지만(긴 입력), 한 줄씩이면 정확한 모델
    def flaky(masked):
        out = to_ja(masked)
        if masked.count("\n") >= 1:
            out = out.replace("⟦4⟧", "", 1)
        return out
    monkeypatch.setattr(T, "_chat", fake_translator(flaky))
    r = T.translate(SRC, "ja-JP", NAMES)
    assert r.status == T.STATUS_TRANSLATED, r.failures
    assert _nums(r.text.split("※")[0]) == _nums(SRC)


def test_line_retry_still_rejects_a_changed_number(monkeypatch):
    # 줄 단위로도 숫자를 쓰면(값 변형) 통과시키지 않는다 -> 기존 폴백
    monkeypatch.setattr(T, "_chat", fake_translator(lambda masked: to_ja(masked).replace("⟦2⟧", "1,254,001")))
    r = T.translate(SRC, "ja-JP", NAMES)
    assert r.status == T.STATUS_FALLBACK


def test_index_written_as_digit_is_not_accepted(monkeypatch):
    # 모델이 자리표시자 번호를 숫자로 풀어 쓴 경우("⟦10⟧" -> "10")는 고치지 않고 거절한다(값이 아니라 번호다)
    monkeypatch.setattr(T, "_chat", fake_translator(lambda masked: to_ja(masked).replace("⟦3⟧", "3")))
    assert T.translate(SRC, "ja-JP", NAMES).status == T.STATUS_FALLBACK
