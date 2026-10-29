"""ETF 규칙(DB 없음): 브랜드 표기 변환, 기간 파싱 전 ETF 이름 가리기, 레버리지/인버스 판별과 고정 설명 문구,
ETF 답변 규칙, 뉴스 이름 확인, 뉴스 수치 가리기, 근거 없는 등락률 문장 제거, 빈 섹션 정리.

원칙: ETF에 대한 일반 설명(레버리지/인버스 구조)이나 판단은 LLM이 쓰지 않고 이 모듈의 고정 문구·규칙으로 넣는다.
ETF 답변에서 LLM이 쓸 수 있는 수치는 주가 조회(stock_tool) 결과뿐이다 - 뉴스에는 같은 계열의 다른 상품 수치가 섞여
있어서("반도체 레버리지 20% 급락…KODEX 레버리지는 12.45% 하락") 기사 속 수치를 이 ETF의 것으로 옮겨 적는 일이 있었다.
"""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.company_aliases import ETF_BRANDS, ETF_BRANDS_NO_HANGUL_AFTER, ETF_FORMER_BRANDS
from app.korean import josa

# ---------- 브랜드 표기 ----------


def _brand_pattern() -> re.Pattern:
    parts = []
    for ko in sorted(ETF_BRANDS, key=len, reverse=True):
        after = r"(?![가-힣])" if ko in ETF_BRANDS_NO_HANGUL_AFTER else ""
        parts.append(re.escape(ko) + after)
    for old in ETF_FORMER_BRANDS:
        parts.append(re.escape(old) + r"(?![A-Za-z0-9])")
    # 브랜드 앞에 한글/영문/숫자가 붙어 있으면(아이에이스, MYKODEX) 다른 낱말의 일부라서 브랜드가 아니다.
    return re.compile(r"(?<![가-힣A-Za-z0-9])(?:" + "|".join(parts) + ")", re.IGNORECASE)


_BRAND_RE = _brand_pattern()
_BRAND_REPLACEMENT = {**ETF_BRANDS, **{old.casefold(): new for old, new in ETF_FORMER_BRANDS.items()}}


def rewrite_etf_brand(text: str) -> str:
    """ETF 브랜드의 한글 표기를 영문으로("코덱스 200" -> "KODEX 200"), 이전 브랜드명은 현재 이름으로("KBSTAR 200",
    "케이비스타 200" -> "RISE 200") 바꾼다. 브랜드가 없으면 text 그대로다. "에이스침대" 같은 일반 이름은 바꾸지 않는다."""

    def sub(s: str) -> str:
        return _BRAND_RE.sub(
            lambda m: _BRAND_REPLACEMENT.get(m.group(0), _BRAND_REPLACEMENT.get(m.group(0).casefold(), m.group(0))), s
        )

    # 두 번 적용한다: 한글 -> 영문(케이비스타 -> KBSTAR) 다음에 이전 이름 -> 현재 이름(KBSTAR -> RISE)
    return sub(sub(text or ""))


# ---------- 기간 파싱 전 ETF 이름 가리기 ----------

# ETF 이름 앞머리(운용사 브랜드). TIME(타임폴리오)은 한글 표기 변환 대상은 아니지만 이름 앞머리로는 쓰인다.
ETF_NAME_BRANDS = tuple(sorted({*ETF_BRANDS.values(), *ETF_FORMER_BRANDS.values(), "TIME"}, key=len, reverse=True))
_ETF_BRAND_START_RE = re.compile(
    r"(?<![A-Za-z0-9가-힣])(?:" + "|".join(map(re.escape, ETF_NAME_BRANDS)) + r")(?![A-Za-z])", re.IGNORECASE
)
# 토큰이 이 모양으로 시작하면 이름이 아니라 기간 표현이다("3개월", "20일", "1주일", "5거래일").
_PERIOD_TOKEN_RE = re.compile(r"\d+\s?(?:거래일|일|주|개월|달|년|분기)")
_HAS_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
_TOKEN_RE = re.compile(r"\s*\S+")


