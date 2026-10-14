"""한국어 조사(은/는, 이/가, 을/를, 과/와, 으로/로)를 앞 단어의 받침에 맞춰 붙인다.

종목명은 "KODEX 200", "TIGER 미국S&P500", "LG", "KODEX CD금리액티브(합성)"처럼 숫자·영문·괄호로 끝나는 경우가 많아서
마지막 글자가 한글일 때만 받침을 보는 방식으로는 "KODEX 200는"처럼 틀린다. 읽는 소리 기준으로 받침을 판단한다:
- 한글: 그 글자의 받침
- 숫자: 읽는 소리의 마지막 글자. 끝자리가 0이 아니면 그 숫자(일/이/삼/사/오/육/칠/팔/구, 받침은 1·3·6·7·8),
  0으로 끝나면 십/백/천/만/억 같은 자리 이름으로 끝나서 받침이 있다(단독 "0"은 '영'이라 받침 있음).
- 영문: 낱자 이름(엘, 엠, 엔, 알은 받침 있음). 다만 영단어처럼 모음 뒤에 r로 끝나면("NAVER", "TIGER") '-어'로 읽혀 받침 없음.
- 끝의 괄호·기호는 건너뛰고 그 앞 글자를 본다("(합성)" -> '성').
"""

_DIGIT_HAS_FINAL = {"0": True, "1": True, "2": False, "3": True, "4": False, "5": False, "6": True, "7": True, "8": True, "9": False}
_LETTER_HAS_FINAL = set("LMNR")
_PAIRS = {"은/는", "이/가", "을/를", "과/와", "으로/로"}


def _final_info(word: str) -> tuple[bool, bool]:
    """(받침이 있는지, 그 받침이 ㄹ인지). 판단할 글자가 없으면 (False, False)."""
    s = (word or "").rstrip()
    i = len(s) - 1
    while i >= 0 and not (s[i].isalnum()):
        i -= 1
    if i < 0:
        return False, False
    ch = s[i]
    if "가" <= ch <= "힣":
        jong = (ord(ch) - ord("가")) % 28
        return jong != 0, jong == 8  # 8 = ㄹ
    if ch.isdigit():
        j = i
        while j >= 0 and s[j].isdigit():
            j -= 1
        digits = s[j + 1 : i + 1]
        if ch == "0" and len(digits.lstrip("0")) > 0 and len(digits) > 1:
            return True, False  # 십/백/천/만/억
        return _DIGIT_HAS_FINAL[ch], ch in "178"  # 일, 칠, 팔은 ㄹ
    if ch.isascii() and ch.isalpha():
        up = ch.upper()
        if up == "R" and i >= 1 and s[i - 1].upper() in "AEIOU" and i >= 2 and s[i - 2].isalpha():
            return False, False  # NAVER, TIGER: '-버', '-거'
        return up in _LETTER_HAS_FINAL, up in "LR"  # 엘, 알은 ㄹ
    return False, False


def josa(word: str, pair: str) -> str:
    """word 뒤에 맞는 조사를 붙인 문자열. pair는 "은/는", "이/가", "을/를", "과/와", "으로/로" 중 하나."""
    if pair not in _PAIRS:
        raise ValueError(f"지원하지 않는 조사 {pair!r}")
    has_final, is_rieul = _final_info(word)
    with_final, without_final = pair.split("/")
    if pair == "으로/로":
        return word + ("으로" if has_final and not is_rieul else "로")
    return word + (with_final if has_final else without_final)
