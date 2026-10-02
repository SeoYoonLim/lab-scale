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
