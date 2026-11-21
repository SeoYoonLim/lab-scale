"""질문의 분석 대상 종목 확인(2026-11-21, 최종 시연 전 품질 수정).

브라우저 테스트에서 종목을 말하지 않은 질문("오늘 주가 어때?")에 삼성전자를 분석해 답하고, 직전 삼성전자 대화 뒤 "엔비디아는 왜 하락했어?"에
삼성전자 시세·뉴스로 답했다. 실제 원인(재현으로 확인):
1. 종목 없는 질문이 코드 답 경로에 걸리지 않으면 LLM 경로로 가는데, 모델은 질문에 없는 "삼성전자"를 도구 인자로 채운다(도구 설명의 예시
   "예: 삼성전자, 005930"). 해석이 되는 이름이라 기존 보정(app.agent._repair_company_args)이 손대지 않았다.
2. 후속 질문에서 질문의 종목이 등록 종목에 없으면(엔비디아) "이번 질문에 종목이 없다"로 보고 직전 질문의 종목(삼성전자)으로 인자를 바꿨다.

우선순위: (1) 이번 질문에 명시한 종목 > (2) 유효한 직전 대화의 종목 > (3) 없으면 되묻기. 이 모듈은 아래만 한다(DB 쓰기 없음).
- foreign_unsupported_names: 등록 종목이 아닌 해외 상장사 이름(엔비디아·NVIDIA 등)을 질문에서 찾는다. 찾으면 다른 종목으로 대신 분석하지 않고 안내한다.
- is_generic_only: 질문이 종목 이름이 될 수 없는 일반어(주가·왜·떨어졌어·today …)로만 되어 있는가. 그러면 LLM에 보내지 않고 되묻는다.
- traceable: 모델이 고른 종목이 질문 원문에서 온 것인가(오타·외국어 표기 포함). 아니면 모델의 추측이다.
- resolved_instruments: 응답의 분석 기준 종목 목록(실제 도구 결과의 등록 종목만, 출처 explicit/previous_context).
"""

from __future__ import annotations

import re
import unicodedata

CLARIFY_ANSWER = "어느 종목을 말씀하시는 건가요? 삼성전자, SK하이닉스 등 분석할 종목을 알려 주세요."

# 응답의 clarification_reason 값
REASON_MISSING = "missing_instrument"  # 종목을 말하지 않았고 이어받을 직전 종목도 없다
REASON_UNSUPPORTED = "unsupported_instrument"  # 서비스 분석 대상이 아닌 종목(해외 상장사 등)을 말했다
REASON_UNRESOLVED = "unresolved_instrument"  # 종목 이름처럼 보이지만 등록 종목에서 찾지 못했다
REASON_AMBIGUOUS = "ambiguous_instrument"  # 그룹 이름처럼 여러 종목에 걸치는 이름만 말했다

# ---------- 분석 대상이 아닌 해외 상장사 ----------
# 이 서비스는 국내 상장 주식·ETF(company 테이블)만 분석한다. 아래는 사용자가 자주 묻는 해외 종목이다(표시 이름, 표기들).
# 한글·가나·한자 표기는 뒤에 조사나 비한글이 올 때만, 영문은 단어 경계에서만 찾는다("메타버스", "인텔리전스"는 아니다).
FOREIGN_UNSUPPORTED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("엔비디아(NVIDIA)", ("엔비디아", "NVIDIA", "Nvidia", "NVDA", "エヌビディア", "英伟达", "輝達", "辉达")),
    ("애플(Apple)", ("애플", "Apple", "AAPL", "アップル", "苹果公司", "蘋果公司")),
    ("테슬라(Tesla)", ("테슬라", "Tesla", "TSLA", "テスラ", "特斯拉")),
    ("마이크로소프트(Microsoft)", ("마이크로소프트", "마소", "Microsoft", "MSFT", "マイクロソフト", "微软", "微軟")),
    ("아마존(Amazon)", ("아마존", "Amazon", "AMZN", "アマゾン", "亚马逊", "亞馬遜")),
    ("알파벳(Alphabet)", ("알파벳", "구글", "Alphabet", "Google", "GOOGL", "GOOG", "グーグル", "谷歌")),
    ("메타(Meta)", ("메타", "Meta Platforms", "Meta", "Facebook", "페이스북", "メタ", "脸书", "臉書")),
    ("넷플릭스(Netflix)", ("넷플릭스", "Netflix", "NFLX", "ネットフリックス", "奈飞", "網飛")),
    ("TSMC", ("TSMC", "티에스엠씨", "台积电", "台積電")),
    ("인텔(Intel)", ("인텔", "Intel", "INTC", "インテル", "英特尔", "英特爾")),
    ("AMD", ("AMD",)),
    ("브로드컴(Broadcom)", ("브로드컴", "Broadcom", "AVGO")),
    ("퀄컴(Qualcomm)", ("퀄컴", "Qualcomm", "QCOM", "高通")),
    ("마이크론(Micron)", ("마이크론", "Micron", "MU")),
    ("팔란티어(Palantir)", ("팔란티어", "Palantir", "PLTR")),
    ("ASML", ("ASML",)),
    ("소니(Sony)", ("소니", "Sony", "ソニー", "索尼")),
    ("도요타(Toyota)", ("도요타", "토요타", "Toyota", "トヨタ", "丰田", "豐田")),
    ("알리바바(Alibaba)", ("알리바바", "Alibaba", "阿里巴巴")),
    ("텐센트(Tencent)", ("텐센트", "Tencent", "腾讯", "騰訊")),
)
# 한글 표기 뒤에 와도 되는 것: 조사(는/은/이/가/의/도/를/을/와/과/랑/에/만/로) 또는 한글이 아닌 글자·끝
_HANGUL_TAIL = r"(?=[은는이가의을를도과와랑에만로]|[^가-힣]|$)"