def mask_etf_names(text: str) -> str:
    """기간 파싱용: 질문에서 ETF 이름 구간을 공백으로 지운다("KODEX 200 주가" -> "          주가").

    ETF 이름은 브랜드로 시작하고("KODEX", "TIGER", ...), 그 뒤 첫 토큰("200", "미국S&P500", "CD1년금리플러스액티브(합성)")과
    영문·숫자가 섞인 뒤 토큰("200 IT", "미국테크TOP10 INDXX")까지가 이름이다. 뒤 토큰이 기간 표현으로 시작하거나("3개월")
    영문·숫자가 없는 한글 낱말이면("주가", "최근") 거기서 이름이 끝난 것으로 본다. 브랜드의 한글 표기("코덱스 200")는 영문으로
    바꾼 뒤 본다. 브랜드로 시작하지 않는 텍스트(주식 질문)는 바꾸지 않는다."""
    s = rewrite_etf_brand(text or "")
    out, pos = [], 0
    for m in _ETF_BRAND_START_RE.finditer(s):
        if m.start() < pos:
            continue
        end = m.end()
        first = True
        while True:
            t = _TOKEN_RE.match(s, end)
            if t is None:
                break
            token = t.group().strip()
            if _PERIOD_TOKEN_RE.match(token):
                break
            if not first and not _HAS_ALNUM_RE.search(token):
                break
            # 첫 토큰은 브랜드에 붙어 있거나(KODEX200) 공백 하나 뒤에 와야 이름의 일부다
            if first and len(t.group()) - len(t.group().lstrip()) > 1:
                break
            end = t.end()
            first = False
        out.append(s[pos:m.start()])
        out.append(" " * (end - m.start()))
        pos = end
    out.append(s[pos:])
    return "".join(out)


# ---------- 레버리지 / 인버스 ----------

_INVERSE_RE = re.compile(r"인버스|베어|-1X", re.IGNORECASE)
_LEVERAGE_RE = re.compile(r"레버리지|(?<![0-9.])[2-9](?:\.\d)?X(?![A-Za-z])", re.IGNORECASE)


def leverage_kind(name: str) -> str | None:
    """이름으로 판별한 레버리지/인버스 종류: "레버리지", "인버스", "인버스 레버리지"(인버스2X 등), 아니면 None."""
    inverse = bool(_INVERSE_RE.search(name or ""))
    leverage = bool(_LEVERAGE_RE.search(name or ""))
    if inverse and leverage:
        return "인버스 레버리지"
    if inverse:
        return "인버스"
    if leverage:
        return "레버리지"
    return None


def leverage_notice(name: str, kind: str) -> str:
    """레버리지/인버스 ETF 답변에 붙이는 고정 설명(수치 없음, 투자 권유 없음)."""
    return (
        f"※ {josa(name, '은/는')} 이름으로 보아 {kind} ETF입니다. 레버리지·인버스 ETF는 하루 동안의 기초지수 수익률을 "
        "정해진 배수(인버스는 반대 방향)로 따라가도록 설계돼 있어, 여러 날 보유하면 복리 효과 때문에 같은 기간 기초지수 수익률에 "
        "배수를 곱한 값과 결과가 달라질 수 있습니다. 시장이 오르내림을 반복할수록 그 차이가 커질 수 있습니다."
    )


# ---------- 구성종목·비중 질문 ----------
# 구성종목(PDF)·편입 비중은 2026-10-29부터 삼성 KODEX·미래에셋 TIGER ETF만 운용사 공시를 수집한다(app/etf_holdings.py). 그 밖의 ETF에는
# 데이터가 없다. 모델은 이런 질문에 가격·뉴스만 답하거나(평가 기준선 etf_03/04) 아는 대로 종목을 늘어놓을 수 있어서, 이런 질문은 코드가
# 답한다: 수집한 데이터가 있으면 템플릿(app/etf_holdings.holdings_answer), 없으면 아래 "제공하지 않음 + 확인 경로"(app/agent.py).
# ETF 이름 속 낱말("TIGER 반도체TOP10")에 걸리지 않게 이름을 가린 뒤 본다.
_CONSTITUENT_RE = re.compile(
    r"(?:구성|편입|보유|상위|담긴|담겨\s*있는|포함된|들어\s*있는|들어간)\s*(?:\d+\s*개\s*)?종목"
    r"|종목\s*(?:구성|비중|구성비|리스트|목록)"
    r"|구성\s*(?:비중|내역|현황|비율)|편입\s*비중|보유\s*비중"
    r"|(?:어떤|무슨|무엇|뭔)\s*종목.{0,12}(?:들어|담|포함|구성|편입)"
    r"|종목(?:들)?(?:이|을)?\s*(?:뭐뭐|다\s*)?(?:들어|담겨|포함)"
)


