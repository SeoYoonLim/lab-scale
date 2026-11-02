"""저장된 뉴스가 그 종목 기사인지 판정한다(순수 함수, DB 없음).

배경(2026-11-01 측정, 최근 90일 12,804건): 네이버 검색은 본문까지 찾아 주므로, 이름이 짧거나 다른 말의 일부인 종목에 엉뚱한 기사가
저장돼 있었다. 제목·요약에 이름이 없는 기사가 12.8%였고 종목 350개 중 30개는 절반이 넘었다.
"태성"의 배우 이태성·태성무역, NAVER의 기자 이메일 @naver.com, DL의 혈당 단위 mg/dL, SK의 SK하이닉스 기사 같은 경우다.

is_relevant_news가 False면 그 기사는 채팅·RAG·브리핑·사건 반응 근거에서 빠진다(행은 지우지 않는다: news.is_relevant).
정상 기사를 숨기지 않으려고 "이름이 독립된 단어로 있는가"만 보며, 예외 목록은 측정에서 사람이 읽어 확인한 표기만 넣는다.
이름은 있지만 본문이 그 회사 얘기가 아닌 기사(증권사 연구원 인용, 카카오톡 일반 언급)는 잡지 않는다.
"""

import re

from app.company_aliases import COMPANY_ALIASES
from app.etf import mentions_etf_name

_ALNUM_RE = re.compile(r"[A-Za-z0-9가-힣&@]")
_HANGUL_RE = re.compile(r"[가-힣]")
_ASCII_ALNUM_RE = re.compile(r"[A-Za-z0-9&]")
_DOMAIN_TAIL_RE = re.compile(r"\.(?:com|co\.kr|net|kr)\b", re.IGNORECASE)
# 짧은 이름 뒤에 이 말이 붙으면 같은 이름으로 본다(그 밖의 글자가 붙으면 "태성무역"처럼 다른 이름이다)
_FOLLOW_RE = re.compile(
    r"(?:은|는|이|가|의|을|를|도|와|과|에|로|으로|만|부터|까지|에서|보다|처럼|등|측|주가|주식|株|그룹|계열|이며|이다|에게|라는|이라|랑|하고|및|외|"
    r"우선주|실적|매출|영업|공시|대표|회장|사장|노조|목표|시총|시가총액|종목|지분|주주|자사주|배당|투자|상승|하락|급등|급락|강세|약세|신고가|신저가|"
    r"회사|본사|임직원|직원|사옥|공장|사업|부문|계약|수주|인수|합병|리포트|분기|올해|작년|지난|최근|전일|장중|장초반|거래)"
)
# 금융 문맥어: 모호한 이름은 이 말 또는 업종어나 종목코드가 함께 있어야 그 회사 기사로 본다
_FINANCE_RE = re.compile(
    r"주가|주식|종목|실적|공시|증권|코스피|코스닥|상장|시총|시가총액|매출|영업이익|순이익|목표주가|투자의견|배당|자사주|수주|거래량|등락|장중|"
    r"상승세|하락세|급등|급락|신고가|증시|펀드|주주"
)
# 영문 짧은 이름(SK, LG)은 그룹 기사도 그 회사 기사로 본다. 한글 이름에는 쓰지 않는다("그룹 엑소 수호 ... 태성 역")
_GROUP_CONTEXT_RE = re.compile(r"그룹|지주|회장|계열|대표이사")

# 뒤에 글자가 붙으면 다른 이름으로 보는 이름(2자 이하는 자동으로 포함)
# NAVER: "네이버페이 증권에 따르면"은 다른 종목 시세 기사의 상투 문구다
_STRICT_NAMES = frozenset({"스피어", "우리기술", "에스엠", "NAVER"})
# 일반 단어·사람 이름과 겹쳐서 독립된 단어로 나와도 문맥이 필요한 이름(영문 2자 이하는 자동으로 포함)
_CONTEXT_NAMES = frozenset({"태성", "동서", "스피어"})
# 이름마다 그 회사 기사로 볼 업종어(측정에서 확인한 그 회사 기사의 말)
_SECTOR_WORDS = {
    "태성": re.compile(r"PCB|기판|복합동박|글라스|습식"),
    "동서": re.compile(r"동서식품|맥심|커피"),
    "스피어": re.compile(r"특수합금|우주항공|스페이스X"),
}
# 별칭(company_aliases)에 없는 뉴스 표기
_EXTRA_FORMS = {
    "NAVER": ("네이버",),
    "S-Oil": ("에쓰오일",),
    "우리금융지주": ("우리금융",),
    "iM금융지주": ("iM금융", "iM뱅크"),
    "현대엘리베이터": ("현대 엘리베이터",),
    "KB발해인프라": ("KB 발해인프라",),
    "에스엠": ("SM엔터테인먼트", "에스엠엔터테인먼트", "SM엔터"),
    "동서": ("동서식품",),
    "LS ELECTRIC": ("LS일렉트릭",),
    "NHN": ("NHN페이코",),
    "LIG디펜스앤에어로스페이스": ("LIG 디펜스앤에어로스페이스", "LIG D&A"),
    "하림지주": ("하림그룹",),
    "롯데지주": ("롯데그룹",),
    "메리츠금융지주": ("메리츠금융",),
}
# 사람이 읽어 그 회사가 아니라고 확인한 표기. 판정 전에 지운다.
_EXCLUDED_PHRASES = {
    "태성": ("태성무역", "태성자산운용", "태성산업", "태성기업", "태성FC", "태성고", "청년 태성", "태성 역", "태성으로"),
    "동서": ("동서횡단", "동서관통", "동서고속", "동서고금", "동서대학교", "동서교차로", "동서도로", "동서석유화학", "동서지간", "동서 간", "동서 양방향"),
    "에스엠": ("에스엠디자인", "에스엠하이플러스", "에스엠벡셀", "에스엠테크"),
    "스피어": ("더 스피어", "미디어 스피어", "스피어 엔터테인먼트", "스피어리그", "스피어텍", "스피어코퍼레이션", "라스베이거스 스피어", "공연장인 스피어"),
    "우리기술": ("우리기술투자",),
    "씨젠": ("씨젠의료재단",),
    "농심": ("농심천심", "농심N천심", "호텔농심"),
    "풍산": ("풍산 류씨", "풍산읍", "풍산면", "풍산역", "풍산초", "풍산중", "풍산시장", "풍산점"),
    "삼현": ("앙상블 삼현", "삼현육각", "삼현삼죽", "삼현승무"),
    "후성": ("후성유전", "외상후성"),
    "DL": ("mg/dL", "LDL", "HDL"),
    "GS": ("GS Caltex", "GS E&C"),
    "LS": ("LS MnM",),
    "LG": ("LG Chem", "LG CNS"),
}


