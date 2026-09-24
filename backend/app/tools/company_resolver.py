"""tool 입력 종목명/티커 정규화 및 보정.

LLM이 만든 인자에는 앞뒤 공백, 따옴표, 'null', 깨진 문자열 등이 섞여 들어온다.
정확 일치 -> 공백/대소문자 무시 일치 -> 유사 종목명 보정(difflib) 순으로 시도하고,
그래도 못 찾으면 예외 대신 "종목을 찾지 못했습니다" 응답을 돌려준다.
"""

import difflib
import re
import unicodedata
from dataclasses import dataclass

from app.models import Company

# 유사 종목명 보정 임계값. 종목명 오타를 사용 가능한 수준으로 복구하면서, DB에 없는 실제 상장사가
# 비슷한 다른 종목으로 잘못 매칭되는 비율을 낮추도록 실험으로 정했다 (0.75: 오타 복구 75%/오매칭 2.2%,
# 0.8: 54%/0.6%, 0.85: 28%/0.2%). 엉뚱한 종목으로 답하는 것이 못 찾는 것보다 나쁘므로 0.8을 쓴다.
FUZZY_CUTOFF = 0.8
MAX_CANDIDATES = 3

_WS_RE = re.compile(r"\s+")
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)
_EMPTY_TOKENS = {"", "null", "none", "undefined", "n/a", "nan"}
_QUOTES = "\"'`“”‘’"


def normalize_name(raw) -> str:
    """앞뒤/중복 공백, 제로폭 문자, 감싼 따옴표를 제거한다. 'null' 같은 빈 값은 ''로."""
    if raw is None:
        return ""
    s = unicodedata.normalize("NFC", str(raw)).translate(_ZERO_WIDTH)
    s = _WS_RE.sub(" ", s).strip().strip(_QUOTES).strip()
    return "" if s.casefold() in _EMPTY_TOKENS else s


def _key(s: str) -> str:
    return s.replace(" ", "").casefold()


@dataclass
class Resolution:
    company: Company | None = None
    corrected_from: str | None = None
    message: str | None = None
    candidates: list[str] | None = None


def resolve_company(db, raw) -> Resolution:
    """종목명 또는 티커 입력을 실제 Company로 해석한다."""
    s = normalize_name(raw)
    if not s:
        return Resolution(message="종목명이 비어 있어 종목을 찾지 못했습니다. 종목명 또는 종목코드를 지정해주세요.")

    companies = db.query(Company).all()

    # 1) 정확 일치 (종목명 또는 티커)
    for c in companies:
        if c.name == s or c.ticker == s or c.ticker == s.upper():
            return Resolution(company=c)

    # 2) 공백/대소문자만 다른 경우 (예: '삼성 전자', 'sk하이닉스')
    loose = [c for c in companies if _key(c.name) == _key(s)]
    if len(loose) == 1:
        return Resolution(company=loose[0], corrected_from=str(raw))

    # 3) 유사 종목명 보정
    key = _key(s)
    keys = {}
    for c in companies:
        keys.setdefault(_key(c.name), []).append(c)
    matches = difflib.get_close_matches(key, list(keys), n=MAX_CANDIDATES, cutoff=FUZZY_CUTOFF)
    # 입력이 종목명을 포함하거나 종목명이 입력을 포함하면 오타가 아니라 다른 종목일 가능성이 높다
    # (예: '삼성전자우'(우선주) -> '삼성전자', '현대' -> '현대차'). 잘못된 종목으로 답하는 것보다
    # 못 찾았다고 하는 편이 안전하므로 보정하지 않는다.
    matches = [m for m in matches if m not in key and key not in m]
    if matches:
        names = [keys[m][0].name for m in matches]
        # 후보가 여럿이고 서로 비슷하게 가까우면 (예: '현대' -> 현대차/현대모비스...) 임의로 고르지 않는다.
        if len(matches) > 1:
            r0, r1 = (difflib.SequenceMatcher(None, key, m).ratio() for m in matches[:2])
            if r0 - r1 < 0.08:
                return Resolution(
                    message=f"'{s}'와(과) 비슷한 종목이 여러 개라 특정할 수 없습니다. 정확한 종목명을 지정해주세요.",
                    candidates=names,
                )
        return Resolution(company=keys[matches[0]][0], corrected_from=str(raw))

    return Resolution(message=f"'{s}' 종목을 찾지 못했습니다. company 테이블에 등록된 종목명 또는 종목코드를 지정해주세요.")


# 종목명 바로 뒤에 붙은 우선주 표기('삼성전자우', '현대차2우B', '삼성전자우선주'). 우선주는 보통주와 다른
# 종목이라 보통주로 취급하면 안 된다. '우' 뒤에 한글이 이어지면('우리', '우수') 우선주가 아니다.
_PREFERRED_RE = re.compile(r"^(?:\d?우b?(?![가-힣])|우선주)")
_ASCII_ALNUM = re.compile(r"[0-9a-z]")


def extract_from_text(db, text: str) -> list[Company]:
    """질문 원문에 포함된 회사명/티커를 DB의 전체 종목과 대조해 찾는다. 등장 순서대로 반환한다.

    - 겹치는 후보는 더 긴 이름이 이긴다 ('카카오뱅크' 질문에서 '카카오'는 제외).
    - 영문/숫자 이름·티커는 앞뒤가 영문/숫자가 아닐 때만 인정한다 ('DL' -> 'DLNA' 제외).
    - 이름 바로 뒤에 우선주 표기가 붙으면 보통주로 취급하지 않는다 ('삼성전자우' -> 제외).
    여러 회사가 언급되면 전부 반환하므로, 어느 것을 쓸지는 호출측이 정한다."""
    hay = unicodedata.normalize("NFC", text or "").casefold()
    if not hay:
        return []

    hits = []  # (start, end, company)
    for c in db.query(Company).all():
        for token, is_name in ((c.name, True), (c.ticker, False)):
            t = token.casefold()
            for m in re.finditer(re.escape(t), hay):
                st, en = m.span()
                if _ASCII_ALNUM.match(t[0]) and st > 0 and _ASCII_ALNUM.match(hay[st - 1]):
                    continue
                if _ASCII_ALNUM.match(t[-1]) and en < len(hay) and _ASCII_ALNUM.match(hay[en]):
                    continue
                # 우선주로 거절한 구간도 남겨서, 그 안의 더 짧은 이름('LG전자우' -> 'LG')이 대신 잡히지 않게 한다.
                if is_name and _PREFERRED_RE.match(hay[en:]):
                    hits.append((st, en, None))
                    continue
                hits.append((st, en, c))

    accepted = []
    for st, en, c in sorted(hits, key=lambda h: (-(h[1] - h[0]), h[0])):
        if all(en <= a_st or st >= a_en for a_st, a_en, _ in accepted):
            accepted.append((st, en, c))

    out, seen = [], set()
    for _, _, c in sorted(accepted, key=lambda h: h[0]):
        if c is not None and c.id not in seen:
            seen.add(c.id)
            out.append(c)
    return out


def to_int(value, default: int, lo: int = 1, hi: int = 50) -> int:
    """LLM이 '5'처럼 문자열이나 null로 넘기는 정수 인자를 안전하게 변환한다."""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def not_found_response(base: dict, res: Resolution) -> dict:
    out = {**base, "found": False, "message": res.message}
    if res.candidates:
        out["candidates"] = res.candidates
    return out