def asks_constituents(question: str) -> bool:
    """ETF의 구성종목·편입 비중·상위 보유종목을 묻는 질문이면 True. ETF가 질문에 있는지는 호출측이 본다."""
    return bool(_CONSTITUENT_RE.search(mask_etf_names(question or "")))


def constituents_answer(names: list[str]) -> str:
    """구성종목·비중 데이터가 없는 ETF의 고정 답(구성종목을 지어내지 않는다). 레버리지/인버스 ETF면 고정 설명을 덧붙인다."""
    joined = ", ".join(names)
    parts = [
        f"{joined}의 구성종목과 편입 비중 데이터는 이 서비스에서 제공하지 않습니다(보유한 ETF 데이터는 가격·거래량과 관련 뉴스입니다). "
        "구성종목과 비중은 수시로 바뀌므로, 정확한 내용은 운용사 공식 홈페이지의 상품 페이지(구성종목/PDF 내역)나 "
        "한국거래소·금융투자협회 공시에서 확인해주세요."
    ]
    parts += [leverage_notice(n, kind) for n in names if (kind := leverage_kind(n))]
    return "\n\n".join(parts)


# ---------- ETF 답변 규칙(최종 답변 단계, ETF 질문에만) ----------

ETF_ANSWER_RULES = (
    "이 질문에는 ETF(상장지수펀드)가 들어 있다. ETF는 개별 기업이 아니라 여러 자산을 담아 운용하는 펀드 상품이다. "
    "ETF에 대해 쓸 때 다음을 지켜라. "
    "(1) ETF를 회사처럼 서술하지 마라: 실적, 매출, 이익, 재무, 공시, 경영, 사업을 말하지 마라. "
    "(2) 조회 결과에 없는 구성종목, 추종지수, 운용보수, 순자산 규모, 운용사, 상품 구조를 단정하지 마라. "
    "(3) 등락률·가격·거래량 같은 수치는 주가 조회 결과에 있는 수치만 써라. 뉴스 기사의 수치는 쓰지 마라 - 기사에는 다른 상품의 "
    "수치가 섞여 있다. 뉴스는 어떤 이야기가 있었는지만 짧게 전하라. "
    "(4) 매수나 매도를 권하지 마라. "
    "(5) 조회 결과가 없는 내용은 섹션을 만들지 말고 아예 쓰지 마라."
)

# ---------- 뉴스: ETF 이름 확인, 수치 가리기 ----------


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "").casefold()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().casefold()


# 이름 바로 뒤에 붙어도 같은 이름으로 보는 한글(조사 첫 글자). "KODEX 200은", "KODEX 레버리지가", "코덱스200에서"
_JOSA_START = set("은는이가을를의에와과도로만으까보처랑께")


def _name_at(hay: str, start: int, end: int) -> bool:
    """hay[start:end]가 독립된 이름인지: 앞에 영문·숫자·한글이 붙어 있지 않고, 뒤에 다른 상품명이 이어지지 않는다
    (뒤가 끝/공백·기호/조사/"ETF"면 같은 이름이다. "TIGER 미국S&P500타겟…", "KODEX 200TR"은 다른 상품이다)."""
    prev = hay[start - 1] if start > 0 else ""
    if prev and ((prev.isascii() and prev.isalnum()) or "가" <= prev <= "힣"):
        return False
    nxt = hay[end] if end < len(hay) else ""
    if not nxt or not (nxt.isalnum() or "가" <= nxt <= "힣"):
        return True
    if hay.startswith("etf", end):
        return True
    return "가" <= nxt <= "힣" and nxt in _JOSA_START


