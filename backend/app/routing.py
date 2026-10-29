"""질문 문구로 tool 후보를 미리 좁히는 코드 레벨 사전 분류.

market_tool(종목 vs 시장 지수 등락률 비교)을 LLM에 항상 보여주면 stock_tool+news_tool 동시 호출(multi)이 깨진다
(scripts/benchmark_routing.py, n=96: 60% -> 36%). 그래서 시장 비교를 묻는 질문일 때만 그 요청의 tool 목록에 넣는다.
여기서 True가 나와도 "후보로 보여줄 뿐"이라 오탐의 비용은 그 질문 한 건의 tool 목록이 5개가 되는 정도다.
"""

import re

# 시장/지수 비교를 묻는 표현. 한 패턴이라도 맞으면 True.
_MARKET_PATTERNS = [
    r"코스피|코스닥|kospi|kosdaq",
    # 시장/증시 전체·대비·평균 ... ('시장 점유율', '시장 진출', '시장 조사'는 비교가 아니라서 넣지 않는다)
    r"(?:시장|증시)\s*(?:전체|전반|대비|평균|흐름|수익률|보다|요인|분위기|영향|지수)",
    # '장 전체', '장 대비'. '공장 전체'처럼 한글 뒤에 붙은 '장'은 제외한다.
    r"(?<![가-힣])장\s*(?:전체|전반|대비|평균|흐름|분위기)",
    # '지수 대비/보다/와 ...'. 그냥 '지수'나 '소비자물가지수' 같은 말은 걸리지 않게 뒤 표현까지 본다.
    r"지수\s*(?:대비|보다|와|과|랑|하고|비교|흐름|때문|영향|상승률|하락률|수익률)",
    r"초과\s*수익|상대\s*수익|벤치마크|장세",
    # '종목만의 문제인지 시장 때문인지'를 가르는 질문
    r"개별\s*(?:종목\s*)?(?:요인|이슈)|종목\s*만의",
]
_MARKET_RE = re.compile("|".join(_MARKET_PATTERNS), re.IGNORECASE)


def needs_market_tool(question: str) -> bool:
    """질문이 종목을 시장(코스피/코스닥) 지수와 비교하려는 내용이면 True."""
    return bool(_MARKET_RE.search(question or ""))


# 환율(원/달러) 수준·추이를 묻는 표현. "환율"이라는 단어 자체가 이미 충분히 구체적이라(일반 뉴스/공시 질문에
# 섞여 나올 일이 거의 없음) market_tool의 '지수'처럼 뒤 문맥까지 보지 않는다. market_tool의 코스피/코스닥 패턴과
# 같은 정도의 오탐(예: "환율이 오르면 왜 수출주가 유리해?" 같은 개념 질문에도 True)은 허용한다 - 오탐의 비용은
# tool 목록이 하나 늘어나는 정도라서(README FR-06 해결 기록 참고) 과도하게 좁히지 않는다.
_FX_PATTERNS = [
    r"환율",
    r"원\s*/?\s*달러|달러\s*/?\s*원|usd\s*/?\s*krw",
]
_FX_RE = re.compile("|".join(_FX_PATTERNS), re.IGNORECASE)


def needs_fx_tool(question: str) -> bool:
    """질문이 원/달러 환율 수준·추이를 조회하려는 내용이면 True."""
    return bool(_FX_RE.search(question or ""))


# 특정 종목을 지정하지 않고 시장 전체에서 급등/급락/거래량 급증 종목을 찾으려는 표현.
# "관심 종목"은 FR-12(watchlist)와 같은 단어를 쓰지만 뜻이 달라서("추천/탐색" 류 동사가 붙을 때만), "~에 추가해줘"
# 같은 watchlist 조작 표현과는 겹치지 않는다(애초에 이 서비스의 채팅 에이전트는 watchlist를 다루지 않는다).
# "급등"/"급락"은 "종목" 없이 단독으로 쓰면 "삼성전자 주가 급등한 이유" 같은 특정 종목 질문에도 걸려서(실측으로
# 확인), 반드시 "종목"과 함께 나올 때만 인정한다 - 그 외(시장 전체 탐색이 아닌) 급등/급락 질문은 news_tool 등
# 기존 경로로도 답이 된다.
_DISCOVERY_FIXED_PATTERNS = [
    r"(?:급등|급락|상한가|하한가).{0,4}종목",
    r"종목.{0,6}(?:급등|급락)",
    r"(?:요즘|오늘|최근).{0,6}(?:뜨는|핫한|인기)\s*종목",
    r"오늘의\s*관심\s*종목",
    r"관심\s*종목\s*(?:추천|탐색)",
    r"(?:오늘|최근).{0,10}(?:많이|크게)\s*(?:오른|내린|떨어진|빠진)\s*종목",
]
_DISCOVERY_FIXED_RE = re.compile("|".join(_DISCOVERY_FIXED_PATTERNS), re.IGNORECASE)
_VOLUME_RE = re.compile("거래량")
_VOLUME_CHANGE_RE = re.compile(r"급증|급등|증가|늘어|폭증")


