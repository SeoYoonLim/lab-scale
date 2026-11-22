"""기사 범위 판정: 기업 직접 / 업종·산업 / 시장 (2026-11-23).

브라우저 테스트: SK하이닉스 하락 원인 답에 "미국 AI 관련주 하락", "반도체 업종 전반의 조정", "코스피 하락"이 SK하이닉스 자체의 원인처럼
섞였다. 원천 기사는 SK하이닉스에 연결돼 있고 제목에 이름도 있지만 실제로는 시황 기사("코스피, 美 반도체주 급락에 5000선 붕괴…삼전닉스
동반 약세"), 여러 기업을 함께 다룬 업종 기사("삼성전자·SK하이닉스 현금 329조"), ETF 편입 비중 기사였다. 제목에 기업명이 있다는 것만으로는
그 기업의 직접 원인 근거가 아니다.

판정(제목 + 본문 요약, 코드 규칙만. LLM을 쓰지 않는다):
- company(기업 직접): 제목이 이 기업 하나를 주어로 다루고(다른 등록 기업과 나란히 나열하지 않음), 시황·업종 낱말이 있으면 이 기업이 제목
  맨 앞 주어일 때만. 본문 요약이 있으면 본문에도 이 기업이 나와야 한다
- market(시장): 지수·증시·시황 기사(코스피·뉴욕증시·개장·외국인 순매도 …)
- sector(업종·산업): 여러 기업을 함께 다룬 기사(삼성전자·SK하이닉스, 삼전닉스, 투톱), 업종·산업·ETF 기사
- unconfirmed(관련성 미확인): 제목에 이 기업이 없거나, 본문 요약에 이 기업이 나오지 않는 기사
공시는 그 기업이 낸 자료라 언제나 company다(이 모듈을 거치지 않는다). 판정이 애매하면 company가 아닌 쪽으로 둔다(원인 단정 방지).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

COMPANY = "company"
SECTOR = "sector"
MARKET = "market"
UNCONFIRMED = "unconfirmed"
REFERENCE = (SECTOR, MARKET, UNCONFIRMED)

LABEL_KO = {SECTOR: "업종·산업", MARKET: "시장", UNCONFIRMED: "관련성 미확인"}

_MARKET_RE = re.compile(
    r"코스피|코스닥|증시|뉴욕\s*마켓|나스닥|다우|S&P|필라델피아|지수|개장|출발|마감\s*시황|시황|장\s*초반|장초반|장중|프리마켓|애프터마켓"
    r"|외국인|기관|순매도|순매수|환율|금리|국채|달러|유가|KOSPI|KOSDAQ|NASDAQ",
    re.IGNORECASE,
)
_SECTOR_RE = re.compile(
    r"반도체\s*(?:주|株|업종|업황|업계|섹터|기업|산업|대형주|투톱)|업종|업황|업계|섹터|관련주|테마|산업|ETF|상장지수펀드|AI\s*주|AI株|빅테크"
    r"|대형주|시가총액\s*상위|시총\s*상위|\d+\s*대\s*기업|메모리\s*(?:3사|업체|기업|시장|업황)|K-?\s*메모리|K-?\s*반도체",
    re.IGNORECASE,
)
# 두 기업 이상을 한 낱말로 묶은 말("삼전닉스", "반도체 투톱", "양사")
_MULTI_RE = re.compile(r"삼전닉스|삼전·닉스|삼전·하닉|투톱|양사|두\s*기업|\d\s*사(?![가-힣])|빅\s*\d")
# 이 기업이 주어여도 다른 종목을 소개하는 기사("SK하이닉스 뛰면 날아오를 '이 종목'", "SK하이닉스 수혜주")
_OTHER_SUBJECT_RE = re.compile(r"['\"‘’“”]\s*이\s*종목\s*['\"‘’“”]|이\s*종목들?(?:은|이|에|을|도)?\s*(?:주목|뜬다|뜰까|수혜)|수혜주|관련주|테마주|협력사\s*주가")
_LEAD_STRIP_RE = re.compile(r"^(?:\s*(?:\[[^\]]{0,20}\]|【[^】]{0,20}】|\([^)]{0,20}\)|<[^>]{0,20}>|[\"'“”‘’]))+\s*")
_LIST_SEP = "·・/、,"
_MIN_CONTENT = 20


def _tokens(name: str, aliases: Iterable[str]) -> list[str]:
    return sorted({t for t in (name, *aliases) if t and len(t) >= 2}, key=len, reverse=True)


def _find(text: str, token: str) -> list[tuple[int, int]]:
    t, h = token.casefold(), text.casefold()
    out = []
    for m in re.finditer(re.escape(t), h):
        st, en = m.span()
        if t[0].isascii() and t[0].isalnum() and st > 0 and h[st - 1].isascii() and h[st - 1].isalnum():
            continue
        if t[-1].isascii() and t[-1].isalnum() and en < len(h) and h[en].isascii() and h[en].isalnum():
            continue
        out.append((st, en))
    return out


def _spans(text: str, tokens: list[str]) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    for tok in tokens:  # 긴 표기부터(겹치면 긴 쪽)
        for st, en in _find(text, tok):
            if all(en <= a or st >= b for a, b in hits):
                hits.append((st, en))
    return sorted(hits)


def _other_spans(text: str, other_names: Iterable[str]) -> list[tuple[int, int]]:
    out = []
    for name in other_names:
        if name and len(name) >= 2:
            out += _find(text, name)
    return out


def _own_and_others(text: str, tokens: list[str], other_names: Iterable[str]) -> tuple[list[tuple[int, int]], bool]:
    """(이 기업 언급 위치, 다른 등록 기업 언급이 있는가). 더 긴 다른 기업 이름 안의 표기('셀트리온제약' 안의 '셀트리온')는 이 기업이 아니고,
    이 기업 표기 안의 더 짧은 다른 이름('SK하이닉스' 안의 'SK')은 다른 기업이 아니다."""
    own = _spans(text, tokens)
    others = _other_spans(text, other_names)
    own = [(a, b) for a, b in own if not any(c <= a and b <= d and d - c > b - a for c, d in others)]
    other_hit = any(all(d <= a or c >= b for a, b in own) for c, d in others)
    return own, other_hit


def _listed(text: str, spans: list[tuple[int, int]]) -> bool:
    """이 기업 이름이 다른 이름과 나열 부호(·, /, 、, 앞의 쉼표)로 붙어 있는가('애플·삼성전자', '삼성전자·SK하이닉스')."""
    for st, en in spans:
        before = text[:st].rstrip()[-1:]
        after = text[en:].lstrip()[:1]
        # 이름 뒤 쉼표는 제목 주어 표기("SK하이닉스, HBM4 양산")라 나열로 보지 않는다
        if (before and before in _LIST_SEP) or (after and after in _LIST_SEP.replace(",", "")):
            return True
    return False


def _leading(text: str, spans: list[tuple[int, int]]) -> bool:
    """이 기업이 제목 맨 앞 주어인가(머리말 '[속보]'·따옴표는 건너뛴다)."""
    lead = len(_LEAD_STRIP_RE.match(text).group(0)) if _LEAD_STRIP_RE.match(text) else 0
    return any(st == lead for st, _ in spans)


def classify(name: str, title: str | None, content: str | None = None, aliases: Iterable[str] = (),
             other_names: Iterable[str] = ()) -> str:
    """기사 하나의 범위(COMPANY/SECTOR/MARKET/UNCONFIRMED). name은 등록명, aliases는 그 기업의 별칭, other_names는 다른 등록 기업 이름들."""
    title = (title or "").strip()
    tokens = _tokens(name, aliases)
    spans, others = _own_and_others(title, tokens, other_names)
    market = bool(_MARKET_RE.search(title))
    if not spans:
        if market:
            return MARKET
        return SECTOR if (_MULTI_RE.search(title) or _SECTOR_RE.search(title)) else UNCONFIRMED
    if _MULTI_RE.search(title) or _listed(title, spans) or others:
        return MARKET if market else SECTOR
    if _OTHER_SUBJECT_RE.search(title):
        return SECTOR
    lead = _leading(title, spans)
    if market and not lead:
        return MARKET
    if _SECTOR_RE.search(title) and not lead:
        return SECTOR
    body = (content or "").strip()
    if len(body) >= _MIN_CONTENT and not _spans(body, tokens):
        return UNCONFIRMED
    return COMPANY


def is_reference(scope: str | None) -> bool:
    return scope in REFERENCE


def annotate(items: list[dict], name: str, aliases: Iterable[str] = (), other_names: Iterable[str] = (),
             content_key: str = "content") -> list[dict]:
    """기사 dict마다 "scope"를 채운다(있으면 그대로). 같은 목록을 돌려준다."""
    others = [n for n in other_names if n != name]
    for it in items:
        if isinstance(it, dict) and "scope" not in it:
            it["scope"] = classify(name, it.get("title"), it.get(content_key), aliases, others)
    return items


def company_context(company_name: str) -> tuple[list[str], list[str]]:
    """(이 기업 별칭, 다른 등록 기업 이름 + 그 별칭). DB를 못 읽으면 등록명 목록 없이 별칭만이다."""
    from app.answer_i18n import _company_names
    from app.company_aliases import COMPANY_ALIASES
    from app.tools.company_resolver import aliases_of

    aliases = [a for a in aliases_of(company_name) if re.search(r"[가-힣A-Za-z]", a)]
    others = [n for n in _company_names() if n != company_name]
    others += [alias for alias, target in COMPANY_ALIASES.items() if target != company_name]
    return aliases, others
