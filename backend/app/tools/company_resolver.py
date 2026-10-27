"""tool 입력 종목명/티커 정규화 및 보정.

LLM이 만든 인자에는 앞뒤 공백, 따옴표, 'null', 깨진 문자열 등이 섞여 들어온다.
정확 일치 -> 공백/대소문자 무시 일치 -> 별칭(app/company_aliases.py) -> [기본 모드만] 한글 음차(엘지->LG) -> 앞부분 약칭
-> 자모 한 글자 오타 -> 유사 종목명 보정(difflib) 순으로 시도하고,
그래도 못 찾으면 예외 대신 "종목을 찾지 못했습니다" 응답을 돌려준다.
주문처럼 오매칭이 곧 금전 사고인 경로는 allow_fuzzy=False(엄격 모드)로 호출한다: 4단계(유사 보정)를 건너뛰고,
유사 후보는 보정하지 않고 "혹시 A, B 중 하나인가요?" 안내로만 돌려준다.
"""

import difflib
import re
import unicodedata
from dataclasses import dataclass

from app.company_aliases import COMPANY_ALIASES
from app.etf import rewrite_etf_brand  # noqa: F401  (app.companies 등이 여기서 가져다 쓴다)
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


# 별칭 조회용. 정규화한 별칭 -> 등록명(resolve_company), 등록명 -> 별칭 목록(extract_from_text).
_ALIAS_BY_KEY = {_key(alias): name for alias, name in COMPANY_ALIASES.items()}
_ALIASES_BY_NAME: dict[str, list[str]] = {}
for _alias, _name in COMPANY_ALIASES.items():
    _ALIASES_BY_NAME.setdefault(_name, []).append(_alias)


@dataclass
class Resolution:
    company: Company | None = None
    corrected_from: str | None = None
    message: str | None = None
    candidates: list[str] | None = None


# 기본 모드의 앞부분 약칭 규칙에 쓰는 최소 길이(공백 제외 글자 수). 3자 이하('삼성전', '현대차')는 다른 종목의 앞부분일 때가 많다.
PREFIX_MIN_LEN = 4
# 자모 오타 규칙은 한글 3글자 이상에만 쓴다. 2글자 이름('동서'/'동성')은 한 자모 차이로 다른 회사가 되는 경우가 많다.
JAMO_MIN_HANGUL = 3

# 영문 약어로 시작하는 종목명의 한글 음차(앞부분만 바꾼다). 긴 것부터 시도한다('에스케이씨' 다음에 '에스케이').
TRANSLIT_PREFIXES = {
    "엔에이치엔": "NHN", "케이티앤지": "KT&G", "에스케이씨": "SKC", "케이씨씨": "KCC", "비엔케이": "BNK", "비지에프": "BGF",
    "에이치엠엠": "HMM", "에이치디씨": "HDC", "에이치엘비": "HLB", "오씨아이": "OCI", "에프앤에프": "F&F",
    "에스케이": "SK", "엘지": "LG", "케이비": "KB", "지에스": "GS", "씨제이": "CJ", "케이티": "KT", "에이치디": "HD",
    "에이치엘": "HL", "에이치케이": "HK", "에이치제이": "HJ", "엔에이치": "NH", "디비": "DB", "디엘": "DL", "디엔": "DN",
    "엘에스": "LS", "엘엑스": "LX", "제이비": "JB", "아이엠": "iM", "엔씨": "NC",
    # "포스코"는 넣지 않는다: 약어 음차가 아니라 한글 브랜드라 POSCO홀딩스/포스코퓨처엠/포스코인터내셔널에 두루 걸린다.
}
_TRANSLIT_ORDER = sorted(TRANSLIT_PREFIXES, key=len, reverse=True)


def transliterate_prefix(name: str) -> str:
    """'엘지화학' -> 'LG화학', '에스케이씨' -> 'SKC'. 음차 표로 시작하지 않으면 그대로 돌려준다."""
    for ko in _TRANSLIT_ORDER:
        if name.startswith(ko):
            return TRANSLIT_PREFIXES[ko] + name[len(ko):]
    return name


