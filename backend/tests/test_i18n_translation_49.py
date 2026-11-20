"""49차 문제 3: 48차 57문항 검사의 번역 실패 6건(데이터 요약으로 대체) 개선.

같은 조건(같은 한국어 원문·번역 코드·모델, 온도 0)으로 3번씩 다시 번역한 결과(2026-11-13): 5건은 3번 모두 같은 이유로 실패, d05는 재현되지 않음.
- t05(zh)·N3(en)·T1(ja): 코드가 만든 시세 요약 문장("…의 D 종가는 P원으로, D0 종가 P0원 대비 N거래일간 R%입니다." 등)을 LLM이 옮기다
  "⟦10⟧거래일간"의 자리표시자를 빠뜨리거나(t05) 순서를 바꾸거나(N3), 비교 머리말을 한국어 그대로 돌려줬다(T1).
- N2·N4(zh): 줄 단위 재시도에서 글머리표 줄("*   업계는 …")을 한국어 그대로 돌려줬다. N2에는 인물 이름(장덕현 사장)도 있었다.
숫자 변형은 6건 모두 없었다(검증이 막았다). 검증 기준은 그대로 두고, 코드가 확실히 옮길 수 있는 부분을 LLM에서 뺀다.
"""

import re

import pytest

from app import i18n_translate as T

SUMMARY = ("NAVER의 2026-11-12 종가는 181,800원으로, 2026-10-15 종가 205,000원 대비 20거래일간 -11.32%입니다. "
           "이 기간 종가 최고는 210,500원(2026-10-20), 최저는 178,300원(2026-11-06)입니다. "
           "마지막 거래일(2026-11-12) 하루 등락률은 -6.43%, 거래량은 1,655,748주입니다. 최고·최저는 종가 기준이며 장중 고가·저가가 아닙니다.")
COMPARE = ("2026-10-12 종가 대비 2026-11-12까지 23거래일 기준 비교입니다(종가 기준).\n\n"
           "같은 기간 등락률은 삼성SDI가 LG에너지솔루션보다 1.52%p 높았습니다.")


def _nums(s):
    return sorted(x.replace(",", "") for x in re.findall(r"\d[\d,]*(?:\.\d+)?", s))


def no_llm(messages):
    raise AssertionError("코드가 옮기는 문장인데 LLM을 불렀다: " + messages[-1]["content"][:200])


@pytest.mark.parametrize("locale, words", [("en-US", ["closed at", "trading days", "shares"]),
                                           ("ja-JP", ["終値", "営業日", "株"]), ("zh-CN", ["收盘价", "个交易日", "股"])])
def test_code_summary_sentences_are_translated_by_code(monkeypatch, locale, words):
    monkeypatch.setattr(T, "_chat", no_llm)
    r = T.translate(SUMMARY, locale, ["NAVER"])
    body = r.text.split("※")[0]
    assert r.status == T.STATUS_TRANSLATED and not T._HANGUL_RE.search(body)
    assert all(w in body for w in words), body
    assert _nums(body) == _nums(SUMMARY)  # 모든 숫자·날짜가 그대로


@pytest.mark.parametrize("locale", ["en-US", "ja-JP", "zh-CN"])
def test_comparison_head_and_conclusion_are_translated_by_code(monkeypatch, locale):
    monkeypatch.setattr(T, "_chat", no_llm)
    r = T.translate(COMPARE, locale, ["삼성SDI", "LG에너지솔루션"])
    body = r.text.split("※")[0]
    assert r.status == T.STATUS_TRANSLATED and _nums(body) == _nums(COMPARE)
    assert "삼성SDI" in body and "LG에너지솔루션" in body
    assert not T._HANGUL_RE.search(body.replace("삼성SDI", "").replace("LG에너지솔루션", ""))


def test_unknown_sentence_still_goes_to_the_llm_with_the_same_checks(monkeypatch):
    seen = []
    monkeypatch.setattr(T, "_chat", lambda m: seen.append(m) or re.sub(r"[가-힣]+", "words", m[1]["content"]))
    r = T.translate("NAVER는 검색 광고 사업을 하는 회사입니다.", "en-US", ["NAVER"])
    assert seen and r.status == T.STATUS_TRANSLATED


@pytest.mark.parametrize("src, locale, value", [
    ("최근 20거래일간 흐름입니다.", "en-US", "20 trading days"), ("최근 20거래일간 흐름입니다.", "ja-JP", "20営業日"),
    ("최근 20거래일간 흐름입니다.", "zh-CN", "20个交易日"), ("거래량은 4,238,276주였습니다.", "zh-CN", "4,238,276股"),
    ("거래량은 4,238,276주였습니다.", "en-US", "4,238,276 shares"),
])
def test_number_with_unit_is_one_protected_value(src, locale, value):
    m = T.mask(src, locale, [])
    assert value in m.values  # 숫자와 단위가 한 자리표시자다(모델이 단위 앞 숫자를 빠뜨리지 못한다)


def test_week_count_is_not_shares():
    m = T.mask("최근 2주 동안 흐름입니다.", "zh-CN", [])
    assert not any(v.endswith("股") for v in m.values)


def test_person_name_before_a_title_is_protected():
    m = T.mask("장덕현 사장은 AI 수요가 늘고 있다고 밝혔다.", "zh-CN", [])
    assert "장덕현" in m.values


def test_line_retry_sends_content_without_the_bullet_and_reattaches_it(monkeypatch):
    # 문단 전체로는 실패하고(자리표시자 누락), 줄 단위에서는 글머리표가 있으면 한국어를 그대로 돌려주는 모델(48차 N2 재현)
    def chat(messages):
        content = messages[1]["content"]
        if "\n" in content:
            return re.sub(r"⟦\d+⟧", "", content, count=1)
        if content.lstrip().startswith(("*", "-")):
            return content  # 한국어 그대로(실패)
        return re.sub(r"[가-힣]+", "内容", content)
    monkeypatch.setattr(T, "_chat", chat)
    src = "*   업계는 부품 수요 호조가 장기화할 것으로 봅니다.\n*   최근 3개월간 계약 규모가 1조원을 넘었습니다."
    r = T.translate(src, "zh-CN", [])
    assert r.status == T.STATUS_TRANSLATED, r.failures
    lines = r.text.split("※")[0].strip().split("\n")
    assert all(line.startswith("*   ") for line in lines) and _nums(r.text.split("※")[0]) == _nums(src)


def test_line_prompt_says_it_is_one_line():
    sys_prompt = T._prompt("⟦0⟧의 주가는 올랐습니다.", "zh-CN", one_line=True)[0]["content"]
    assert "one line" in sys_prompt.lower()


def test_checks_are_not_relaxed(monkeypatch):
    # 숫자를 쓰거나 자리표시자를 빠뜨린 번역은 여전히 거절한다
    monkeypatch.setattr(T, "_chat", lambda m: re.sub(r"[가-힣]+", "words", m[1]["content"]).replace("⟦0⟧", "999", 1))
    assert T.translate("이 회사는 ⟦⟧ 1,000원짜리 상품을 팝니다.".replace("⟦⟧", ""), "en-US", []).status == T.STATUS_FALLBACK