def needs_discovery_tool(question: str) -> bool:
    """질문이 특정 종목 없이 시장 전체에서 급등/급락/거래량 급증 종목을 찾으려는 내용이면 True.

    "거래량...급증/증가/늘어" 류는 어순이 다양해서("거래량이 평소보다 많이 늘어난 종목") 고정 거리 패턴
    대신 두 낱말이 질문 어디에든 같이 있으면 True로 본다(시장 전체 탐색이 아닌 "특정 종목 거래량 늘었어?"
    질문도 걸릴 수 있지만, 다른 게이트들과 같은 수준의 허용 가능한 오탐이다)."""
    q = question or ""
    if _DISCOVERY_FIXED_RE.search(q):
        return True
    return bool(_VOLUME_RE.search(q) and _VOLUME_CHANGE_RE.search(q))


# 하루치 수치가 아니라 주가의 "흐름"을 묻는 표현. 모델은 이런 질문에도 stock_tool의 period_days를 기본값 1로
# 채우거나("삼성전자 최근 주가 흐름 알려줘" 10/10) 호출마다 1/10/30으로 다르게 추측해서("현대차 주가 추이 좀 보여줘"),
# 기간을 말하지 않은 흐름 질문은 코드가 기본 기간을 정한다(app/agent.py의 _apply_default_period).
# "최근/요즘"은 바로 뒤에 주가·시세·상황이 올 때만 인정한다("최근 뉴스", "최근 공시"는 흐름 질문이 아니다).
_TREND_RE = re.compile(r"흐름|추이|추세|동향|움직임|(?:최근|요즘|요새)\s*(?:주가|시세|상황|근황)")


def is_trend_question(question: str) -> bool:
    """질문이 주가의 흐름/추이(또는 '최근 주가/상황')를 묻는 내용이면 True. 기간 표현이 있는지는 보지 않는다."""
    return bool(_TREND_RE.search(question or ""))


# 질문이 "주가, 뉴스, 공시를 같이 확인해줘"처럼 정보 종류를 이름으로 직접 요청한 경우에 대응하는 tool.
# 모델은 이런 질문에서도 10번에 1~2번은 stock_tool만 부른다(종목 상세 화면의 [리서치하기] 버튼 질문, 삼성전자 8/10,
# 카카오 9/10). 프롬프트를 고치면 다른 라우팅이 흔들리므로(README FR-06 기록) 프롬프트는 그대로 두고, 1차 호출이
# 끝난 뒤 빠진 tool만 코드가 덧붙인다(app/agent.py의 _add_requested_tools).
_REQUESTED_TOOLS = [
    ("stock_tool", re.compile(r"주가")),
    ("news_tool", re.compile(r"뉴스")),
    ("disclosure_tool", re.compile(r"공시")),
]
# "뉴스 말고 주가만"처럼 일부를 빼달라는 질문은 낱말만으로 판단할 수 없어서 아예 손대지 않는다.
_REQUEST_EXCLUSION_RE = re.compile(r"말고|빼고|제외|없이")


def explicitly_requested_tools(question: str) -> list[str]:
    """질문이 주가·뉴스·공시 중 둘 이상을 이름으로 요청하면 해당 tool 이름 목록, 아니면 빈 리스트.

    하나만 나온 질문("삼성전자 최근 공시 뭐 있어?")은 모델의 판단이 이미 안정적이고 rag_search_tool과의 구분
    (목록 vs 내용)이 걸려 있어서 건드리지 않는다."""
    q = question or ""
    if _REQUEST_EXCLUSION_RE.search(q):
        return []
    wanted = [name for name, pattern in _REQUESTED_TOOLS if pattern.search(q)]
    return wanted if len(wanted) >= 2 else []