def _jamo(ch: str) -> tuple | None:
    """한글 음절 -> (초성, 중성, 종성) 번호. 한글 음절이 아니면 None."""
    code = ord(ch) - 0xAC00
    if not 0 <= code < 11172:
        return None
    return code // 588, (code % 588) // 28, code % 28


def _one_jamo_apart(a: str, b: str) -> bool:
    """글자 수가 같고 정확히 한 글자만 다르며, 그 글자가 둘 다 한글이고 초성·중성·종성 중 한 자리만 다르면 True(한글 3자 이상)."""
    if len(a) != len(b) or a == b or sum(1 for ch in a if _jamo(ch)) < JAMO_MIN_HANGUL:
        return False
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    if len(diff) != 1:
        return False
    ja, jb = _jamo(diff[0][0]), _jamo(diff[0][1])
    return ja is not None and jb is not None and sum(p != q for p, q in zip(ja, jb)) == 1


def _suggest_message(s: str, key: str, keys: dict) -> Resolution:
    """엄격 모드에서 정확히 못 찾았을 때의 응답. 유사한 종목명(최대 MAX_CANDIDATES개)을 보정 없이 안내만 한다.

    후보는 유사도(FUZZY_CUTOFF) 이상이거나 입력과 포함 관계인 이름('삼성전자우' -> 삼성전자)이고 유사도 순이다.
    """
    close = set(difflib.get_close_matches(key, list(keys), n=MAX_CANDIDATES, cutoff=FUZZY_CUTOFF))
    contained = {m for m in keys if len(key) >= 2 and len(m) >= 2 and (m in key or key in m)}
    ranked = sorted(close | contained, key=lambda m: -difflib.SequenceMatcher(None, key, m).ratio())
    names = list(dict.fromkeys(keys[m][0].name for m in ranked))[:MAX_CANDIDATES]
    if not names:
        return Resolution(message=f"정확한 종목을 찾지 못했습니다. '{s}'에 해당하는 종목명 또는 종목코드를 정확히 입력해주세요.")
    asked = ", ".join(names) + " 중 하나인가요?" if len(names) > 1 else f"{names[0]}인가요?"
    return Resolution(message=f"정확한 종목을 찾지 못했습니다. 혹시 {asked}", candidates=names)