def _is_short_ascii(name: str) -> bool:
    return name.isascii() and len(name) <= 2


def _is_strict(name: str) -> bool:
    return len(name) <= 2 or name in _STRICT_NAMES or (name.isascii() and len(name) <= 3)


def needs_context(name: str) -> bool:
    return _is_short_ascii(name) or name in _CONTEXT_NAMES


def name_forms(name: str) -> list[str]:
    """그 종목을 가리키는 표기: 공식 이름, 별칭, 뉴스 표기."""
    return [name, *(a for a, n in COMPANY_ALIASES.items() if n == name), *_EXTRA_FORMS.get(name, ())]


def _mentions_word(text: str, forms: list[str], strict: bool) -> bool:
    """표기가 독립된 단어로 나오는지. 앞에 글자가 붙으면("이태성", "제주SK", "@naver.com") 다른 말이다.
    뒤는 영문 표기에 영문이 이어지면("KT&G", "SKT") 다른 말이고, strict면 한글도 조사·문맥어일 때만 인정한다."""
    for form in forms:
        for m in re.finditer(re.escape(form), text, re.IGNORECASE):
            prev = text[m.start() - 1] if m.start() else " "
            rest = text[m.end():]
            nxt = rest[0] if rest else " "
            if _ALNUM_RE.match(prev) or _DOMAIN_TAIL_RE.match(rest):
                continue
            if form.isascii() and _ASCII_ALNUM_RE.match(nxt):
                continue
            if strict and _HANGUL_RE.match(nxt) and not _FOLLOW_RE.match(rest):
                continue
            return True
    return False


def title_mentions(company_name: str, title: str | None) -> bool:
    """제목에 이 회사가 독립된 이름으로 있는지(정렬용, DB 없음). 뒤에 조사·문맥어 말고 다른 글자가 붙으면 다른 이름으로 본다("카카오뱅크", "SK하이닉스")."""
    text = title or ""
    for phrase in _EXCLUDED_PHRASES.get(company_name, ()):
        text = text.replace(phrase, " ")
    return _mentions_word(text, name_forms(company_name), True)


def is_relevant_news(company_name: str, ticker: str | None, asset_type: str | None, title: str | None,
                     content: str | None, listed_names=()) -> bool:
    """이 기사가 그 종목 기사인지. listed_names(상장 종목 이름 전체)에서 이 이름을 포함한 더 긴 이름("SK하이닉스")은 먼저 지운다.
    ETF는 기존 기준(mentions_etf_name)을 그대로 쓴다."""
    text = f"{title or ''} {content or ''}"
    longer = sorted((n for n in listed_names if n != company_name and company_name in n), key=len, reverse=True)
    if asset_type == "etf":
        return mentions_etf_name(text, company_name, longer)
    if ticker and re.search(rf"(?<![0-9A-Za-z]){re.escape(ticker)}(?![0-9A-Za-z])", text):
        return True
    for other in longer:
        text = text.replace(other, " ")
    for phrase in _EXCLUDED_PHRASES.get(company_name, ()):
        text = text.replace(phrase, " ")
    if not _mentions_word(text, name_forms(company_name), _is_strict(company_name)):
        return False
    if needs_context(company_name):
        if _FINANCE_RE.search(text):
            return True
        sector = _SECTOR_WORDS.get(company_name)
        return bool(sector.search(text)) if sector else bool(_GROUP_CONTEXT_RE.search(text))
    return True