# 주가가 오르내린 "원인"을 묻는 질문: 원인 표현과 등락 표현이 함께 있어야 한다.
# 시스템 프롬프트가 "'왜 올랐는지/내렸는지' 같이 뉴스가 필요한 질문에는 news_tool"이라고 안내해서 모델은 이런 질문에
# news_tool만 부른다(5개 질문 x 5회 = 25회 중 25회, stock_tool 0회). 그러면 실제 주가가 어떻게 움직였는지 모르는 채로
# 질문의 전제를 그대로 받아 "삼성전자 왜 올랐어?"에 오른 이유를 지어내거나(실제로는 20거래일 -2.59%) 주가와 무관한
# 최신 기사를 원인이라고 답한다. 프롬프트는 그대로 두고, 1차 호출 뒤에 주가 조회를 코드가 덧붙인다
# (app/agent.py의 _add_requested_tools).
# "올라온 공시"(게시됐다는 뜻)는 등락 표현이 아니라서 '올라' 뒤에 온/왔/오가 오면 제외한다.
_CAUSE_RE = re.compile(r"왜|이유|원인|때문|까닭")
_PRICE_MOVE_RE = re.compile(
    r"올랐|올라(?![온왔오])|오르|오른|상승|급등|폭등"
    r"|떨어졌|떨어지|떨어진|떨어져|하락|급락|폭락|내렸|내린|내려가|빠졌|빠져|빠진|빠지"
)


def is_cause_question(question: str) -> bool:
    """질문이 등락의 원인을 묻는 내용이면 True. 원인 표현만 있거나("PER이 왜 중요해?") 등락 표현만 있으면
    ("오늘 많이 오른 종목 알려줘") False다. 종목이 특정됐는지는 여기서 보지 않는다(호출측이 tool 호출 인자로 본다)."""
    q = question or ""
    return bool(_CAUSE_RE.search(q) and _PRICE_MOVE_RE.search(q))


# 뉴스·공시 같은 "사건" 이후 주가 반응을 묻는 질문(app/tools/event_reaction_tool.py). 사건어가 먼저 나오고 그 뒤에
# 시간 관계어(이후/직후/나온 뒤/발표 후/~했을 때 주가)가 와야 한다 - "9월 이후 뉴스"처럼 순서가 반대면 기간 표현이다.
# "반응"은 뜻이 넓어서 사건어와 함께 있을 때만 인정하고(순서 무관), 종목이 특정됐는지는 호출측(app/agent.py)이 본다.
# "삼성전자 최근 뉴스 알려줘", "삼성전자 공시 알려줘", "뉴스랑 주가 움직임 같이 알려줘"(동시 조회)는 관계어가 없어서 걸리지 않는다.
_EVENT_WORD = (
    r"공시|뉴스|기사|보도|발표|실적|소식|유상증자|무상증자|합병|분할|배당|자기주식|자사주|공급계약|소송"
)
_EVENT_AFTER_RE = re.compile(
    r"(?:" + _EVENT_WORD + r")[^.?!\n]{0,15}?"
    r"(?:이후|직후"
    r"|(?:나온|나오고|난|나고|뜬|뜨고|있은|있었던|있고|낸|내고|올라온|공개된|발표한|발표된|발표하고|공시된|보도된|된|한)"
    r"\s*(?:뒤|후|다음(?:\s*날)?)(?:에|로|엔)?(?![가-힣])"
    # 동사 없이 "공시 후 주가", "발표 뒤에 주가 흐름"처럼 쓴 경우는 바로 뒤에 주가 관련 낱말이 올 때만("뉴스 다음 기사"는 제외)
    r"|(?<![가-힣])(?:뒤|후|다음(?:\s*날)?)(?:에|로|엔)?(?![가-힣])\s*(?:의\s*)?(?:주가|주식|시세|반응|흐름|움직|등락|수익률)"
    r"|했을\s*때\s*(?:주가|주식|시세)|됐을\s*때\s*(?:주가|주식|시세)|나왔을\s*때\s*(?:주가|주식|시세))"
)
_EVENT_WORD_RE = re.compile(_EVENT_WORD)
_REACTION_RE = re.compile(r"반응")


def needs_event_reaction_tool(question: str) -> bool:
    """질문이 뉴스·공시(사건) 이후의 주가 반응을 묻는 내용이면 True.

    True만으로 tool을 실행하지는 않는다 - 질문(또는 후속 질문이면 직전 질문)에서 종목을 하나 특정할 수 있어야 한다."""
    q = question or ""
    if _EVENT_AFTER_RE.search(q):
        return True
    return bool(_REACTION_RE.search(q) and _EVENT_WORD_RE.search(q))