def _token_pattern(token: str) -> re.Pattern:
    if re.match(r"[A-Za-z]", token):
        # 영문: 단어 경계. 대문자 약칭(NVDA·MU·AMD)은 대소문자를 구분한다("mu"·"amd" 같은 일반 낱말과 섞이지 않게)
        flags = 0 if token.isupper() and len(token) <= 5 else re.IGNORECASE
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])", flags)
    if re.match(r"[가-힣]", token):
        return re.compile(r"(?<![가-힣])" + re.escape(token) + _HANGUL_TAIL)
    return re.compile(re.escape(token))


_FOREIGN_PATTERNS = [(display, [_token_pattern(t) for t in tokens]) for display, tokens in FOREIGN_UNSUPPORTED]


def foreign_unsupported_names(question: str, known_spans_text: str = "") -> list[str]:
    """질문에 나온 분석 대상 밖 해외 종목의 표시 이름들(나온 순서). known_spans_text(질문에서 찾은 등록 종목 이름들)에 포함된 표기는
    등록 종목 이름의 일부라 세지 않는다(예: ETF 이름 속 "테슬라")."""
    q = unicodedata.normalize("NFKC", question or "")
    found: list[tuple[int, str]] = []
    for display, pats in _FOREIGN_PATTERNS:
        for pat in pats:
            m = pat.search(q)
            if m and not (known_spans_text and m.group(0).casefold() in known_spans_text.casefold()):
                found.append((m.start(), display))
                break
    return [d for _, d in sorted(found)]


def unsupported_answer(names: list[str]) -> str:
    quoted = ", ".join(f"'{n}'" for n in names)
    return (f"{quoted}{_eun(names[-1])} 이 서비스의 분석 대상이 아닙니다. 이 서비스는 국내 상장 주식·ETF(등록 종목)만 분석하며, "
            "다른 종목으로 대신 분석하지 않았습니다. 분석할 국내 종목명이나 종목코드로 다시 질문해 주세요. "
            "예: \"삼성전자 최근 5거래일 분석해 줘\"")


def _eun(name: str) -> str:
    """'은/는' 고르기. 괄호 표기면 괄호 앞 한글 끝 글자로 본다(엔비디아(NVIDIA) -> 는)."""
    base = re.sub(r"\(.*\)$", "", name).strip() or name
    ch = base[-1]
    code = ord(ch) - 0xAC00
    if 0 <= code < 11172:
        return "은" if code % 28 else "는"
    return "은" if ch.isdigit() or ch.upper() in "LMNR" else "는"