def mentions_etf_name(text: str, name: str, other_names=()) -> bool:
    """text(기사 제목+본문)에 ETF 이름이 독립된 이름으로 나오는지. 공백·대소문자는 무시하고("KODEX200"), 한글 브랜드
    표기("코덱스 200")도 인정한다. 이름 뒤에 다른 상품명이 붙어 있거나("KODEX 200TR", "TIGER 미국S&P500타겟데일리커버드콜")
    더 긴 다른 ETF 이름(other_names)의 일부로만 나오면 이 ETF의 기사가 아니다."""
    key = _norm(name)
    if not key:
        return False
    hay = _norm(rewrite_etf_brand(text))
    for other in sorted({_norm(o) for o in other_names}, key=len, reverse=True):
        if len(other) > len(key) and key in other:
            hay = hay.replace(other, " ")
    for variant in {key, key.replace(" ", "")}:
        for m in re.finditer(re.escape(variant), hay):
            if _name_at(hay, m.start(), m.end()):
                return True
    return False


# 기사 속 수치는 LLM에 넘기지 않으려고 전부 가린다(한글 단위가 붙은 "9천억원", "1조2000억원", 순위·날짜 포함). 단위가 붙은 것만
# 가렸을 때는 "9천억"의 "9"가 남아 모델이 "9천만 원"처럼 다른 금액으로 복원했다.
_NEWS_NUMBER_RE = re.compile(
    r"[+-]?\d[\d,.]*(?:\s?[천백십만억조]+\s?\d*[\d,.]*)*\s?(?:%p|%|퍼센트|원|배|포인트|bp|주|위|명|건|개|일|월|년)?"
)
NUMBER_MASK = "[수치 생략]"


def mask_news_numbers(text: str) -> str:
    return _NEWS_NUMBER_RE.sub(NUMBER_MASK, text or "")


def news_result_for_llm(result: dict) -> dict:
    """news_tool 결과에서 기사 제목·본문의 수치를 가린 사본(LLM에 넘기는 용도). 원본 result는 바꾸지 않는다."""
    if not isinstance(result, dict) or not isinstance(result.get("news"), list):
        return result
    news = [
        {**n, "title": mask_news_numbers(n.get("title")), "content": mask_news_numbers(n.get("content"))}
        for n in result["news"]
    ]
    return {**result, "news": news, "note": "ETF 답변에서는 기사 속 수치를 쓰지 않는다(다른 상품의 수치가 섞여 있다)."}


# ---------- 답변 정리 ----------

_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")


def _variants(x: Decimal) -> set[Decimal]:
    x = abs(x)
    out = {x.normalize()}
    for q in ("1", "0.1", "0.01"):
        out.add(x.quantize(Decimal(q), rounding=ROUND_HALF_UP).normalize())
    return out


def allowed_percents(results) -> set[Decimal]:
    """tool 결과(주가 조회 등)에 나온 등락률 값과 반올림 표기: 이름에 pct가 들어간 숫자 필드, 문자열에서 % 바로 앞의 숫자.

    "20거래일"의 20, 날짜, 가격처럼 등락률이 아닌 숫자는 넣지 않는다 - 넣으면 기사 속 "20% 급락"이 "20거래일" 때문에
    통과한다(실제로 그랬다)."""
    found: set[Decimal] = set()

    def walk(v, key=""):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            if "pct" in key:
                found.update(_variants(Decimal(str(v))))
        elif isinstance(v, str):
            for m in _PCT_RE.findall(v.replace(",", "")):
                found.update(_variants(Decimal(m)))
        elif isinstance(v, dict):
            for k, val in v.items():
                walk(val, str(k))
        elif isinstance(v, (list, tuple)):
            for val in v:
                walk(val, key)

    walk(results)
    return found


# 문장 나누기: 마침표·물음표·느낌표 뒤 공백에서 나누되, 말줄임표("수익률... 등이", "…")에서는 나누지 않는다.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[^.…][.!?])\s+")


def _sentences(line: str) -> list[str]:
    return _SENTENCE_SPLIT_RE.split(line)