_EVENT_DISCLOSURE_RE = re.compile(r"공시|유상증자|무상증자|합병|분할|자기주식|자사주|공급계약")
_EVENT_NEWS_RE = re.compile(r"뉴스|기사|보도|소식")


def event_type_for(question: str) -> str:
    """질문의 사건 종류: 공시 쪽 낱말만 있으면 disclosure, 뉴스 쪽 낱말만 있으면 news, 둘 다 있거나 둘 다 없으면
    ("실적 발표") all."""
    q = question or ""
    disc, news = bool(_EVENT_DISCLOSURE_RE.search(q)), bool(_EVENT_NEWS_RE.search(q))
    if disc and not news:
        return "disclosure"
    if news and not disc:
        return "news"
    return "all"


def event_keyword_for(question: str, allowed) -> str | None:
    """질문에 나온 제목 키워드(허용 목록 allowed 중 질문에 먼저 나온 것). '자사주'는 공시 제목 표기인 '자기주식'으로 본다."""
    q = (question or "").replace("자사주", "자기주식")
    hits = [(q.find(k), k) for k in allowed if k in q]
    return min(hits)[1] if hits else None


# ---------- 개인화 브리핑 (2026-10-28 추가: 기존 게이트의 동작은 그대로) ----------
# "오늘 브리핑", "내 보유종목 어때", "관심종목 뭐 달라졌어"처럼 로그인 사용자 자신의 보유·관심종목을 묻는 질문이면
# 에이전트가 LLM 없이 app.briefing 계산(GET /api/briefing과 같은 값)으로 답한다. 종목 질문("삼성전자 오늘 어때")은 해당하지 않는다.
_BRIEFING_WORD_RE = re.compile(r"브리핑")
_MY_RE = re.compile(r"(?:^|[\s,])(?:내|나의|제|우리)\s*(?:모의\s*투자\s*)?")
_OWN_LIST_RE = re.compile(r"보유\s*종목|보유한\s*종목|보유\s*주식|가진\s*종목|들고\s*있는\s*종목|관심\s*종목|포트폴리오|계좌|잔고")
_LIST_ONLY_RE = re.compile(r"보유\s*종목|관심\s*종목")
_STATUS_RE = re.compile(r"어때|어떻|달라|변화|바뀌|요약|상황|현황|정리|알려|보여|브리핑|움직|소식")
# 관심종목·주문 조작(추가/삭제/매수)은 브리핑이 아니다
_BRIEFING_ACTION_RE = re.compile(r"추가|삭제|등록|빼줘|빼 줘|넣어|매수|매도|주문|사줘|팔아줘|초기화|진단")
_WATCH_SCOPE_RE = re.compile(r"관심\s*종목")
_HOLD_SCOPE_RE = re.compile(r"보유|가진\s*종목|들고\s*있는|포트폴리오|계좌|잔고")


def needs_briefing(question: str) -> bool:
    """로그인 사용자의 개인화 브리핑을 묻는 질문이면 True."""
    q = question or ""
    if _BRIEFING_ACTION_RE.search(q):
        return False
    if _BRIEFING_WORD_RE.search(q):
        return True
    if not _OWN_LIST_RE.search(q) or not _STATUS_RE.search(q):
        return False
    # "포트폴리오 분산이란?"처럼 일반 개념은 소유 표현(내/나의)이나 보유·관심종목이라는 말이 있어야 한다
    return bool(_MY_RE.search(" " + q) or _LIST_ONLY_RE.search(q))


def mentions_own(question: str) -> bool:
    """질문이 "내/나의/제/우리"처럼 질문자 자신의 것을 가리키는지(브리핑 게이트 needs_briefing의 판정은 바꾸지 않는다)."""
    return bool(_MY_RE.search(" " + (question or "")))


def briefing_scope(question: str) -> str:
    """브리핑 질문의 범위: 관심종목만 말하면 watchlist, 보유·계좌만 말하면 holdings, 그 밖(브리핑, 둘 다)은 all."""
    q = question or ""
    watch, hold = bool(_WATCH_SCOPE_RE.search(q)), bool(_HOLD_SCOPE_RE.search(q))
    if watch and not hold:
        return "watchlist"
    if hold and not watch:
        return "holdings"
    return "all"