def resolve_company(db, raw, allow_fuzzy: bool = True) -> Resolution:
    """종목명 또는 티커 입력을 실제 Company로 해석한다.

    allow_fuzzy=False(엄격 모드)면 정확한 종목코드/종목명, 공백·대소문자만 다른 이름, 등록된 별칭(ETF 브랜드 표기 포함)만
    인정한다. 그 밖에는 유사 후보가 있어도 company를 돌려주지 않고 message/candidates로 안내만 한다.
    """
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

    # 3) 별칭 (예: '네이버' -> 'NAVER'). 퍼지 매칭보다 먼저 본다: 퍼지는 못 찾는 데서 끝나지 않고 '삼성SDS' -> 삼성SDI처럼
    #    다른 종목으로 잘못 연결하기도 한다. 별칭의 등록명이 DB에 없으면 아무것도 하지 않고 다음 단계로 넘어간다.
    key = _key(s)
    branded = rewrite_etf_brand(s)  # ETF: "코덱스200" -> "KODEX200", "타이거 나스닥" -> "TIGER 나스닥"
    for k in dict.fromkeys((key, _key(branded))):
        alias_name = _ALIAS_BY_KEY.get(k)
        if alias_name is not None:
            for c in companies:
                if c.name == alias_name:
                    return Resolution(company=c, corrected_from=str(raw))

    # 3b) ETF 브랜드의 한글 표기/이전 이름을 영문으로 바꾸면 공백/대소문자 일치가 되는 경우 ("코덱스 200" -> 'KODEX 200')
    if branded != s:
        by_brand = [c for c in companies if _key(c.name) == _key(branded)]
        if len(by_brand) == 1:
            return Resolution(company=by_brand[0], corrected_from=str(raw))

    keys = {}
    for c in companies:
        keys.setdefault(_key(c.name), []).append(c)
    if not allow_fuzzy:
        return _suggest_message(s, key, keys)

    # --- 여기부터는 기본 모드(allow_fuzzy=True)에서만. 주문의 엄격 모드는 위에서 이미 후보 안내로 끝났다. ---

    # 3c) 영문 약어의 한글 음차를 영문으로 ('엘지화학' -> 'LG화학', '케이비금융' -> 'KB금융'). 바꾼 이름이 정확히(공백·대소문자
    #     무시) 등록명이거나 별칭일 때만 채택한다.
    translit = transliterate_prefix(s)
    t_key = _key(translit)
    if translit != s:
        if t_key in keys and len(keys[t_key]) == 1:
            return Resolution(company=keys[t_key][0], corrected_from=str(raw))
        alias_name = _ALIAS_BY_KEY.get(t_key)
        if alias_name is not None:
            for c in companies:
                if c.name == alias_name:
                    return Resolution(company=c, corrected_from=str(raw))
    variants = list(dict.fromkeys((key, t_key)))

    # 3d) 앞부분 약칭 ('JB금융' -> 'JB금융지주'): 입력(4자 이상)으로 시작하는 종목이 정확히 하나면 채택, 둘 이상이면 후보 안내.
    prefixed = {m for v in variants if len(v) >= PREFIX_MIN_LEN for m in keys if m.startswith(v) and m != v}
    if len(prefixed) == 1:
        return Resolution(company=keys[prefixed.pop()][0], corrected_from=str(raw))
    if len(prefixed) > 1:
        names = sorted(keys[m][0].name for m in prefixed)
        return Resolution(
            message=f"'{s}'(으)로 시작하는 종목이 여러 개라 특정할 수 없습니다. 정확한 종목명을 지정해주세요.",
            candidates=names[:MAX_CANDIDATES],
        )

    # 3e) 자모 한 글자 오타 ('셀트리언' -> '셀트리온'): 한글 3글자 이상, 글자 수가 같고, 한 글자의 초성·중성·종성 중 하나만 다른
    #     종목이 정확히 하나면 채택한다(그런 종목이 둘 이상이면 1등과 2등의 차이가 없으니 후보 안내).
    one_off = {m for v in variants for m in keys if _one_jamo_apart(v, m)}
    if len(one_off) == 1:
        return Resolution(company=keys[one_off.pop()][0], corrected_from=str(raw))
    if len(one_off) > 1:
        return Resolution(
            message=f"'{s}'와(과) 비슷한 종목이 여러 개라 특정할 수 없습니다. 정확한 종목명을 지정해주세요.",
            candidates=sorted(keys[m][0].name for m in one_off)[:MAX_CANDIDATES],
        )

    # 4) 유사 종목명 보정
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
    - 별칭('네이버' -> NAVER)도 그 회사의 이름처럼 찾는다.
    - ETF는 브랜드 한글 표기('코덱스 200' -> 'KODEX 200')를 영문으로 바꿔서 찾고, 이름의 공백이 빠진 입력('KODEX200')도 인정한다.
    여러 회사가 언급되면 전부 반환하므로, 어느 것을 쓸지는 호출측이 정한다."""
    hay = rewrite_etf_brand(unicodedata.normalize("NFC", text or "")).casefold()
    if not hay:
        return []

    hits = []  # (start, end, company)
    for c in db.query(Company).all():
        aliases = ((a, True) for a in _ALIASES_BY_NAME.get(c.name, ()))
        # ETF 이름은 "KODEX 200"처럼 공백이 있어서 "KODEX200"으로 쓰는 사람이 많다. ETF만 공백 없는 표기도 이름으로 본다.
        spaceless = ((c.name.replace(" ", ""), True),) if getattr(c, "asset_type", None) == "etf" and " " in c.name else ()
        for token, is_name in ((c.name, True), (c.ticker, False), *aliases, *spaceless):
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