def drop_unsupported_percent_sentences(answer: str, allowed: set[Decimal]) -> tuple[str, list[str]]:
    """등락률(%) 수치가 allowed(주가 조회 결과의 등락률)에 없는 문장을 지운다. (정리된 답변, 지운 문장 목록).
    헤더 줄과 % 수치가 없는 문장은 그대로 둔다. 종목별 대조는 check_etf_answer가 한다."""
    dropped = []
    out_lines = []
    for line in (answer or "").split("\n"):
        if line.lstrip().startswith("#"):
            out_lines.append(line)
            continue
        kept = []
        for sent in _sentences(line):
            bad = [p for p in _PCT_RE.findall(sent) if Decimal(p).normalize() not in allowed]
            if bad:
                dropped.append(sent.strip())
            else:
                kept.append(sent)
        out_lines.append(" ".join(s for s in kept if s))
    return "\n".join(out_lines), dropped


# ---------- ETF 답변 검증: 종목별 수치·방향·출처 없는 상품 설명 ----------

# 단위가 붙은 수치. "주"는 거래량(1,000 이상)만 본다 - "2주간"(기간)과 구분하려고.
_UNIT_NUM_RE = re.compile(r"([+-]?\d[\d,]*(?:\.\d+)?)\s?(%p|%|원|주)")
# 한글 단위를 섞어 쓴 수치("1만 8천만 주", "9천억원"). 조회 결과(주가 요약)는 한글 단위를 쓰지 않으므로 그대로 옮긴 값이 아니다.
_KOREAN_NUM_RE = re.compile(r"\d[\d,.]*\s?[천백십만억조](?:\s?\d*[\d,.]*\s?[천백십만억조])*\s?(?:원|주)")
# 시장 지수·증시 전체를 가리키는 말. ETF 질문에서는 시장 지수 비교를 하지 않으므로, 이런 말에 붙은 수치는 조회 결과가 아니다
# ("이 기간 동안 코스피는 -4.26%로 하락" - 실제로는 ETF의 등락률).
_MARKET_TERM_RE = re.compile(r"코스피|코스닥|kospi|kosdaq|지수|증시", re.IGNORECASE)
# 수집하지 않는 데이터(거래대금)와 근거 없는 "최고/최저" 주장. 주가 요약에는 종가 최고·최저(원)만 있다.
_UNCOLLECTED_RE = re.compile(r"거래대금")
_EXTREME_RE = re.compile(r"최고|최저|신고가|신저가")
_KEY_UNITS = (("pct", "%"), ("volume", "주"), ("price", "원"), ("close", "원"), ("open", "원"), ("high", "원"), ("low", "원"))
_UP_RE = re.compile(r"상승|올랐|오른|올라|증가|플러스")
_DOWN_RE = re.compile(r"하락|내렸|내린|떨어|감소|빠졌|마이너스|급락")
# 조회 결과에 없는 상품 정보(추종지수·구성종목·보수·순자산·구조)를 단정하는 문장. 기사를 근거로 든 문장("기사에서는 …")은 남긴다.
_PRODUCT_CLAIM_RE = re.compile(
    r"추종|구성\s?종목|편입\s?종목|운용\s?보수|총\s?보수|순자산|기초\s?지수|지수.{0,12}따라|설계|구조(?!조정)"
)
# 주가를 조회했는데 "조회하지 못했다/알 수 없다"고 쓴 문장(실제로 있었다: KODEX 200TR 3회 중 2회).
_DENIES_DATA_RE = re.compile(r"조회하지 못|조회되지 않|조회 결과에 없|조회할 수 없|알려(?:줄|드릴) 수 없|확인할 수 없|확인하지 못")
_SOURCED_RE = re.compile(r"기사|뉴스|보도")


def typed_numbers(results) -> dict[str, set[Decimal]]:
    """tool 결과의 수치를 단위별로: {"%": 등락률, "원": 가격, "주": 거래량} (반올림 표기 포함, 부호 무시).
    숫자 필드는 이름으로(…pct, volume, …price/close/open/high/low), 문자열은 숫자 뒤 단위로 나눈다."""
    out: dict[str, set[Decimal]] = {"%": set(), "원": set(), "주": set()}

    def walk(v, key=""):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            for part, unit in _KEY_UNITS:
                if part in key:
                    out[unit].update(_variants(Decimal(str(v))))
                    break
        elif isinstance(v, str):
            for num, unit in _UNIT_NUM_RE.findall(v):
                out["%" if unit == "%p" else unit].update(_variants(Decimal(num.replace(",", ""))))
        elif isinstance(v, dict):
            for k, val in v.items():
                walk(val, str(k))
        elif isinstance(v, (list, tuple)):
            for val in v:
                walk(val, key)

    walk(results)
    return out


