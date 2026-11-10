"""기록 검색어(keyword) 해석: 공백으로 나눈 낱말, 종목 별칭, 위험 키워드 → 진단 플래그 코드. 2026-11-05.

규칙(통합 검색 GET /api/history/search와 진단 이력 q가 같이 쓴다):
- 검색어를 공백으로 나눈 낱말(최대 MAX_TERMS개, 중복 제거)이 **모두** 어딘가에 들어 있어야 매칭이다(AND).
  "ETF 중복"은 "ETF"와 "중복"이 각각 들어 있으면 된다.
- 낱말 하나는 그 글자 그대로(부분 일치, 대소문자 무시) 또는 별칭의 등록명으로 찾는다('삼전' → '삼성전자'도).
- 진단 검색에서는 위험 키워드(RISK_KEYWORD_FLAGS)가 들어 있는 낱말이 그 플래그 코드가 있는 진단과도 매칭된다
  ('집중도' → CONCENTRATION_HIGH·MARKET_SKEW). 플래그 문장에는 '집중도'라는 낱말이 없어서다.
"""

from app.company_aliases import COMPANY_ALIASES
from app.tools.company_resolver import _key

MAX_TERMS = 5
MAX_KEYWORD_LEN = 50

_ALIAS_BY_KEY = {_key(alias): name for alias, name in COMPANY_ALIASES.items()}

# 낱말에 이 키가 들어 있으면 해당 플래그 코드가 있는 진단도 찾는다. "위험"·"리스크"는 플래그가 하나라도 있는 진단(ANY_FLAG).
ANY_FLAG = "*"
RISK_KEYWORD_FLAGS: dict[str, tuple[str, ...]] = {
    "집중": ("CONCENTRATION_HIGH", "MARKET_SKEW"),
    "쏠림": ("CONCENTRATION_HIGH", "MARKET_SKEW"),
    "편중": ("CONCENTRATION_HIGH", "MARKET_SKEW"),
    "분산": ("CONCENTRATION_HIGH", "FEW_HOLDINGS"),
    "종목수": ("FEW_HOLDINGS",),
    "현금": ("CASH_HIGH", "CASH_LOW"),
    "손실": ("BIG_LOSS",),
    "손익": ("BIG_LOSS",),
    "레버리지": ("LEVERAGED_INVERSE_ETF",),
    "인버스": ("LEVERAGED_INVERSE_ETF",),
    "concentration": ("CONCENTRATION_HIGH", "MARKET_SKEW"),
    "cash": ("CASH_HIGH", "CASH_LOW"),
    "loss": ("BIG_LOSS",),
    "leverage": ("LEVERAGED_INVERSE_ETF",),
    "위험": (ANY_FLAG,),
    "리스크": (ANY_FLAG,),
    "risk": (ANY_FLAG,),
}


def split_terms(keyword: str | None) -> list[str]:
    """공백으로 나눈 낱말(앞에서부터 MAX_TERMS개, 대소문자 무시 중복 제거). 비었으면 []."""
    terms, seen = [], set()
    for t in (keyword or "").split():
        if t.casefold() not in seen:
            seen.add(t.casefold())
            terms.append(t)
    return terms[:MAX_TERMS]


def term_variants(term: str) -> list[str]:
    """낱말 그대로 + 별칭이면 그 등록명."""
    alias = _ALIAS_BY_KEY.get(_key(term))
    return [term] if alias is None or alias.casefold() == term.casefold() else [term, alias]


def flag_codes(term: str) -> set[str]:
    """낱말에 들어 있는 위험 키워드의 플래그 코드. ANY_FLAG가 들어 있을 수 있다."""
    t = term.casefold()
    return {code for key, codes in RISK_KEYWORD_FLAGS.items() if key in t for code in codes}
