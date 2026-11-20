"""answer_tidy.drop_repeats의 목록 줄 처리(2026-11-20 최종 브라우저 검증).

"그럼 최근 공시는?" 답에서 같은 제목·같은 날짜의 서로 다른 공시 3건(접수번호 001281·001302·001338)을 번호 목록으로 옮겼는데,
"4."가 한 문장으로 잘리고 본문은 반복으로 지워져 화면에 "4.", "5."만 남았다.
"""

from app import answer_tidy

TITLE = "동일인등출자계열회사와의상품ㆍ용역거래변경 - 2026-11-13"


def test_numbered_items_with_the_same_title_are_all_kept():
    t = ("삼성전자 최근 10건의 공시입니다.\n\n"
         "1. 분기보고서 (2026.09) - 2026-11-16\n"
         "2. 기업설명회(IR)개최(안내공시) - 2026-11-13\n"
         f"3. {TITLE}\n4. {TITLE}\n5. {TITLE}\n"
         "6. 최대주주등소유주식변동신고서 - 2026-11-13")
    out, n = answer_tidy.tidy(t)
    assert n == 0 and out == t
    assert out.count(TITLE) == 3


def test_no_bare_list_marker_is_left_behind():
    para = "SK하이닉스는 최근 하이브리드 DRAM 개발에 성공했다고 발표했습니다."
    t = f"## 요약\n\n{para}\n\n## 공시\n\n1. {para}\n2. 분기보고서 (2026.09) - 2026-11-16\n- {para}\n- 남아야 하는 다른 항목입니다"
    out, n = answer_tidy.tidy(t)
    assert n == 2 and out.count(para) == 1
    lines = out.split("\n")
    assert "1." not in lines and "-" not in lines and "- " not in lines
    assert "2. 분기보고서 (2026.09) - 2026-11-16" in lines and "- 남아야 하는 다른 항목입니다" in lines


def test_consecutive_identical_numbered_line_is_still_a_generation_repeat():
    t = f"1. {TITLE}\n1. {TITLE}\n2. 분기보고서 (2026.09) - 2026-11-16"
    out, n = answer_tidy.tidy(t)
    assert n == 1 and out.count(TITLE) == 1


def test_sentence_inside_a_list_item_after_the_marker_is_checked():
    """목록 표시 뒤의 여러 문장도 문장 단위로 본다(첫 문장만 반복이면 그 문장만 지운다)."""
    para = "삼성전자는 2026-11-17 종가 190,300원으로 마감했습니다."
    t = f"{para}\n\n- {para} 거래량은 16,773,338주였습니다."
    out, n = answer_tidy.tidy(t)
    assert n == 1 and out.count(para) == 1 and "- 거래량은 16,773,338주였습니다." in out