def _mentioned(sentence: str, names) -> list[str]:
    hay = _squash(rewrite_etf_brand(sentence))
    return [n for n in names if _squash(n) in hay]


def _without(sentence: str, subjects) -> str:
    """문장에서 질문 속 종목 이름을 지운 것("KODEX 코스닥150레버리지"의 '코스닥'을 시장 지수로 오인하지 않게)."""
    hay = _norm(rewrite_etf_brand(sentence))
    for name in sorted({_norm(n) for n in subjects}, key=len, reverse=True):
        hay = hay.replace(name, " ").replace(name.replace(" ", ""), " ")
    return hay


def _mentions_other(sentence: str, subjects, other_names) -> bool:
    hay = _without(sentence, subjects)
    return any(len(o) >= 2 and o in hay for o in (_norm(n) for n in other_names if n not in subjects))


def _unsupported_numbers(sentence: str, allowed: dict[str, set[Decimal]]) -> list[str]:
    bad = []
    for num, unit in _UNIT_NUM_RE.findall(sentence):
        unit = "%" if unit == "%p" else unit
        value = Decimal(num.replace(",", ""))
        if unit == "주" and abs(value) < 1000:
            continue
        if abs(value).normalize() not in allowed[unit]:
            bad.append(f"{num}{unit}")
    return bad


def _wrong_direction(sentence: str, result: dict) -> bool:
    """"N거래일간 상승/하락" 서술이 그 종목의 실제 기간 등락률 부호와 반대인지."""
    pct, days = result.get("period_change_pct"), result.get("trading_days")
    if pct is None or not days or f"{days}거래일" not in sentence.replace(" ", ""):
        return False
    up, down = bool(_UP_RE.search(sentence)), bool(_DOWN_RE.search(sentence))
    return (pct < 0 and up and not down) or (pct > 0 and down and not up)


def check_etf_answer(answer: str, price_results: dict[str, dict], other_names=()) -> tuple[str, list[tuple[str, str]]]:
    """ETF 답변을 주가 조회 결과(price_results: {종목명: stock_tool 결과})와 문장 단위로 대조해 맞지 않는 문장을 지운다.
    (정리된 답변, [(지운 문장, 사유)]).

    - 수치: 문장에 나온 등락률(%)·가격(원)·거래량(주)은 그 문장이 가리키는 종목의 조회 결과에 있어야 한다. 종목명이 없는
      문장은 조회한 종목 전체의 결과와 대조한다. ("KODEX 200 … -2.59%"처럼 다른 종목의 수치를 붙인 문장, "평균 종가 100,750원"
      처럼 결과에 없는 계산값, 거래량을 "거래대금 …원"으로 바꾼 문장을 막는다.)
    - 방향: "20거래일간 상승"처럼 기간 등락의 방향을 말했는데 실제 부호와 반대면 지운다.
    - 출처 없는 상품 설명: 추종지수·구성종목·보수·순자산·구조를 단정하는 문장은 기사를 근거로 든 경우가 아니면 지운다.
    - 질문에 없는 다른 종목(other_names: DB의 종목명들)의 이름을 수치와 함께 쓴 문장("삼성전자는 113,360원에서 …" - 실제로는
      TIGER 200의 수치)을 지운다.
    - 기사 수치를 가린 자리("[수치 생략]")가 남은 문장, 주가를 조회했는데 "조회하지 못했다"고 쓴 문장을 지운다.
    - 시장 지수(코스피·코스닥)에 수치를 붙이거나 그 등락을 기사 근거 없이 단정한 문장, 수집하지 않는 거래대금 수치,
      가격(원) 없이 "최고·최저"를 주장한 문장, 한글 단위 수치("1만 8천만 주")를 쓴 문장을 지운다.
    헤더 줄(#)은 그대로 둔다."""
    names = list(price_results)
    typed = {n: typed_numbers(r) for n, r in price_results.items()}
    union = {u: set().union(*(t[u] for t in typed.values())) if typed else set() for u in ("%", "원", "주")}
    dropped: list[tuple[str, str]] = []
    out_lines = []
    for line in (answer or "").split("\n"):
        if line.lstrip().startswith("#"):
            out_lines.append(line)
            continue
        kept = []
        for sent in _sentences(line):
            mentioned = _mentioned(sent, names)
            allowed = {u: set().union(*(typed[n][u] for n in mentioned)) for u in ("%", "원", "주")} if mentioned else union
            targets = mentioned or (names if len(names) == 1 else [])
            reason = None
            if NUMBER_MASK in sent:
                reason = "가린 수치 자리"
            elif price_results and _DENIES_DATA_RE.search(sent):
                reason = "조회한 주가를 없다고 함"
            elif _KOREAN_NUM_RE.search(sent):
                reason = "조회 결과에 없는 한글 단위 수치"
            elif _UNIT_NUM_RE.search(sent) and _mentions_other(sent, names, other_names):
                reason = "질문에 없는 종목에 수치를 붙임"
            elif _UNIT_NUM_RE.search(sent) and _MARKET_TERM_RE.search(_without(sent, names)):
                reason = "시장 지수에 수치를 붙임"
            elif _UNCOLLECTED_RE.search(sent) and re.search(r"\d", sent):
                reason = "수집하지 않는 거래대금 수치"
            elif _EXTREME_RE.search(sent) and not any(u == "원" for _, u in _UNIT_NUM_RE.findall(sent)):
                reason = "근거 없는 최고·최저 주장"
            elif bad := _unsupported_numbers(sent, allowed):
                reason = f"조회 결과에 없는 수치 {bad}"
            elif any(_wrong_direction(sent, price_results[n]) for n in targets):
                reason = "기간 등락 방향이 조회 결과와 반대"
            elif (_MARKET_TERM_RE.search(_without(sent, names)) and (_UP_RE.search(sent) or _DOWN_RE.search(sent))
                  and not _SOURCED_RE.search(sent)):
                reason = "시장 지수 등락을 근거 없이 단정"
            elif _PRODUCT_CLAIM_RE.search(sent) and not _SOURCED_RE.search(sent):
                reason = "출처 없는 상품 설명"
            if reason:
                dropped.append((sent.strip(), reason))
            else:
                kept.append(sent)
        out_lines.append(" ".join(s for s in kept if s))
    return "\n".join(out_lines), dropped