# ---------- 종목 이름이 될 수 없는 일반어 ----------
# 질문의 낱말이 모두 이것으로 시작하면(또는 같으면) 종목을 말하지 않은 질문이다. 하나라도 남으면 종목 이름(오타·음차 포함)일 수 있어
# 기존 흐름(LLM 경로의 리졸버가 후보를 되묻는다)으로 간다.
_GENERIC_PREFIXES = (
    "오늘", "지금", "현재", "요즘", "요새", "최근", "어제", "이번", "지난", "금일", "올해", "이달", "한달", "일주일", "하루", "며칠",
    "주가", "주식", "가격", "시세", "종가", "시가", "고가", "저가", "얼마", "몇", "알려", "보여", "말해", "주세요", "부탁", "혹시",
    "이거", "이것", "그거", "그것", "해당", "종목", "어때", "어떻", "어떤", "어떨", "어땠", "궁금", "왜", "무슨", "무엇", "뭐", "뭔",
    "떨어", "올랐", "오른", "올라", "오를", "오르", "내렸", "내린", "내려", "내릴", "하락", "상승", "급등", "급락", "폭락", "폭등",
    "반등", "약세", "강세", "전망", "분석", "뉴스", "공시", "흐름", "동향", "추이", "추세", "수익률", "등락", "거래량", "이유", "원인",
    "설명", "정리", "요약", "앞으로", "향후", "예상", "예측", "판단", "상황", "움직", "변동", "해줘", "해주", "해 줘", "했어", "했나",
    "했지", "됐어", "됐나", "되나", "될까", "할까", "그럼", "그러면", "그리고", "근데", "그런데", "그래서", "많이", "조금", "갑자기",
    "계속", "자세히", "간단히", "좀", "줘", "거래일", "영업일", "봐줘", "봐 줘", "가르쳐",
)
_GENERIC_EXACT = frozenset({"이", "그", "저", "해", "돼", "된", "될", "할", "한", "다", "요", "거", "것", "더", "또", "잘", "주", "일", "원"})
_EN_GENERIC = frozenset({
    "how", "is", "are", "the", "a", "an", "stock", "stocks", "share", "shares", "doing", "today", "what", "whats", "why", "did", "does",
    "do", "fall", "fell", "fallen", "falling", "drop", "dropped", "dropping", "decline", "declined", "rise", "rose", "risen", "rising",
    "go", "going", "went", "gone", "up", "down", "this", "that", "these", "week", "weeks", "month", "months", "price", "prices",
    "analyze", "analyse", "analysis", "outlook", "forecast", "return", "returns", "news", "it", "its", "of", "over", "last", "past",
    "trading", "day", "days", "recent", "recently", "was", "were", "be", "been", "tell", "me", "about", "please", "show", "give",
    "performance", "trend", "and", "for", "in", "on", "to", "now", "current", "currently", "lately", "so", "much", "can", "could",
    "you", "i", "my", "with", "happening", "happened", "going", "look", "looks", "like", "will", "would", "should", "next", "yesterday",
    "explain", "summary", "summarize", "latest", "s",
})
_NUM_TOKEN_RE = re.compile(r"^\d+(?:거래일|영업일|일간|일|주일|주|개월|달|년|th|st|nd|rd)?$")
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def _is_generic_token(token: str) -> bool:
    if _NUM_TOKEN_RE.match(token) or token in _GENERIC_EXACT:
        return True
    if re.match(r"[A-Za-z]", token):
        return token.casefold() in _EN_GENERIC
    return token.startswith(_GENERIC_PREFIXES)


def is_generic_only(question: str) -> bool:
    """질문의 낱말이 모두 일반어인가(종목 이름이 될 낱말이 없다). 낱말이 없으면 False."""
    tokens = _TOKEN_RE.findall(unicodedata.normalize("NFKC", question or ""))
    return bool(tokens) and all(_is_generic_token(t) for t in tokens)


# 후속 질문의 첫 낱말이 종목 이름이 아니라 직전 종목에 대한 주제일 때("외국인은 왜 팔았어?", "배당은 어때?"). 이런 낱말은 종목을 찾지
# 못했다고 하지 않고 기존 흐름(직전 종목 이어받기)으로 둔다(app.agent._answer_unknown_followup_subject).
_FOLLOWUP_TOPICS = (
    "외국인", "기관", "개인", "수급", "공매도", "신용", "배당", "실적", "매출", "영업", "순이익", "이익", "부채", "재무", "주주", "자사주",
    "시총", "시가총액", "목표", "증권사", "애널", "리포트", "보고서", "차트", "지지", "저항", "이평", "이동평균", "거래대금", "변동성",
    "위험", "리스크", "호재", "악재", "이슈", "업황", "경쟁", "사업", "제품", "신사업", "투자", "밸류", "가치", "평가", "환율", "금리",
    "달러", "유가", "경기", "업종", "섹터", "테마", "그룹", "계열", "자회사", "지분", "합병", "분할", "상장", "유상", "무상", "결론",
    "정리", "요약", "다음", "내일", "모레", "단기", "중기", "장기", "기술", "펀더", "PER", "PBR", "ROE", "EPS",
    "포트폴리오", "보유", "계좌", "관심", "브리핑", "수익", "손익", "손실", "평가", "전체", "산업", "업계", "경쟁사", "동종", "둘", "두",
    "세", "이것들", "그것들", "그쪽", "거기", "여기", "저기", "이쪽", "앞", "뒤", "향후", "앞으로", "지금", "현재", "오늘", "어제",
)