def comparison_sentence(price_results: list[dict]) -> str | None:
    """두 종목 이상을 조회했으면 조회 결과로 계산한 등락률 비교 문장(고정 템플릿). 비교할 결과가 둘 미만이면 None.
    마지막 거래일이 서로 다르면(수집 시점 차이) 같은 날짜 구간의 비교가 아니라는 점을 밝힌다."""
    rows = []
    for r in price_results:
        if not isinstance(r, dict) or not r.get("found"):
            continue
        if r.get("period_change_pct") is not None:
            rows.append((r["company_name"], r["period_change_pct"],
                         f"{r['period_start_date']}~{r['period_end_date']} {r['trading_days']}거래일간", r["period_end_date"]))
        elif r.get("change_pct") is not None:
            rows.append((r["company_name"], r["change_pct"], "마지막 거래일 하루", None))
    if len(rows) < 2:
        return None
    parts = [f"{josa(name, '은/는')} {span} {pct:+.2f}%" for name, pct, span, _ in rows]
    top = max(rows, key=lambda x: x[1])
    if sum(1 for x in rows if x[1] == top[1]) > 1:
        verdict = "이 기간 등락률은 같습니다."
    else:
        verdict = f"등락률은 {josa(top[0], '이/가')} 가장 높습니다." if len(rows) > 2 else f"등락률은 {josa(top[0], '이/가')} 더 높습니다."
    text = f"※ 조회 결과로 계산한 비교: {', '.join(parts)}입니다. {verdict}"
    ends = {x[3] for x in rows}
    if len(ends) > 1 and None not in ends:
        text += " 다만 종목마다 저장된 마지막 거래일이 달라 같은 날짜 구간의 비교는 아닙니다."
    return text