def is_followup_topic(word: str) -> bool:
    return bool(word) and (word.startswith(_FOLLOWUP_TOPICS) or word.upper().startswith(("PER", "PBR", "ROE", "EPS")))


# ---------- 모델이 고른 종목이 질문에서 왔는가 ----------
_PARTICLE_TAIL_RE = re.compile(r"(?:이랑|랑|하고|에서|으로|로|은|는|이|가|의|도|을|를|와|과|만|에)$")


def _key(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").replace(" ", "").casefold()


def _candidates(question: str) -> list[str]:
    """질문에서 종목 이름일 수 있는 조각(일반어 제외, 조사 뗌, 1~3낱말 묶음)."""
    words = [w for w in _TOKEN_RE.findall(unicodedata.normalize("NFKC", question or "")) if not _is_generic_token(w)]
    words = [_PARTICLE_TAIL_RE.sub("", w) if re.match(r"[가-힣]", w) and len(w) > 2 else w for w in words]
    out: list[str] = []
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            piece = " ".join(words[i:i + n])
            if len(piece.replace(" ", "")) >= 2 and piece not in out:
                out.append(piece)
    return out


def traceable(db, raw, company, question: str) -> bool:
    """모델이 넘긴 종목 인자(raw, company로 해석됨)가 질문 원문에서 왔는가. 인자 문자열이 질문에 있거나, 질문의 어떤 조각이
    같은 종목으로 해석되면(오타·외국어 표기·약칭) True. 질문에 아무 흔적이 없으면 모델이 추측해 채운 종목이다."""
    from app.tools.company_resolver import _ALIASES_BY_NAME, resolve_company

    qk = _key(question)
    rk = _key(str(raw or ""))
    if len(rk) >= 2 and rk in qk:
        return True
    for token in (company.name, str(company.ticker), *_ALIASES_BY_NAME.get(company.name, ())):
        if len(_key(token)) >= 2 and _key(token) in qk:
            return True
    for piece in _candidates(question):
        try:
            res = resolve_company(db, piece)
        except Exception:  # noqa: BLE001 - 판정 실패는 "흔적 없음"이 아니라 판단 보류(True)로 본다
            return True
        if res.company is not None and res.company.id == company.id:
            return True
    return False


# ---------- 응답의 분석 기준 종목 ----------
_INSTRUMENT_TOOLS = frozenset({"stock_tool", "market_tool", "news_tool", "disclosure_tool", "rag_search_tool", "event_reaction_tool"})


def instrument_names(records: list[dict]) -> list[str]:
    """도구 결과에 나온 종목 이름(실행 순서, 중복 없음). 등록 종목인지는 호출측이 DB로 확인한다(못 찾은 결과의 이름은 입력 그대로라서)."""
    out: list[str] = []
    for rec in records or []:
        result = rec.get("result")
        if rec.get("tool_name") not in _INSTRUMENT_TOOLS or not isinstance(result, dict):
            continue
        name = result.get("company_name")
        if isinstance(name, str) and name.strip() and name not in out:
            out.append(name)
    return out


def build_resolved(rows: list[tuple[str, str]], question_names: set[str], has_previous: bool) -> list[dict]:
    """[(등록명, 종목코드)] -> [{"ticker", "name", "source"}]. 이번 질문에 있던 종목은 explicit, 질문에 없고 직전 대화를 이어받았으면
    previous_context. 질문에 없고 직전 대화도 없는 종목(오타를 리졸버가 바로잡은 경우)은 질문에서 온 것이라 explicit이다."""
    out = []
    for name, ticker in rows:
        source = "explicit" if name in question_names or not has_previous else "previous_context"
        out.append({"ticker": str(ticker), "name": name, "source": source})
    return out


def english_name(name: str) -> str | None:
    """등록명의 영어 표기(app.company_names_i18n·DART 영문명의 첫 영문 표기). 없으면 None(화면은 등록명을 쓴다)."""
    from app.company_names_dart import DART_ENGLISH_NAMES
    from app.company_names_i18n import FOREIGN_NAMES

    for table in (FOREIGN_NAMES, DART_ENGLISH_NAMES):
        for alias in table.get(name, ()):
            if re.fullmatch(r"[A-Za-z0-9&.,'()\- ]+", alias):
                return alias
    return None