# 최종 답변의 빈 섹션 자리표시. 구조화 안내(agent.ANSWER_STRUCTURE_INSTRUCTION)가 쓸 수 있는 섹션 다섯 개를 나열하는데,
# 모델이 결과가 없는 섹션도 제목을 쓰고 "(이 섹션은 tool 호출 결과가 없으므로 빈칸입니다.)"처럼 채워 넣는다.
_PLACEHOLDER_RE = re.compile(
    r"빈칸|빈 칸|해당\s?(?:사항\s?)?없|(?:결과|정보|데이터|내용|신호|뉴스)(?:가|는)?\s?없(?:으므로|어서|습니다|음)|"
    r"제공되지 않|생략합니다|이 섹션은|N/?A|(?:분석|판단|확인)할 수 없|불명확|^[-(]?\s*없(?:음|습니다)\.?\)?$",
    re.IGNORECASE,
)


def remove_empty_sections(answer: str) -> str:
    """'## 제목' 섹션 중 본문이 비었거나 자리표시 문장("빈칸입니다", "해당 없음" 등)뿐인 섹션을 통째로 지운다.
    섹션이 없는 답변은 그대로 둔다. 모든 섹션이 지워지면 원문을 그대로 돌려준다(빈 답변을 내지 않는다)."""
    if not answer or "## " not in answer:
        return answer
    lines = answer.split("\n")
    blocks, cur = [], {"head": None, "body": []}
    for line in lines:
        if line.startswith("## "):
            blocks.append(cur)
            cur = {"head": line, "body": []}
        else:
            cur["body"].append(line)
    blocks.append(cur)

    def empty(body):
        text = [l.strip() for l in body if l.strip()]
        return not text or all(_PLACEHOLDER_RE.search(l) and len(l) <= 80 for l in text)

    kept = [b for b in blocks if b["head"] is None or not empty(b["body"])]
    if not any(b["head"] for b in kept) and any(b["head"] for b in blocks):
        return answer
    out = []
    for b in kept:
        if b["head"] is not None:
            out.append(b["head"])
        out.extend(b["body"])
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


# LLM 답변에서 코드가 대신 쓰는 섹션(가격·뉴스). 제목에 이 낱말이 있는 '## 섹션'은 통째로 빼고 코드가 만든 섹션을 넣는다.
_CODE_SECTION_RE = re.compile(r"^##\s*.*(?:주가|뉴스|기사|공시)")
MAX_ETF_NEWS_LINES = 5


def drop_sections(answer: str, pattern: re.Pattern) -> str:
    out, skipping = [], False
    for line in (answer or "").split("\n"):
        if line.startswith("## "):
            skipping = bool(pattern.search(line))
        if not skipping:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def compose_etf_answer(llm_answer: str, price_results: list[dict], news_results: list[dict]) -> str:
    """ETF 답변을 조립한다. 가격과 뉴스는 LLM이 옮겨 쓰지 않고 코드가 조회 결과로 쓴다:
    - '## 주가 동향': 종목별 주가 조회 요약(stock_tool의 summary 그대로)
    - '## 관련 뉴스': ETF 이름이 직접 나오는 기사 제목(출처·날짜), 종목당 최대 MAX_ETF_NEWS_LINES건
    - 그 밖의 LLM 섹션(원인 분석·위험요인 등, check_etf_answer를 거친 것)과 섹션 없는 문장은 뒤에 그대로 둔다.
    가격·뉴스를 LLM이 옮겨 쓰게 했을 때 다른 상품 기사를 이 ETF의 일처럼 쓰거나(분배금, 순매수 순위) 조회한 주가를 없다고 쓰는
    일이 반복돼서 이렇게 바꿨다."""
    parts = []
    summaries = [p["summary"] for p in price_results if isinstance(p, dict) and p.get("summary")]
    if summaries:
        parts.append("## 주가 동향\n" + "\n".join(summaries))
    lines = []
    for r in news_results:
        if not isinstance(r, dict) or not r.get("found"):
            continue
        for n in r.get("news", [])[:MAX_ETF_NEWS_LINES]:
            meta = ", ".join(x for x in (n.get("source"), (n.get("published_at") or "")[:10]) if x)
            lines.append(f"- {n.get('title')}" + (f" ({meta})" if meta else ""))
    if lines:
        parts.append("## 관련 뉴스\n" + "\n".join(lines))
    rest = drop_sections(llm_answer, _CODE_SECTION_RE)
    if rest:
        parts.append(rest)
    return "\n\n".join(parts)
