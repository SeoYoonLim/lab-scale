"""(2026-11-03) 전망·방향·매수 가부 질문과 그 후속 질문("그래서 결론이 뭐야?")은 코드가 DB에서 지표를 계산하고 문장도 코드가 만든다.
LLM은 부르지 않는다(숫자·사실 문장을 LLM이 쓰지 않는다). API.md "전망·후속 결론 질문" 절에 규칙을 문서화했다.

배경(수정 전 재현, evals/reports/forecast_20261103.md): "SK하이닉스 일주일 안에 오를지 내릴지 분석해줘"는 주가 조회 하나만 하고
"원인 분석"·"뉴스·공시 근거" 섹션을 일반론으로 채웠고, 같은 1주일에 +10.13%와 -8.25%(최고 종가 대비 하락률이자 하루 등락률)를 섞었다.
"그래서 결론이 뭐야?"는 용어 사전 경로가 가로챘고(app/glossary.py), 그다음 후속 질문은 직전 리포트(용어 사전 답, 종목 없음)만 이어받아
LLM이 종목을 지어내거나 종목 없이 의미 검색을 해 다른 회사 기사가 근거로 붙었다.

의도(우선순위는 app/agent.py _answer): 브리핑 > 전망·후속 결론(이 모듈) > 사건 반응 > 용어 사전 > 기간·보유 수익률·단순 시세 > 일반 도구.
모의 주문은 채팅이 아니라 주문 API(POST /api/portfolio/orders, 엄격 매칭)이고, 주문 문장(_ORDER_RE)은 이 경로로 보내지 않는다.
- is_forecast_question: 방향·전망·매수 가부·위험 표현. 종목은 질문에서 정확히 1개(여러 개면 기존 흐름, 임의 선택 없음).
  종목이 없으면 직전 리포트의 종목(previous["company_name"])을 이어받고, 그것도 없으면 어떤 종목인지 되묻는다.
- is_followup_verdict_question: 직전 종목이 있을 때의 결론·판단·확률·위험 질문. 종류는 followup_kind(verdict/probability/risk).

기간(PERIODS): 질문에 기간이 없으면 1주일. 후속 질문은 직전 답변의 기간을 이어받는다(답변 첫 줄의 표식, marker_of).
판정(judge): 상승·하락 신호 개수 차이가 SUMMARY_MARGIN 이상이면 우위, 미만이면 중립. 계산한 신호 지표가 MIN_SIGNAL_INDICATORS
미만이거나, 마지막 시세가 기대 거래일보다 MAX_LAG_BUSINESS_DAYS 영업일을 넘게 늦거나, 계산 구간에 지수에는 있는 거래일이 빠졌으면 판단 유보.
"""

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from app.backtest import sma, sma_target
from app.briefing import KST, MARKET_CLOSE, drop_open_session, session_is_open
from app.data_status import business_days_behind, last_business_day
from app.disclaimer import DISCLAIMER
from app.etf import leverage_kind, leverage_notice
from app.models.market_index import MARKET_INDEXES
from app.tools.market_tool import compare_returns, index_code_for_market


@dataclass(frozen=True)
class Period:
    label: str
    days: int  # 분석 기간(거래일). 주 수익률·지수 대비·교차 탐색 구간
    returns: tuple[int, int]  # 보여 줄 수익률 창(거래일)
    ma: tuple[int, int]  # 단기·장기 이동평균(일)
    range_days: int  # 최근 고점·저점 조회 구간(거래일)
    news_days: int  # 근거 뉴스·공시 발행일 필터(일)


PERIODS = {
    "1주일": Period("1주일", 5, (5, 20), (5, 20), 20, 14),
    "1개월": Period("1개월", 20, (20, 60), (20, 60), 60, 30),
    "3개월": Period("3개월", 60, (60, 120), (60, 120), 120, 90),
}
DEFAULT_PERIOD = "1주일"
RSI_PERIOD = 14
RSI_OVERBOUGHT, RSI_OVERSOLD = 70.0, 30.0
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
VOLUME_AVG_DAYS = 20
VOLUME_SURGE = 1.5
VOLATILITY_DAYS = 20
RETURN_FLAT_PCT = 1.0  # 분석 기간 수익률이 ±이 값 안이면 중립
RELATIVE_PP = 2.0
SUMMARY_MARGIN = 2  # 묶음 적용 전(judge_by_indicators) 기준
MIN_SIGNAL_INDICATORS = 5  # 묶음 적용 전(judge_by_indicators) 기준
# 지표 종류 -> 묶음. 학습 구간(신호일과 수익률 구간 끝이 2025-12-31 이하) 신호(-1/0/+1)의 피어슨 상관 절댓값을 세 기간에 걸쳐 평균하고,
# 평균 연결 군집을 |r| 0.5에서 끊어 정했다(scripts/backtest_outlook.py --analyze, API.md "신호 묶음"). 단일 연결은 지표가 사슬처럼 이어져
# 거래량을 뺀 전부가 한 묶음이 됐다. RSI는 추세와 음의 상관(과열 = 하락 쪽)이라 따로다.
# 2026-11-03 밤(2차 개정): 교차를 중기 추세에 합쳤다. 교차는 이동평균 배열이 바뀐 사건이라 같은 사실을 두 번 세게 된다
# (학습 구간 상관: 이동평균 배열-교차 +0.54/+0.58/+0.66, 세 기간 모두 0.5 이상). 이 개정은 검증 구간 결과를 본 뒤라 다시 잰 검증 구간은
# 엄밀한 홀드아웃이 아니다(API.md "신호 묶음").
SIGNAL_GROUPS: dict[str, str] = {
    "close_vs_ma_short": "단기 흐름", "period_return": "단기 흐름", "relative": "단기 흐름",
    "close_vs_ma_long": "중기 추세", "ma_alignment": "중기 추세", "cross": "중기 추세",
    "rsi": "RSI", "macd": "MACD", "volume": "거래량",
}
GROUP_LABELS: dict[str, str] = {
    "단기 흐름": "단기 흐름(단기 이동평균 위치·기간 수익률·지수 대비)",
    "중기 추세": "중기 추세(장기 이동평균 위치·이동평균 배열·교차)",
    "RSI": "RSI(14)", "MACD": "MACD(12,26,9)", "거래량": "거래량",
}
# 같은 사실을 두 번 세지 않는다(2026-11-04): 교차는 단기선·장기선 위아래가 바뀐 사건이라, 교차가 있으면 지금의 이동평균 배열과 항상 같은 쪽이다.
# 그래서 묶음 안에서 보이기만 하고 표에는 넣지 않는다(이동평균 배열이 있을 때). 묶음당 1표는 그대로다.
SAME_FACT_AS: dict[str, str] = {"cross": "ma_alignment"}
# 국면 분류(classify_regime, API.md "국면 규칙"). 기간과 관계없이 5·20·60일선과 5·20·60거래일 수익률, 최근 20거래일 최저 종가를 본다.
REGIME_REBOUND = "단기 반등 시도, 추세 전환은 미확인"
REGIME_UP = "상승 추세 지속"
REGIME_PULLBACK = "상승 추세 중 조정"
REGIME_DOWN = "하락 추세 지속"
REGIME_FLAT = "횡보, 방향 불분명"
REGIME_HOLD = "판단 유보(데이터 부족)"
REGIMES = (REGIME_REBOUND, REGIME_UP, REGIME_PULLBACK, REGIME_DOWN, REGIME_FLAT, REGIME_HOLD)
# 단기 방향(2026-11-04, 25차): 분석 기간의 방향은 묶음 판정 그대로다. 국면(추세)은 따로 보이고 방향을 덮어쓰지 않는다.
# 24차는 묶음 판정과 국면이 같은 방향일 때만 상승·하락이라 SK하이닉스(상승 2·하락 0·중립 3, 반등 시도)가 "중립(굳이 고르면 상승 쪽)"이 됐다.
DIR_UP, DIR_DOWN, DIR_NEUTRAL, DIR_HOLD = "상승 쪽", "하락 쪽", "중립", "판단 유보"
CONF_LOW, CONF_MID, CONF_HIGH = "낮음", "보통", "높음"
_REGIME_SHORT = {REGIME_REBOUND: "하락 후 반등 시도(추세 전환 미확인)", REGIME_UP: "상승 추세 지속", REGIME_PULLBACK: "상승 추세 중 조정",
                 REGIME_DOWN: "하락 추세 지속", REGIME_FLAT: "횡보", REGIME_HOLD: "판단 유보(데이터 부족)"}
_REGIME_SIDE = {REGIME_UP: DIR_UP, REGIME_DOWN: DIR_DOWN}
REGIME_DROP20_PCT = -10.0  # 20거래일 수익률이 이 이하면 큰 하락 뒤
REGIME_DROP60_PCT = -15.0  # 또는 60거래일 수익률이 이 이하면 큰 하락 뒤
REGIME_TREND20_PCT = 5.0  # 20거래일 수익률 ±이 값 이상이면 추세
REGIME_TREND60_PCT = 10.0  # 60거래일 수익률 ±이 값 이상이면 중기 추세
REGIME_REBOUND_FROM_LOW_PCT = 5.0  # 최근 20거래일 최저 종가 대비 이만큼 오르면 반등 신호
REGIME_DIP5_PCT = -3.0  # 5거래일 수익률이 이 이하면 단기 조정
RANGE52_DAYS = 250  # 52주 최고·최저 종가 조회 구간(거래일)
BIG_DAY_MOVE_PCT = 2.0  # 기준일 하루 등락률이 ±이 값 이상이면 근거 항목에 싣는다
GROUP_MARGIN = 2  # 묶음 표 차이. 학습 구간에서 1과 2 모두 예측력이 없어(상승 우위의 이후 상승 비율이 전체보다 낮음) 기존 값 2를 유지했다
MIN_GROUPS = 3
MAX_LAG_BUSINESS_DAYS = 5  # 마지막 시세가 이보다 많이 늦으면 판단 유보(1~5영업일은 경고만)
PRICE_ROWS = 260  # 120일선·MACD·변동성 계산과 장중 행 여유
NEWS_LIMIT = 3
DISCLOSURE_LIMIT = 3
NEWS_FETCH = 40  # 근거 후처리 전에 읽는 최근 뉴스 수
# 목표가·확률을 다루는 기사 제목은 근거 목록에 싣지 않는다(답변에 목표가·확률 수치를 쓰지 않는다)
_EXCLUDED_TITLE_RE = re.compile(r"목표\s*(?:주)?가|확률")

UP, DOWN, NEUTRAL = "up", "down", "neutral"
LABEL_UP, LABEL_DOWN, LABEL_NEUTRAL, LABEL_HOLD = "상승 우위", "하락 우위", "중립", "판단 유보"
NOT_A_FORECAST = "현재 신호의 종합이며 미래 가격을 예측하는 것이 아닙니다."
NO_PROBABILITY = "검증된 상승·하락 확률이나 기대수익률은 계산하지 않습니다. 과거 검증 수치는 과거에 관측된 빈도입니다."
ASK_SUBJECT_ANSWER = ("질문에서 종목을 찾지 못했습니다. 어떤 종목에 대한 질문인지 종목명 또는 종목코드로 알려주세요. "
                      "예: \"SK하이닉스 일주일 안에 오를까?\"")
JUDGE_RULE = (f"성격이 겹치는 지표를 묶어 묶음당 1표로 세고, 상승·하락 표 차이가 {GROUP_MARGIN}표 이상이면 많은 쪽의 우위, 그보다 작으면 중립. "
              f"계산한 묶음이 {MIN_GROUPS}개 미만이거나 마지막 시세가 {MAX_LAG_BUSINESS_DAYS}영업일을 넘게 늦거나 거래일이 빠졌으면 판단 유보")

DIRECTION_RULE = (f"분석 기간의 방향 = 묶음 판정(상승 우위면 상승 쪽, 하락 우위면 하락 쪽, 표 차이 {GROUP_MARGIN}표 미만이면 중립, "
                  "계산한 묶음 부족·시세 지연 5영업일 초과·거래일 누락이면 판단 유보). 국면(추세)은 따로 보이며 방향을 바꾸지 않는다")
CONFIDENCE_RULE = ("신뢰도(상승 쪽·하락 쪽일 때만): 과거 검증에서 그 신호 상태의 차이가 유의하지 않거나 결과가 없으면 낮음, 유의하지만 우세한 쪽 "
                   "묶음이 계산한 묶음의 절반 미만이거나 시세가 2영업일 이상 지연이면 보통, 그 밖은 높음. 신뢰도가 낮아도 방향을 중립으로 바꾸지 않고 "
                   "표현만 약하게 한다")
REGIME_RULE = (f"20거래일 수익률 {REGIME_DROP20_PCT:.0f}% 이하 또는 60거래일 {REGIME_DROP60_PCT:.0f}% 이하의 큰 하락 뒤 반등 신호(5거래일 수익률 > 0, "
               f"5일선 > 20일선, 최근 20거래일 최저 종가 대비 +{REGIME_REBOUND_FROM_LOW_PCT:.0f}% 이상 중 하나)가 있고 종가가 20일선이나 60일선 아래면 "
               f"'{REGIME_REBOUND}'. 종가 < 20일선 < 60일선이고 20거래일 -{REGIME_TREND20_PCT:.0f}% 이하 또는 60거래일 -{REGIME_TREND60_PCT:.0f}% "
               f"이하면 '{REGIME_DOWN}'. 60거래일 +{REGIME_TREND60_PCT:.0f}% 이상, 종가 > 60일선, 20일선 > 60일선이고 종가 < 20일선 또는 "
               f"5거래일 {REGIME_DIP5_PCT:.0f}% 이하면 '{REGIME_PULLBACK}'. 종가 > 20일선 > 60일선이고 20거래일 +{REGIME_TREND20_PCT:.0f}% 이상 또는 "
               f"60거래일 +{REGIME_TREND60_PCT:.0f}% 이상이면 '{REGIME_UP}'. 그 밖은 '{REGIME_FLAT}', 60거래일 시세가 없거나 판단 유보면 "
               f"'{REGIME_HOLD}'. 분석 기간과 관계없이 같은 규칙이며 위에서부터 첫 일치")


# ---------- 질문 판별 ----------

_FORECAST_RE = re.compile(
    r"오를까|오를지|오르려나|오르나요|오를\s*(?:것|거)\s*같|오를\s*수\s*있을|올라갈까|올라갈지|오른다고|"
    r"내릴까|내릴지|내려갈까|내려갈지|내린다고|떨어질까|떨어질지|빠질까|빠질지|하락할까|상승할까|반등할까|"
    r"전망|앞으로|향후|어떻게\s*될|어떨\s*(?:것|거)\s*같|어떨까|방향|"
    # 2026-11-07(35차): "지금 사야 해?", "팔아야 돼?"가 빠져 LLM이 관련 없는 뉴스를 나열했다(live lv_07). 매수·매도 판단 질문은 전망 경로다
    r"사도\s*(?:될|돼|되|괜찮)|살까|사야\s*(?:해|할|하|돼|되|될)|매수해도|매수할까|매수하는\s*게|매수해야\s*(?:해|할|하|돼|되|될)|"
    r"들어가도|팔까|팔아야\s*(?:해|할|하|돼|되|될)|매도해야\s*(?:해|할|하|돼|되|될)|"
    r"매도할까|매도해도|위험할까|위험한가|위험해\?|"
    # 2026-11-04(b18_16): "오르냐 내리냐", "떨어지냐"가 빠져 후속 질문이 LLM 경로로 갔다
    r"오르냐|내리냐|오르니|내리니|올라가냐|내려가냐|떨어지냐|떨어지니"
)
# 직전 종목을 이어받는 결론·판단·확률·위험 표현. "그래서/그럼"만으로는 후속 결론이 아니다("그럼 최근 뉴스는?").
_VERDICT_RE = re.compile(
    r"결론|한마디로|요약|정리하면|정리해|올라|내려|오른다는|내린다는|오를\s*거|내릴\s*거|상승이야|하락이야|상승이냐|하락이냐|"
    r"생각에|생각은|판단으로|판단은|어떻게\s*해야|어떻게\s*하라|어떡해|사\s*\?*\s*말아|살까\s*말까|어느\s*쪽|"
    r"가능성|확률|위험|리스크|오르냐|내리냐|오르니|내리니|올라가냐|내려가냐|떨어지냐|떨어지니|"
    # 2026-11-04(24차): "굳이 하나만 고르면", "어느 쪽이 더 강해", "살 만하다는 거야"도 후속 결론이다
    r"하나만|하나를|골라|고르면|고르라면|고르자면|찍어|둘\s*중|뭐가\s*더|더\s*(?:강해|세|센|커|크|큰|높아|높)|살\s*만|사도\s*된|사라는|"
    # 2026-11-04(25차): "상승 추세인데 다음 주 조정받을까?", "반등할 가능성은?", "선택해 봐", "방향은 중립이야 상승이야?"
    r"조정\s*(?:받|올|오|들어)|반등\s*(?:할|가능)|선택해|방향은|방향이"
)
_RISK_RE = re.compile(r"위험|리스크|손실|낙폭")
_PROBABILITY_RE = re.compile(r"가능성|확률|몇\s*(?:%|퍼센트|프로)")
_PICK_RE = re.compile(r"하나만|하나를|골라|고르면|고르라면|고르자면|찍어|둘\s*중")
_COMPARE_RE = re.compile(r"어느\s*쪽이\s*더|뭐가\s*더|더\s*(?:강해|세|센|커|크|큰)")
_BUY_RE = re.compile(r"살\s*만|사도\s*(?:돼|되|된|될|괜찮)|사라는|사야\s*(?:돼|되|하나|할까)|매수해도|팔아야|매도해도|팔라는|"
                     r"(?:지금|오늘|이번\s*주)\s*사면|사면\s*(?:오를|돼|될|괜찮|이익)")
# 25차: 직전 분석에 이어 "왜?"(판단 이유), "한 줄로만"(한 줄 답)
_WHY_RE = re.compile(r"^\s*(?:그래서|그럼|근데)?\s*왜\s*[?？.]*\s*$|왜\s*(?:그렇게|상승|하락|중립)|(?:판단|그렇게\s*본)\s*(?:이유|근거)|"
                     r"(?:이유|근거)(?:가|는)?\s*(?:뭐|뭔|뭡)|쪽이라고\s*(?:판단|본|했)")
_ONELINE_RE = re.compile(r"한\s*줄")
# 직전 분석에 이어 실적·시세 데이터를 묻는 후속 질문(2026-11-04). "실적"은 _OTHER_INTENT_RE라 따로 본다.
_EARNINGS_FOLLOWUP_RE = re.compile(r"실적.{0,20}(?:반영|영향|판단|달라|바뀌|전망)|(?:반영|영향).{0,10}실적")
_DATA_FOLLOWUP_RE = re.compile(
    r"(?:주가|시세|가격|데이터).{0,12}(?:없으면|없을\s*때|없는\s*경우|늦으면|늦은|오래|최신|반영된|기준일)|기준일이\s*언제|언제\s*(?:기준|데이터)")
# 26차(2026-11-04 밤): 간결 전망과 상세 리서치. "자세히", "전체", "리포트", "지표까지", "뉴스와 공시까지 모두 반영" 같은 말이 있으면 상세다.
# 상세 요청은 "뉴스·공시·실적"이 들어 있어도(_OTHER_INTENT_RE) 전망 경로다. "분석해줘"는 상세로 본다(지시의 상세 예시: "자세히 분석해 줘").
_STRONG_DETAIL_RE = re.compile(r"자세히|자세하게|상세|전체|리포트|보고서|기술적\s*지표|지표까지|모두\s*반영|포함해|포함한|까지\s*(?:반영|포함|분석|확인)|심층")
_DETAIL_RE = re.compile(_STRONG_DETAIL_RE.pattern + r"|분석")
_ANALYSIS_RE = re.compile(r"전망|분석|리포트|보고서|기술적\s*지표|지표까지|방향")
# 직전 분석에 이어 상세를 달라는 후속 질문("전체적으로 자세히 분석해 줘", "기술적 지표까지 확인해줘"). "실적까지 반영해서 다시 판단"은 실적 후속이다.
_DETAIL_FOLLOWUP_RE = re.compile(r"자세히|자세하게|상세|전체|리포트|보고서|기술적\s*지표|지표까지|심층|분석\s*해")
_FILING_RE = re.compile(r"(?:실적|공시).{0,15}(?:언제|날짜|며칠|접수일|발표일|접수\s*됐|접수\s*되)|(?:언제|날짜).{0,10}(?:공시|발표|접수)")
_SIGNAL_TERMS = (("cross", re.compile(r"골든\s*크로스|데드\s*크로스")), ("macd", re.compile(r"MACD|맥디", re.I)),
                 ("rsi", re.compile(r"RSI", re.I)), ("volume", re.compile(r"거래량")), ("relative", re.compile(r"지수\s*대비|코스피\s*대비")),
                 ("ma", re.compile(r"이동\s*평균|이평선|\d+\s*일선")))
_MEANING_RE = re.compile(r"뜻|의미|뭐야|뭔데|뭐지|무슨|어떤\s*거|설명")
_QUARTER_RE = re.compile(r"(?:(20\d{2})\s*년\s*)?([1-4])\s*분기")
# 주문 요청은 이 경로가 아니다. "1주일", "2주 뒤"는 기간이다.
_ORDER_RE = re.compile(
    r"사\s*줘|사주세요|사\s*줄래|매수\s*해\s*줘|매도\s*해\s*줘|팔아\s*줘|팔아주세요|주문|체결|실행\s*해|넣어\s*줘|담아\s*줘|"
    r"\d+\s*주(?!일|간|차|가|년|기|\s*(?:뒤|후|안|내|동안|만에|째|전))"
)
# 원인·실적·뉴스·리포트·용어를 묻는 질문은 기존 흐름이다
_OTHER_INTENT_RE = re.compile(r"왜|이유|원인|실적|매출|영업이익|리포트|증권사|애널리스트|컨센서스|뉴스|공시|기사|배당|뜻|정의|개념")
# 직전 종목 없이 결론·방향만 묻는 질문이라도 시장·지수·환율·내 포트폴리오를 묻는 말이면 기존 흐름이다
_MARKET_WORD_RE = re.compile(r"코스피|코스닥|시장|지수|환율|금리|달러|증시|포트폴리오|보유|종목|업종|섹터|테마|추천")
_PERIOD_PATTERNS = (
    # "2분기", "3분기"(실적 분기)는 기간이 아니다(2026-11-04 dev21_05: "2분기 실적은 전망에 어떤 영향이야?"가 3개월로 바뀌었다)
    ("3개월", re.compile(r"3\s*개월|석\s*달|세\s*달|(?<![1-4])(?<![1-4] )분기|90\s*일")),
    ("1개월", re.compile(r"1\s*개월|한\s*달|다음\s*달|이번\s*달|한달|30\s*일|4\s*주")),
    ("1주일", re.compile(r"일주일|1\s*주일|한\s*주|다음\s*주|이번\s*주|1\s*주|7\s*일|며칠")),
)
# 이 경로가 만든 답변의 표식. 후속 질문이 기간(과 종목)을 이어받는다. 전체 분석의 "신호 판정 상세" 줄
# "- 이름(종목코드), 분석 기간 1주일(5거래일): 라벨"과, 맨 앞 제목 줄 "이름(종목코드) · 향후 5거래일"(2026-11-04, 후속 답에는 이것만 있다).
_MARKER_RE = re.compile(r"^- (?P<name>.+?)\((?P<ticker>[0-9A-Z]{6})\), 분석 기간 (?P<period>1주일|1개월|3개월)\(", re.MULTILINE)
_TITLE_RE = re.compile(r"^(?P<name>.+?)\((?P<ticker>[0-9A-Z]{6})\) · 향후 (?P<days>\d+)거래일", re.MULTILINE)


# 채팅에서 주문을 실행해 달라는 요청(2026-11-03). 채팅에는 주문 경로가 없다(주문은 POST /api/portfolio/orders, 엄격 매칭).
# _ORDER_RE(전망에서 빼는 넓은 규칙)보다 좁다: "주문 방법", "체결강도가 뭐야" 같은 질문은 여기 걸리지 않는다.
_ORDER_COMMAND_RE = re.compile(
    r"사\s*줘|사주세요|사\s*줄래|매수\s*해\s*줘|매수해주세요|매도\s*해\s*줘|매도해주세요|팔아\s*줘|팔아주세요|"
    r"(?:매수|매도)\s*주문\s*(?:넣어|해|좀|부탁)|주문\s*(?:넣어|해\s*줘)|(?:매수|매도)\s*실행|시장가로|지정가로|"
    r"\d+\s*주\s*(?:만\s*)?(?:사|매수|팔|매도)(?!\s*(?:도|면|려면|는|기|ㄹ))"
)
ORDER_GUIDE_ANSWER = ("채팅에서는 주문을 실행하지 않습니다. 모의투자 화면에서 종목과 수량을 확인한 뒤 주문할 수 있어요.")


# 주문 방법을 묻는 질문(dev17_08: LLM이 "주문이 접수/체결되면" 같은 절차를 지어냈다). 같은 고정 안내로 답한다.
_ORDER_HOWTO_RE = re.compile(
    r"주문(?:은|을|하는\s*건|하려면)?\s*(?:어떻게|방법)|어떻게\s*주문|(?:매수|매도)(?:는|하는\s*건|하려면)?\s*(?:어떻게|방법)|"
    r"어떻게\s*(?:사|팔)(?:나요|나|야|지|요)"
)


def is_order_command(question: str) -> bool:
    """주문 실행을 요청하는 문장이거나 주문 방법을 묻는 문장인가. 전망 질문("사도 될까?")은 아니다."""
    q = question or ""
    if _ORDER_HOWTO_RE.search(q) and not is_forecast_question(q):
        return True
    return bool(_ORDER_COMMAND_RE.search(q)) and not is_forecast_question(q.replace("사줘", "").replace("팔아줘", ""))


def is_order_request(question: str) -> bool:
    return bool(_ORDER_RE.search(question or ""))


def _excluded(q: str) -> bool:
    return is_order_request(q) or bool(_OTHER_INTENT_RE.search(q))


def wants_detail(question: str) -> bool:
    """상세 리서치 요청인가(26차). 아니면 간결 전망이다."""
    return bool(_DETAIL_RE.search(question or ""))


def is_forecast_question(question: str) -> bool:
    """전망 질문. 상세 요청("뉴스와 공시까지 모두 반영해서 전망 분석해줘", "기술적 지표까지 분석해줘")은 뉴스·공시·실적 단어가 있어도
    전망이다(26차). 주문 요청은 아니다."""
    q = question or ""
    if is_order_request(q):
        return False
    strong = bool(_STRONG_DETAIL_RE.search(q))
    if _FORECAST_RE.search(q):
        return not _excluded(q) or strong
    return strong and bool(_ANALYSIS_RE.search(q)) and not _OTHER_INTENT_RE.search(_STRONG_DETAIL_RE.sub("", q).replace("분석", ""))


def is_followup_verdict_question(question: str) -> bool:
    q = question or ""
    return bool(_VERDICT_RE.search(q)) and not _excluded(q)


def followup_kind(question: str) -> str:
    """후속 결론 질문의 종류. 확률 -> 상승·하락 비교 -> 고르기 -> 매수 가부 -> 위험 -> 판단(나머지) 순서로 본다."""
    q = question or ""
    if _PROBABILITY_RE.search(q):
        return "probability"
    if _COMPARE_RE.search(q):
        return "compare"
    if _PICK_RE.search(q):
        return "pick"
    if _BUY_RE.search(q):
        return "buy"
    if _RISK_RE.search(q):
        return "risk"
    return "verdict"


def special_followup_kind(question: str) -> str | None:
    """직전 분석(표식 있는 답변)에 이어 한 줄 답("oneline"), 판단 이유("why"), 공시 날짜("filing"), 신호 의미("signal"),
    시세 데이터 기준("data"), 실적 반영("earnings"), 상세 리서치("detail")를 묻는가. 아니면 None.
    "왜"·"실적"·"공시"는 기존 흐름 단어(_OTHER_INTENT_RE)라 표식이 있을 때만 이 경로다."""
    q = question or ""
    if is_order_request(q):
        return None
    if _ONELINE_RE.search(q):
        return "oneline"
    if _WHY_RE.search(q):
        return "why"
    if _FILING_RE.search(q):
        return "filing"
    if signal_term(q):
        return "signal"
    if _DATA_FOLLOWUP_RE.search(q):
        return "data"
    if _EARNINGS_FOLLOWUP_RE.search(q):
        return "earnings"
    if _DETAIL_FOLLOWUP_RE.search(q):
        return "detail"
    return None


def signal_term(question: str) -> tuple[str, str] | None:
    """"골든크로스가 무슨 뜻이야?" -> (신호 종류, 질문에 쓴 말). 뜻·의미를 묻지 않으면 None."""
    q = question or ""
    if not _MEANING_RE.search(q):
        return None
    for kind, rx in _SIGNAL_TERMS:
        m = rx.search(q)
        if m:
            return kind, m.group(0)
    return None


def quarter_in(question: str, now: datetime) -> tuple[int, int] | None:
    """질문의 "3분기", "2025년 4분기" -> (연도, 분기). 연도가 없으면 지금 기준으로 이미 끝난 가장 최근 그 분기
    (11월에 "3분기"는 올해, "4분기"는 아직 끝나지 않았으니 지난해)."""
    m = _QUARTER_RE.search(question or "")
    if not m:
        return None
    q = int(m.group(2))
    if m.group(1):
        return int(m.group(1)), q
    today = now.astimezone(KST).date()
    return (today.year if q < (today.month - 1) // 3 + 1 else today.year - 1), q


def asks_subject(question: str) -> bool:
    """종목 없이 결론·방향만 묻는 질문인가("그래서 결론이 뭐야?"). 시장·지수·내 포트폴리오 질문은 아니다."""
    q = question or ""
    return (is_forecast_question(q) or is_followup_verdict_question(q)) and not _MARKET_WORD_RE.search(q)


def period_in(question: str) -> str | None:
    for label, rx in _PERIOD_PATTERNS:
        if rx.search(question or ""):
            return label
    return None


# ---------- 전망 기간을 숫자로 말한 질문 (49차, 2026-11-13) ----------
# 수정 전: "향후 20거래일 전망"은 period_in이 기간을 못 찾아 기본 1주일(5거래일)로 답했다(요청한 기간 숫자가 바뀜).
# "향후 1년 전망"도 말없이 1주일로 답했다. 이제 숫자로 말한 미래 기간을 읽는다:
# - 5·20·60거래일(또는 1주·4주·1개월·3개월처럼 같은 거래일 수)이면 그 기간
# - 그 밖에 90거래일 이하이면 가장 가까운 지원 기간으로 분석하고, 답 맨 앞에 바꾼 사실을 밝힌다(horizon_note)
# - 90거래일을 넘거나 연 단위·"내년"이면 전망을 하지 않고 지원 기간을 안내한다(도구를 부르지 않는다)
_HORIZON_UNIT_DAYS = {"거래일": 1, "영업일": 1, "주": 5, "개월": 20, "달": 20, "년": 250}
_HORIZON_RE = re.compile(
    r"(?:향후|앞으로|다음|이후)\s*(\d+)\s*(거래일|영업일|일|주|개월|달|년)(?:\s*(?:동안|간))?"
    r"|(?<![\d])(\d+)\s*(거래일|영업일|일|주|개월|달|년)\s*(?:뒤|후|이후)"
)
_HORIZON_YEAR_RE = re.compile(r"내년|(?:향후|앞으로)\s*(?:한|1)\s*년|(?:향후|앞으로)\s*몇\s*년|장기\s*전망")
SUPPORTED_HORIZONS = {5: "1주일", 20: "1개월", 60: "3개월"}
MAX_HORIZON_DAYS = 90


@dataclass(frozen=True)
class Horizon:
    text: str  # 질문 속 표현
    requested_days: int | None  # 거래일 환산(연 단위는 None일 수 있다)
    label: str | None  # 분석할 지원 기간(1주일/1개월/3개월), 지원 밖이면 None
    note: bool  # 요청과 다른 기간으로 분석했다(답 맨 앞에 밝힌다)

    @property
    def unsupported(self) -> bool:
        return self.label is None


def horizon_in(question: str) -> Horizon | None:
    """질문이 숫자로 말한 미래 기간. 없으면 None(기존 period_in·기본 기간을 쓴다)."""
    q = question or ""
    y = _HORIZON_YEAR_RE.search(q)
    if y:
        return Horizon(y.group(0), None, None, False)
    m = _HORIZON_RE.search(q)
    if not m:
        return None
    n, unit = (int(m.group(1)), m.group(2)) if m.group(1) else (int(m.group(3)), m.group(4))
    days = max(1, round(n * 5 / 7)) if unit == "일" else n * _HORIZON_UNIT_DAYS[unit]
    if days in SUPPORTED_HORIZONS:
        return Horizon(m.group(0), days, SUPPORTED_HORIZONS[days], False)
    if days > MAX_HORIZON_DAYS:
        return Horizon(m.group(0), days, None, False)
    nearest = min(SUPPORTED_HORIZONS, key=lambda d: (abs(d - days), d))
    return Horizon(m.group(0), days, SUPPORTED_HORIZONS[nearest], True)


def horizon_note(h: Horizon) -> str:
    used = PERIODS[h.label].days
    return (f"요청하신 기간({h.text.strip()}, 약 {h.requested_days}거래일)은 지원하는 전망 기간(향후 5·20·60거래일)이 아니어서 "
            f"가장 가까운 향후 {used}거래일({h.label}) 기준으로 분석했습니다.")


def unsupported_horizon_answer(h: Horizon) -> str:
    return (f"전망은 향후 5·20·60거래일(1주일·1개월·3개월) 기준으로만 제공합니다. 요청하신 기간({h.text.strip()})의 전망은 제공하지 않습니다"
            "(지표 계산과 과거 검증이 이 세 기간 기준입니다). 기간을 바꿔 다시 질문해 주세요.\n\n" + DISCLAIMER)


def marker_of(answer: str | None) -> dict | None:
    """이 경로가 만든 답변이면 {"name", "ticker", "period"}, 아니면 None."""
    m = _MARKER_RE.search(answer or "")
    if m:
        return m.groupdict()
    t = _TITLE_RE.search(answer or "")
    period = next((p.label for p in PERIODS.values() if t and str(p.days) == t.group("days")), None)
    return {"name": t.group("name"), "ticker": t.group("ticker"), "period": period} if period else None


# ---------- 지표 계산(순수 함수) ----------


def _pct(new: float, old: float) -> float | None:
    return round((new / old - 1) * 100, 2) if old else None


def rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """최근 period개 종가 변화의 단순평균 RSI = 상승폭 평균 ÷ (상승폭 평균 + 하락폭 평균) × 100. 자료가 모자라거나 변화가 전부 0이면 None."""
    if len(closes) < period + 1:
        return None
    diffs = [b - a for a, b in zip(closes[-period - 1:-1], closes[-period:])]
    gain = sum(d for d in diffs if d > 0) / period
    loss = sum(-d for d in diffs if d < 0) / period
    if gain + loss == 0:
        return None
    return round(gain / (gain + loss) * 100, 2)


def ema(values: list[float], n: int) -> list[float]:
    """지수이동평균(평활 계수 2/(n+1), 첫 값으로 시작)."""
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd(closes: list[float]) -> dict | None:
    """MACD(12,26,9): MACD = EMA12 - EMA26, 시그널 = MACD의 EMA9. 종가가 26+9개 미만이면 None."""
    if len(closes) < MACD_SLOW + MACD_SIGNAL:
        return None
    line = [f - s for f, s in zip(ema(closes, MACD_FAST), ema(closes, MACD_SLOW))]
    signal = ema(line, MACD_SIGNAL)
    return {"macd": round(line[-1], 2), "signal": round(signal[-1], 2), "hist": round(line[-1] - signal[-1], 2)}


def volatility(closes: list[float], days: int = VOLATILITY_DAYS) -> float | None:
    """최근 days거래일 일간 수익률(%)의 표본표준편차. 종가가 days+1개 미만이면 None."""
    if len(closes) < days + 1:
        return None
    rets = [(b / a - 1) * 100 for a, b in zip(closes[-days - 1:-1], closes[-days:])]
    mean = sum(rets) / days
    return round(math.sqrt(sum((r - mean) ** 2 for r in rets) / (days - 1)), 2)


def last_cross(days: list[date], closes: list[float], short: int, long: int, lookback: int) -> dict | None:
    """최근 lookback 거래일 안에서 단기선·장기선의 위아래가 바뀐 마지막 날(app.backtest.sma_target 판정). 없으면 None."""
    found = None
    for i in range(max(1, len(closes) - lookback), len(closes)):
        before, now = sma_target(closes[:i], short, long), sma_target(closes[: i + 1], short, long)
        if before is not None and now is not None and before != now:
            found = {"date": days[i].isoformat(), "kind": "golden" if now else "dead"}
    return found


def compute_indicators(rows: list[tuple], period: Period, index_rows: list[tuple] | None = None) -> dict | None:
    """rows: 날짜 오름차순 [(date, close, volume)], index_rows: 같은 기간 지수 [(date, close)]. 행이 2개 미만이면 None."""
    if len(rows) < 2:
        return None
    days = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    volumes = [r[2] for r in rows]
    close = closes[-1]
    out: dict = {"base_date": days[-1].isoformat(), "close": close, "n_rows": len(rows), "first_date": days[0].isoformat(),
                 "prev_date": days[-2].isoformat(), "day_change_pct": _pct(close, closes[-2]), "returns": {}}
    for n in sorted(set(period.returns) | {period.days}):
        if len(rows) > n:
            out["returns"][n] = {"pct": _pct(close, closes[-1 - n]), "from_date": days[-1 - n].isoformat(),
                                 "from_close": closes[-1 - n]}
    short, long = period.ma
    for n in (short, long):
        value = sma(closes, n)
        if value is not None:
            out[f"ma{n}"] = {"value": round(value, 2), "gap_pct": _pct(close, value)}
    if f"ma{long}" in out:
        out["ma_short_above_long"] = sma_target(closes, short, long)
        out["cross"] = last_cross(days, closes, short, long, period.days)
    out["rsi"] = rsi(closes)
    out["macd"] = macd(closes)
    out["volatility"] = volatility(closes)
    # 최고·최저 종가(2026-11-11 42차): "최근 N거래일"은 기준일을 포함한 최근 N개 종가다(app/price_flow·stock_tool.summarize_period와
    # 같은 정의). 전에는 N+1개(수익률의 비교 기준일 종가까지)에서 골라, 같은 "최근 20거래일"인데 주가 흐름 답과 최고·최저가 달랐다
    # (SK하이닉스 2026-11-03: 전망 1,842,000원(10-02, 21번째 종가) vs 주가 흐름 1,784,000원(10-06)). 비교 기준일은 수익률에만 쓴다.
    window = list(zip(days, closes))[-period.range_days:]
    hi_d, hi = max(window, key=lambda x: (x[1], x[0]))
    lo_d, lo = min(window, key=lambda x: (x[1], -x[0].toordinal()))
    out["range"] = {"days": len(window), "from_date": window[0][0].isoformat(), "to_date": window[-1][0].isoformat(),
                    "high": hi, "high_date": hi_d.isoformat(), "from_high_pct": _pct(close, hi),
                    "low": lo, "low_date": lo_d.isoformat(), "from_low_pct": _pct(close, lo)}
    prior = [v for v in volumes[-1 - VOLUME_AVG_DAYS:-1] if v is not None]
    if volumes[-1] is not None and len(prior) == VOLUME_AVG_DAYS and sum(prior) > 0:
        avg = sum(prior) / VOLUME_AVG_DAYS
        out["volume"] = {"value": int(volumes[-1]), "avg": round(avg), "ratio": round(volumes[-1] / avg, 2)}
    # 52주 기준(기준일을 포함한 최근 RANGE52_DAYS개 종가, 42차에 range와 같은 정의로). 분석 기간 기준(range)과 이름·조회 기간을
    # 따로 붙여 보인다(2026-11-04).
    w52 = list(zip(days, closes))[-RANGE52_DAYS:]
    h52_d, h52 = max(w52, key=lambda x: (x[1], x[0]))
    l52_d, l52 = min(w52, key=lambda x: (x[1], -x[0].toordinal()))
    out["range52"] = {"days": len(w52), "from_date": w52[0][0].isoformat(), "to_date": w52[-1][0].isoformat(),
                      "high": h52, "high_date": h52_d.isoformat(), "from_high_pct": _pct(close, h52),
                      "low": l52, "low_date": l52_d.isoformat(), "from_low_pct": _pct(close, l52)}
    ri = {}
    for n in (5, 20, 60):
        ma = sma(closes, n)
        ri[f"ma{n}"] = round(ma, 2) if ma is not None else None
        ri[f"r{n}"] = _pct(close, closes[-1 - n]) if len(closes) > n else None
        ri[f"r{n}_from"] = (days[-1 - n].isoformat(), closes[-1 - n]) if len(closes) > n else None
    low20 = min(closes[-20:])  # 최근 20거래일(기준일 포함 20개 종가) 최저, 42차에 range와 같은 정의로
    ri["from_low20_pct"] = _pct(close, low20)
    out["regime_inputs"] = ri
    if index_rows:
        cmp = compare_returns(list(zip(days, closes)), [(d, float(c)) for d, c in index_rows], period.days)
        if cmp is not None and cmp["end_date"] == out["base_date"]:
            out["relative"] = cmp
        else:
            # 2026-11-03: 수집 도중(종목 시세는 들어왔고 지수는 아직)에는 끝 날짜가 달라 비교하지 않는다. 그 날짜를 밝힌다.
            out["relative_missing"] = {"index_last": max(d for d, _ in index_rows).isoformat()}
    return out


def classify_regime(ind: dict, label: str | None = None) -> str:
    """국면(코드 규칙, API.md "국면 규칙"). 위에서부터 첫 일치. 분석 기간과 관계없이 같은 입력을 본다.
    1) 판단 유보 라벨이거나 60일선·60거래일 수익률을 계산할 수 없으면 판단 유보
    2) 큰 하락 뒤(20거래일 <= -10% 또는 60거래일 <= -15%) + 반등 신호(5거래일 > 0, 5일선 > 20일선, 20거래일 최저 종가 대비 +5% 이상 중 하나)
       + 종가가 20일선이나 60일선 아래 -> 단기 반등 시도, 추세 전환은 미확인
    3) 종가 < 20일선 < 60일선이고 (20거래일 <= -5% 또는 60거래일 <= -10%) -> 하락 추세 지속
    4) 60거래일 >= +10%, 종가 > 60일선, 20일선 > 60일선이고 (종가 < 20일선 또는 5거래일 <= -3%) -> 상승 추세 중 조정
    5) 종가 > 20일선 > 60일선이고 (20거래일 >= +5% 또는 60거래일 >= +10%) -> 상승 추세 지속
    6) 그 밖 -> 횡보, 방향 불분명"""
    ri = ind.get("regime_inputs") or {}
    close = ind["close"]
    need = ("r5", "r20", "r60", "ma5", "ma20", "ma60")
    if label == LABEL_HOLD or any(ri.get(k) is None for k in need):
        return REGIME_HOLD
    r5, r20, r60, ma5, ma20, ma60 = (ri[k] for k in need)
    big_drop = r20 <= REGIME_DROP20_PCT or r60 <= REGIME_DROP60_PCT
    rebound_sign = r5 > 0 or ma5 > ma20 or ri["from_low20_pct"] >= REGIME_REBOUND_FROM_LOW_PCT
    if big_drop and rebound_sign and (close < ma20 or close < ma60):
        return REGIME_REBOUND
    if close < ma20 < ma60 and (r20 <= -REGIME_TREND20_PCT or r60 <= -REGIME_TREND60_PCT):
        return REGIME_DOWN
    if r60 >= REGIME_TREND60_PCT and close > ma60 and ma20 > ma60 and (close < ma20 or r5 <= REGIME_DIP5_PCT):
        return REGIME_PULLBACK
    if close > ma20 > ma60 and (r20 >= REGIME_TREND20_PCT or r60 >= REGIME_TREND60_PCT):
        return REGIME_UP
    return REGIME_FLAT


def _won(v: float) -> str:
    return f"{v:,.0f}원" if float(v).is_integer() else f"{v:,.2f}원"


def _signed(v: float) -> str:
    return f"{v:+.2f}%"


def classify_signals(ind: dict, period: Period) -> list[dict]:
    """지표마다 {key, side(up/down/neutral), name, rule, value}. 계산하지 못한 지표는 빠진다. 같은 입력이면 항상 같은 결과다."""
    out = []
    close = ind["close"]

    def add(key, side, name, rule, value, kind=None):
        kind = kind or key
        out.append({"key": key, "kind": kind, "group": SIGNAL_GROUPS[kind], "side": side, "name": name, "rule": rule,
                    "value": value})

    short, long = period.ma
    for n in (short, long):
        ma = ind.get(f"ma{n}")
        if ma:
            side = UP if close > ma["value"] else DOWN if close < ma["value"] else NEUTRAL
            name = {UP: f"종가가 {n}일 이동평균 위", DOWN: f"종가가 {n}일 이동평균 아래", NEUTRAL: f"종가가 {n}일 이동평균과 같음"}[side]
            add(f"close_vs_ma{n}", side, name, f"종가 > {n}일 이동평균이면 상승 쪽, < 이면 하락 쪽",
                f"종가 {_won(close)}, {n}일 이동평균 {_won(round(ma['value']))}",
                kind="close_vs_ma_short" if n == short else "close_vs_ma_long")
    if ind.get("ma_short_above_long") is not None:
        above = ind["ma_short_above_long"]
        add("ma_alignment", UP if above else DOWN, f"{short}일선이 {long}일선 {'위' if above else '아래'}",
            f"{short}일 이동평균 > {long}일 이동평균이면 상승 쪽, 아니면 하락 쪽",
            f"{short}일 {_won(round(ind[f'ma{short}']['value']))}, {long}일 {_won(round(ind[f'ma{long}']['value']))}")
    if "cross" in ind:
        c = ind["cross"]
        side = NEUTRAL if c is None else (UP if c["kind"] == "golden" else DOWN)
        name = (f"최근 {period.days}거래일 안에 교차 없음" if c is None else
                f"{c['date']} {'골든크로스' if c['kind'] == 'golden' else '데드크로스'}")
        add("cross", side, name, f"최근 {period.days}거래일 안에 {short}일선이 {long}일선을 위로 교차하면 상승 쪽, 아래로 교차하면 하락 쪽",
            "교차 없음" if c is None else f"{c['date']}")
    r = ind["returns"].get(period.days)
    if r:
        side = UP if r["pct"] > RETURN_FLAT_PCT else DOWN if r["pct"] < -RETURN_FLAT_PCT else NEUTRAL
        add("period_return", side, f"{period.days}거래일 수익률 {_signed(r['pct'])}",
            f"{period.days}거래일 수익률이 +{RETURN_FLAT_PCT:.0f}% 초과면 상승 쪽, -{RETURN_FLAT_PCT:.0f}% 미만이면 하락 쪽",
            f"{r['from_date']} 종가 {_won(r['from_close'])} 대비")
    if ind.get("rsi") is not None:
        v = ind["rsi"]
        side = DOWN if v >= RSI_OVERBOUGHT else UP if v <= RSI_OVERSOLD else NEUTRAL
        name = {DOWN: "RSI 과열 구간", UP: "RSI 과매도 구간", NEUTRAL: "RSI 중간 구간"}[side]
        add("rsi", side, name, f"RSI(14) ≥ {RSI_OVERBOUGHT:.0f}이면 하락 쪽(과열), ≤ {RSI_OVERSOLD:.0f}이면 상승 쪽(과매도)", f"RSI(14) {v:.2f}")
    if ind.get("macd"):
        m = ind["macd"]
        side = UP if m["macd"] > m["signal"] else DOWN if m["macd"] < m["signal"] else NEUTRAL
        add("macd", side, f"MACD가 시그널 {'위' if side == UP else '아래' if side == DOWN else '와 같음'}",
            "MACD(12,26,9) > 시그널이면 상승 쪽, < 이면 하락 쪽", f"MACD {m['macd']:,.2f}, 시그널 {m['signal']:,.2f}")
    if ind.get("volume") and ind.get("day_change_pct") is not None:
        v, day = ind["volume"], ind["day_change_pct"]
        side = NEUTRAL if v["ratio"] < VOLUME_SURGE or day == 0 else (UP if day > 0 else DOWN)
        name = {UP: "거래량을 동반한 상승", DOWN: "거래량을 동반한 하락", NEUTRAL: "거래량 특이점 없음"}[side]
        add("volume", side, name, f"거래량이 직전 {VOLUME_AVG_DAYS}거래일 평균의 {VOLUME_SURGE}배 이상이고 기준일 상승이면 상승 쪽, 하락이면 하락 쪽",
            f"평균의 {v['ratio']:.2f}배, 기준일 등락 {_signed(day)}")
    rel = ind.get("relative")
    if rel:
        d = rel["diff_pct_point"]
        side = UP if d >= RELATIVE_PP else DOWN if d <= -RELATIVE_PP else NEUTRAL
        name = {UP: "지수보다 강한 흐름", DOWN: "지수보다 약한 흐름", NEUTRAL: "지수와 비슷한 흐름"}[side]
        add("relative", side, name, f"{period.days}거래일 수익률이 지수보다 {RELATIVE_PP:.0f}%p 이상 높으면 상승 쪽, 낮으면 하락 쪽",
            f"차이 {d:+.2f}%p")
    return out


def group_votes(signals: list[dict], groups: dict[str, str] | None = None) -> dict[str, str]:
    """묶음별 1표: 묶음 안 상승 쪽이 많으면 up, 하락 쪽이 많으면 down, 같으면 neutral. 계산한 지표가 없는 묶음은 빠진다.
    groups(지표 종류 -> 묶음)를 주면 그 묶음으로 센다(백테스트의 묶음 비교용). 기본은 SIGNAL_GROUPS."""
    groups = groups or SIGNAL_GROUPS
    tally: dict[str, int] = {}
    for s in signals:
        g = groups[s["kind"]]
        skip = counted_elsewhere(s, signals, groups)
        tally[g] = tally.get(g, 0) + (0 if skip else 1 if s["side"] == UP else -1 if s["side"] == DOWN else 0)
    return {g: UP if v > 0 else DOWN if v < 0 else NEUTRAL for g, v in tally.items()}


def counted_elsewhere(signal: dict, signals: list[dict], groups: dict[str, str] | None = None) -> bool:
    """같은 묶음에서 같은 사실을 나타내는 다른 지표가 이미 세어지는가(SAME_FACT_AS). 교차는 이동평균 배열이 있으면 세지 않는다."""
    groups = groups or SIGNAL_GROUPS
    other = SAME_FACT_AS.get(signal["kind"])
    return (other is not None and groups.get(other) == groups[signal["kind"]]
            and any(s["kind"] == other for s in signals))


def judge(signals: list[dict], data: dict, groups: dict[str, str] | None = None, margin: int | None = None) -> str:
    """묶음당 1표로 판정한다(2026-11-03). 상승·하락 표 차이가 GROUP_MARGIN 이상이면 우위, 미만이면 중립.
    계산한 묶음이 MIN_GROUPS 미만이거나 데이터 점검이 유보(data["hold"])면 판단 유보."""
    votes = group_votes(signals, groups)
    margin = GROUP_MARGIN if margin is None else margin
    n_up = sum(v == UP for v in votes.values())
    n_down = sum(v == DOWN for v in votes.values())
    if len(votes) < MIN_GROUPS or data["hold"]:
        return LABEL_HOLD
    if n_up - n_down >= margin:
        return LABEL_UP
    if n_down - n_up >= margin:
        return LABEL_DOWN
    return LABEL_NEUTRAL


def judge_by_indicators(signals: list[dict], data: dict) -> str:
    """2026-11-03 이전 판정(지표마다 1표, 차이 2 이상, 지표 5개 미만이면 유보). 묶음 적용 전후 비교용으로만 남긴다."""
    n_up = sum(s["side"] == UP for s in signals)
    n_down = sum(s["side"] == DOWN for s in signals)
    if len(signals) < MIN_SIGNAL_INDICATORS or data["hold"]:
        return LABEL_HOLD
    if n_up - n_down >= SUMMARY_MARGIN:
        return LABEL_UP
    if n_down - n_up >= SUMMARY_MARGIN:
        return LABEL_DOWN
    return LABEL_NEUTRAL


def vote_tally(signals: list[dict]) -> dict:
    """묶음 표 집계: up/down/neutral/total(계산한 묶음 수)."""
    votes = group_votes(signals)
    up, down = sum(v == UP for v in votes.values()), sum(v == DOWN for v in votes.values())
    return {"up": up, "down": down, "neutral": len(votes) - up - down, "total": len(votes)}


def tally_text(t: dict) -> str:
    return f"{t['total']}개 묶음 중 상승 {t['up']}·하락 {t['down']}·중립 {t['neutral']}"


def strength_label(label: str, t: dict) -> str:
    """우세한 쪽 묶음이 계산한 묶음의 절반 미만이면 "약한 상승 우위"처럼 쓴다(2개만 상승이고 나머지가 중립인 경우)."""
    lead = t["up"] if label == LABEL_UP else t["down"] if label == LABEL_DOWN else None
    return f"약한 {label}" if lead is not None and t["total"] and lead * 2 < t["total"] else label


def direction_of(label: str) -> str:
    """분석 기간의 방향(DIRECTION_RULE). 국면을 보지 않는다."""
    return {LABEL_UP: DIR_UP, LABEL_DOWN: DIR_DOWN, LABEL_NEUTRAL: DIR_NEUTRAL}.get(label, DIR_HOLD)


def confidence_of(period: Period, label: str, t: dict, lag: int) -> str | None:
    """신뢰도(CONFIDENCE_RULE). 방향이 상승 쪽·하락 쪽이 아니면 None."""
    if label not in (LABEL_UP, LABEL_DOWN):
        return None
    r = _backtest_row(period, label)
    if r is None or not r.get("significant"):
        return CONF_LOW
    lead = t["up"] if label == LABEL_UP else t["down"]
    return CONF_MID if lead * 2 < t["total"] or lag >= 2 else CONF_HIGH


def _direction(res: dict, period: Period) -> tuple[str, str | None, dict]:
    t = vote_tally(res["signals"])
    return direction_of(res["label"]), confidence_of(period, res["label"], t, res["data"].get("lag_business_days") or 0), t


def direction_title(company, period: Period, res: dict) -> str:
    """제목 줄: "SK하이닉스(000660) · 향후 5거래일 · 방향: 상승 쪽(신뢰도 낮음)". 첫 줄에서 방향을 바로 읽게 한다."""
    d, conf, _ = _direction(res, period)
    return f"{title_line(company, period)} · 방향: {d}" + (f"(신뢰도 {conf})" if conf else "")


def _hold_reason(res: dict, t: dict) -> str:
    data = res["data"]
    if data.get("lag_business_days", 0) > MAX_LAG_BUSINESS_DAYS:
        return f"마지막 시세가 {data['lag_business_days']}영업일 지연"
    if data.get("missing_days"):
        return f"계산 구간 거래일 {data['missing_days']}일 시세 없음"
    return f"계산한 묶음 {t['total']}개로 {MIN_GROUPS}개 미만"


def direction_sentence(res: dict, regime: str, period: Period, pick: bool = False) -> str:
    """방향을 먼저 말하는 문장. 상승 쪽·하락 쪽이면 약한 신호·추세·신뢰도를 "다만"으로 붙이고, 중립·판단 유보는 그렇게 말한다.
    "중립인데 굳이 고르면 상승 쪽" 같은 표현은 쓰지 않는다(25차)."""
    d, conf, t = _direction(res, period)
    tally = tally_text(t)
    base = res["indicators"]["base_date"]
    when = f"({base} 종가 기준)" if (res["data"].get("lag_business_days") or 0) >= 1 else ""
    if d == DIR_HOLD:
        return (("하나만 고르라고 해도 " if pick else "") + f"데이터가 부족해 현재는 방향 판단을 보류하겠습니다"
                f"({_hold_reason(res, t)}, {tally}).")
    if d == DIR_NEUTRAL:
        gap = t["up"] - t["down"]
        more = (f", {'상승' if gap > 0 else '하락'} 쪽 묶음이 {abs(gap)}개 더 많지만 기준인 {GROUP_MARGIN}개 차이에 못 미칩니다"
                if gap else "")
        return (("하나만 고르라고 해도 " if pick else "") + f"현재는 상승과 하락 중 어느 쪽도 뚜렷하게 우세하지 않습니다"
                f"({tally}{more}). 추세는 {_REGIME_SHORT[regime]}입니다{when}.")
    lead = t["up"] if d == DIR_UP else t["down"]
    strength = f"신호가 약하고({tally})" if lead * 2 < t["total"] else f"묶음 비중은 {tally}이고"
    trend = (f"추세({_REGIME_SHORT[regime]})도 같은 방향이지만" if _REGIME_SIDE.get(regime) == d
             else f"추세는 아직 {_REGIME_SHORT[regime]} 단계이며")
    r = _backtest_row(period, res["label"])
    why_conf = ("과거 검증에서 이런 신호의 방향 예측력이 확인되지 않아 " if conf == CONF_LOW and r is not None
                else "과거 검증 결과가 없어 " if conf == CONF_LOW else "")
    conf_word = {CONF_LOW: "낮습니다", CONF_MID: "보통입니다", CONF_HIGH: "높습니다"}[conf]
    return (("하나만 고르면 " if pick else "") + f"{d}입니다. 다만 {strength}, {trend}, {why_conf}신뢰도는 {conf_word}{when}.")


def _power_stats(r: dict) -> str:
    lo, hi = r["diff_ci95"]
    return f"표본 {r['n']:,}, 차이 {r['diff'] * 100:+.1f}%p, 95% 구간 {lo * 100:+.1f}~{hi * 100:+.1f}%p"


def power_sentence(period: Period, label: str) -> str:
    """예측력(과거 검증): 표본 수·차이·95% 구간과 유의성. 유의하지 않으면 방향 예측력이 확인되지 않았다고 쓴다."""
    if label == LABEL_HOLD:
        return "판단 유보는 과거 검증 대상이 아니라 예측력을 말할 수 없습니다."
    r = _backtest_row(period, label)
    if r is None:
        return "과거 검증 결과가 아직 없어 예측력을 말할 수 없습니다."
    head = (f"과거 검증에서 이 신호 상태({label})의 {period.label} 뒤 상승 비율은 {r['up_rate'] * 100:.1f}%, "
            f"같은 기간 전체는 {r['base_rate'] * 100:.1f}%였습니다({_power_stats(r)}).")
    if r.get("significant"):
        return head + " 차이가 통계적으로 유의했지만 과거 빈도일 뿐 확률이나 예측이 아닙니다."
    return head + " 차이가 통계적으로 유의하지 않아 방향 예측력은 확인되지 않았습니다."


def power_short(period: Period, label: str) -> str:
    if label == LABEL_HOLD:
        return "판단 유보라 과거 검증 대상이 아닙니다"
    r = _backtest_row(period, label)
    if r is None:
        return "과거 검증 결과가 아직 없습니다"
    if r.get("significant"):
        return f"과거 검증에서 유의한 차이가 있었지만 확률이나 예측은 아닙니다({_power_stats(r)})"
    return f"과거 검증에서 방향 예측력은 확인되지 않았습니다({_power_stats(r)})"


def direction_headline(res: dict, regime: str, period: Period) -> str:
    """상세 답 첫 줄: 방향을 먼저, 짧게. 신호 비중·추세·과거 검증은 종합 판단에서."""
    d, conf, t = _direction(res, period)
    if d in (DIR_HOLD, DIR_NEUTRAL):
        return direction_sentence(res, regime, period)
    lead = t["up"] if d == DIR_UP else t["down"]
    weak = "신호가 약하고 " if lead * 2 < t["total"] else ""
    conf_word = {CONF_LOW: "낮습니다", CONF_MID: "보통입니다", CONF_HIGH: "높습니다"}[conf]
    return f"{d}입니다. 다만 {weak}신뢰도는 {conf_word}({tally_text(t)})."


def brief_answer(company, period: Period, res: dict, now: datetime) -> str:
    """간결 전망(26차): 제목(방향·신뢰도) -> 방향 문장 -> 상승 근거 최대 2 -> 하락 위험 최대 2 -> 과거 검증 -> 국면·데이터 경고 -> 안내.
    상세 답과 같은 계산(analyze)·같은 근거(group_evidence)를 쓴다. 지표 전체·뉴스·공시 목록은 넣지 않는다."""
    ind, label, data = res["indicators"], res["label"], res["data"]
    regime = classify_regime(ind, label)
    ge = group_evidence(ind, res["signals"], period)
    r = _backtest_row(period, label) if label != LABEL_HOLD else None
    power = (" 이 신호 조합의 방향 예측력은 확인되지 않았습니다." if r is not None and not r.get("significant") else "")
    warn = "".join(f" · 데이터 경고: {w}" for w in data["warnings"])
    lines = [direction_title(company, period, res), "", direction_sentence(res, regime, period),
             f"- 상승 근거: {_support_text(ge)}",
             f"- 하락 위험: {_risk_text(ge)}",
             _marker_line(company, period, label),  # 신호 판정 라벨(후속 질문·과거 검증 줄이 이 라벨을 쓴다)
             f"- 과거 검증: {backtest_line(period, label).removeprefix('- ')}{power}",
             f"- 시장 국면: {state_line(regime, ind)} · 기준일(최신 거래일): {ind['base_date']}, "
             f"답변 생성 {now.astimezone(KST):%Y-%m-%d %H:%M} KST{warn}",
             "- 과거 시세 신호를 요약한 것이며 투자 권유가 아닙니다. 기술적 지표·뉴스·공시·실적까지 보려면 \"자세히 분석해줘\"라고 물어보세요."]
    return "\n".join(lines)


def direction_summary_line(res: dict, period: Period) -> str:
    """단독 전망 머리말의 근거 요약 줄: 묶음 비중(강도) · 과거 검증 예측력."""
    t = vote_tally(res["signals"])
    return f"근거 요약: {tally_text(t)}({strength_label(res['label'], t)}) · {power_short(period, res['label'])}"


# ---------- 시세 기준일 점검 ----------

PRICE_STATUS_LATEST, PRICE_STATUS_NOT_COLLECTED, PRICE_STATUS_FAILED = "최신", "미수집", "수집 실패"
PRICE_STATUS_HOLIDAY, PRICE_STATUS_STOCK_ONLY = "휴장일 추정", "종목만 누락"


def price_status(db, base_date: date, now: datetime) -> dict:
    """기대 거래일 시세가 없을 때 이유를 나눈다(KRX 휴장일 달력이 없어 수집 기록으로 추정한다).
    종목만 누락: 지수(KS11)에는 기준일 뒤 날짜가 있다(거래정지이거나 이 종목만 수집 안 됨).
    수집 실패: 기대 거래일 장 마감 뒤 시세 수집(prices) 마지막 기록이 실패. 휴장일 추정: 장 마감 뒤 수집이 성공했는데 시장 전체에
    그날 시세가 없다. 미수집: 장 마감 뒤 시세 수집 기록이 없다."""
    from sqlalchemy import func

    from app.models import DataRefreshLog, MarketIndex

    expected = expected_trading_day(now)
    out = {"expected_date": expected.isoformat(), "status": PRICE_STATUS_LATEST, "detail": ""}
    if base_date >= expected:
        return out
    idx = db.query(func.max(MarketIndex.price_date)).filter(MarketIndex.index_code == "KS11").scalar()
    if idx is not None and idx > base_date:
        out.update(status=PRICE_STATUS_STOCK_ONLY,
                   detail=f"지수에는 {idx.isoformat()} 시세가 있음, 거래정지이거나 이 종목만 수집되지 않았을 수 있음")
        return out
    close_at = datetime.combine(expected, MARKET_CLOSE, tzinfo=KST)
    runs = (db.query(DataRefreshLog).filter(DataRefreshLog.step == "prices", DataRefreshLog.status.in_(("success", "failed")))
            .order_by(DataRefreshLog.id.desc()).limit(20).all())
    after = [r for r in runs if r.started_at is not None and r.started_at >= close_at]
    if after and after[0].status == "failed":
        out.update(status=PRICE_STATUS_FAILED, detail=f"{after[0].started_at.astimezone(KST):%Y-%m-%d %H:%M} 시세 수집 실패 기록")
    elif after:
        out.update(status=PRICE_STATUS_HOLIDAY,
                   detail=f"{after[0].started_at.astimezone(KST):%Y-%m-%d %H:%M} 수집에서 시장 전체에 그날 시세 없음")
    else:
        last = f"마지막 시세 수집 {runs[0].started_at.astimezone(KST):%Y-%m-%d %H:%M}" if runs else "시세 수집 기록 없음"
        out.update(status=PRICE_STATUS_NOT_COLLECTED, detail=f"장 마감 뒤 시세 수집 기록 없음, {last}")
    return out


def expected_trading_day(now: datetime) -> date:
    """지금(KST) 기준으로 종가가 있어야 할 가장 최근 거래일. 장중(거래일 15:30 전)이면 직전 거래일, 휴장일·주말이면 그 앞 거래일.
    2026-11-08(36차)부터 KRX 거래일 달력(app.krx_calendar.expected_trading_day)과 같다(공휴일을 평일로 보지 않는다)."""
    today = now.astimezone(KST).date()
    d = last_business_day(today)
    if d == today and session_is_open(now):
        d = last_business_day(today - timedelta(days=1))
    return d


def data_check(base_date: date, now: datetime, missing_days: list[date], status: dict | None = None) -> dict:
    """데이터 점검: 늦은 영업일 수, 경고 문장, 판단 유보 여부. status(price_status)가 있으면 늦은 이유를 경고에 붙인다."""
    expected = expected_trading_day(now)
    lag = business_days_behind(base_date, expected)
    warnings = []
    why = f"({status['status']}: {status['detail']})" if status and status["status"] != PRICE_STATUS_LATEST else ""
    base, exp = base_date.isoformat(), expected.isoformat()
    # 2026-11-04(25차): 마지막 시세일은 기대 거래일보다 "과거"다. "늦습니다" 대신 "N영업일 이전 데이터"·"N영업일 지연"이라고 쓴다.
    if lag == 1:
        warnings.append(f"마지막 시세({base})가 기대 거래일({exp})보다 1영업일 이전 데이터입니다{why}. "
                        f"이 분석은 {base} 종가 기준이며 최신 거래일 전망이 아닙니다.")
    elif 2 <= lag <= MAX_LAG_BUSINESS_DAYS:
        warnings.append(f"마지막 시세({base})가 기대 거래일({exp})보다 {lag}영업일 지연된 데이터입니다{why}. "
                        f"이 분석은 {base} 종가 기준이며 최신 거래일 전망이 아닙니다.")
    elif lag > MAX_LAG_BUSINESS_DAYS:
        warnings.append(f"마지막 시세({base})가 기대 거래일({exp})보다 {lag}영업일 지연(5영업일 초과)되어{why} "
                        "데이터가 부족합니다. 판단을 유보합니다.")
    from app.krx_calendar import lag as krx_lag

    unknown = krx_lag(base_date, expected).unknown
    if lag >= 1 and unknown:  # 36차: 달력 범위 밖·미확정 날이 지연에 들어 있으면 밝힌다(단순 시세 답과 같은 기준)
        warnings.append(f"지연 계산에 KRX 휴장일 달력으로 확인할 수 없는 날 {unknown}일이 들어 있어 실제 지연은 이보다 짧을 수 있습니다.")
    if missing_days:
        shown = ", ".join(d.isoformat() for d in missing_days[:3]) + (" 등" if len(missing_days) > 3 else "")
        warnings.append(f"가격 데이터 부족: 계산 구간에 지수에는 있는 거래일 {len(missing_days)}일의 시세가 없습니다({shown}). 판단을 유보합니다.")
    return {"expected_date": exp, "lag_business_days": lag, "calendar_unknown_days": unknown, "missing_days": len(missing_days),
            "warnings": warnings, "hold": lag > MAX_LAG_BUSINESS_DAYS or bool(missing_days),
            "status": (status or {}).get("status"), "status_detail": (status or {}).get("detail"),
            "freshness": freshness_label(lag, missing_days, status)}


def freshness_label(lag: int, missing_days: list | None = None, status: dict | None = None) -> str:
    """시세 신선도: 최신 시세 / 1영업일 이전 시세 / N영업일 지연 / 5영업일 초과 지연 / 가격 데이터 부족(+ 수집 상태)."""
    if missing_days:
        return "가격 데이터 부족"
    cause = f"({status['status']})" if status and status.get("status") not in (None, PRICE_STATUS_LATEST) else ""
    if lag <= 0:
        return "최신 시세"
    if lag == 1:
        return f"1영업일 이전 시세{cause}"
    if lag <= MAX_LAG_BUSINESS_DAYS:
        return f"{lag}영업일 지연{cause}"
    return f"5영업일 초과 지연{cause}"


# ---------- 답변 문장 ----------


def _marker_line(company, period: Period, label: str) -> str:
    return f"- {company.name}({company.ticker}), 분석 기간 {period.label}({period.days}거래일): {label}"


def _side_lines(signals: list[dict], side: str) -> list[str]:
    rows = [f"- [{s['group']}] {s['name']}: {s['value']} (규칙: {s['rule']})" for s in signals if s["side"] == side]
    return rows or ["- 없음"]


_SIDE_WORD = {UP: "상승 쪽", DOWN: "하락 쪽", NEUTRAL: "중립"}


def group_lines(signals: list[dict]) -> list[str]:
    """묶음당 1표 판정 근거: 표 집계 한 줄 + 묶음마다 "묶음: 표 ← 지표(쪽), ..." 한 줄. 개별 지표는 모두 그대로 보인다."""
    votes = group_votes(signals)
    n = {side: sum(v == side for v in votes.values()) for side in (UP, DOWN, NEUTRAL)}
    lines = [f"- 묶음 판정(묶음당 1표): 상승 쪽 {n[UP]}표, 하락 쪽 {n[DOWN]}표, 중립 {n[NEUTRAL]}표 (계산한 묶음 {len(votes)}개)"]
    for g in dict.fromkeys(SIGNAL_GROUPS.values()):
        if g not in votes:
            continue
        members = ", ".join(_member(s, signals) for s in signals if s["group"] == g)
        lines.append(f"  - {GROUP_LABELS[g]}: {_SIDE_WORD[votes[g]]} 1표 ← {members}")
    return lines


def _member(s: dict, signals: list[dict]) -> str:
    if counted_elsewhere(s, signals):
        same = next(x for x in signals if x["kind"] == SAME_FACT_AS[s["kind"]])
        return f"{s['name']}({_SIDE_WORD[s['side']]}, '{same['name']}'와 같은 사실이라 세지 않음)"
    return f"{s['name']}({_SIDE_WORD[s['side']]})"


def _count_line(signals: list[dict]) -> str:
    n = {side: sum(s["side"] == side for s in signals) for side in (UP, DOWN, NEUTRAL)}
    return (f"- 개별 지표: 상승 쪽 {n[UP]}개, 하락 쪽 {n[DOWN]}개, 중립 {n[NEUTRAL]}개 "
            f"(계산한 지표 {len(signals)}개. 판정에는 위 묶음 표만 씁니다)")


_BACKTEST: dict = {}
BACKTEST_PATH = Path(__file__).resolve().parent / "data" / "outlook_backtest.json"


def backtest_summary() -> dict | None:
    """scripts/backtest_outlook.py --final이 만든 요약(app/data/outlook_backtest.json). 없으면 None."""
    if "data" not in _BACKTEST:
        try:
            _BACKTEST["data"] = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _BACKTEST["data"] = None
    return _BACKTEST["data"]


def backtest_line(period: Period, label: str) -> str:
    """같은 라벨의 과거 검증 결과 한 줄(검증 구간). 과거 빈도이며 확률·예측이 아니라고 밝힌다."""
    if label == LABEL_HOLD:
        return "- 과거 검증: 판단 유보는 과거 검증 대상이 아닙니다."
    bt = backtest_summary()
    v = ((bt or {}).get("periods", {}).get(period.label, {}).get("valid") or {})
    r = (v.get("labels") or {}).get(label)
    if not r or not r.get("n"):
        return "- 과거 검증: 결과 파일이 없어 표시하지 않습니다."
    lo, hi = r["diff_ci95"]
    line = (f"- 과거 검증(검증 구간 신호일 {v['first_date']}~{v['last_date']}, {v['horizon']}거래일 뒤): 같은 라벨({label})일 때 "
            f"상승 비율 {r['up_rate'] * 100:.1f}%(같은 기간 전체 {r['base_rate'] * 100:.1f}%, 표본 {r['n']:,}, "
            f"차이 {r['diff'] * 100:+.1f}%p, 95% 구간 {lo * 100:+.1f}~{hi * 100:+.1f}%p). 과거에 관측된 빈도이며 확률이나 예측이 아닙니다.")
    if not r.get("significant"):
        line += " 과거 검증에서 이 신호 조합은 전체 평균과 뚜렷한 차이가 없었습니다."
    return line


def _range_text(rg: dict, kind: str, basis: str) -> str:
    """최고·최저 종가 한 줄의 괄호: 조회 기간, 기준 이름(분석 기간/52주)과 거래일 수, 값과 날짜."""
    word = "최고" if kind == "high" else "최저"
    return (f"(조회 기간 {rg['from_date']}~{rg['to_date']}, {basis} 기준 최근 {rg['days']}거래일, "
            f"{word} 종가 {_won(rg[kind])}, {rg[kind + '_date']})")


def indicator_lines(ind: dict, period: Period) -> list[str]:
    lines = []
    for n, r in ind["returns"].items():
        lines.append(f"- {n}거래일 수익률: {_signed(r['pct'])} ({r['from_date']} 종가 {_won(r['from_close'])} 대비 "
                     f"{ind['base_date']} 종가 {_won(ind['close'])})")
    ri = ind.get("regime_inputs") or {}
    if 60 not in ind["returns"] and ri.get("r60") is not None:
        d, c = ri["r60_from"]
        lines.append(f"- 60거래일 수익률: {_signed(ri['r60'])} ({d} 종가 {_won(c)} 대비 {ind['base_date']} 종가 {_won(ind['close'])}, 국면 판단용)")
    if ind.get("day_change_pct") is not None:
        lines.append(f"- 기준일 하루 등락률: {_signed(ind['day_change_pct'])} ({ind['prev_date']} 종가 대비)")
    for n in period.ma:
        ma = ind.get(f"ma{n}")
        if ma:
            side = "위" if ma["gap_pct"] > 0 else ("아래" if ma["gap_pct"] < 0 else "같은 수준")
            lines.append(f"- {n}일 이동평균: {_won(round(ma['value']))} (종가가 {abs(ma['gap_pct']):.2f}% {side}, {ind['base_date']} 기준)")
    if 60 not in period.ma and ri.get("ma60") is not None:
        lines.append(f"- 60일 이동평균: {_won(round(ri['ma60']))} ({ind['base_date']} 기준, 국면 판단용)")
    if ind.get("rsi") is not None:
        lines.append(f"- RSI(14): {ind['rsi']:.2f} ({ind['base_date']} 기준)")
    if ind.get("macd"):
        m = ind["macd"]
        lines.append(f"- MACD(12,26,9): MACD {m['macd']:,.2f}, 시그널 {m['signal']:,.2f}, 차이 {m['hist']:+,.2f} ({ind['base_date']} 기준)")
    if ind.get("volume"):
        v = ind["volume"]
        lines.append(f"- 거래량: {ind['base_date']} {v['value']:,}주, 직전 {VOLUME_AVG_DAYS}거래일 평균 {v['avg']:,}주의 {v['ratio']:.2f}배")
    if ind.get("volatility") is not None:
        lines.append(f"- 변동성: 최근 {VOLATILITY_DAYS}거래일 일간 수익률 표준편차 {ind['volatility']:.2f}% ({ind['base_date']} 기준)")
    rg = ind["range"]
    lines.append(f"- 최고 종가 대비: {_signed(rg['from_high_pct'])} {_range_text(rg, 'high', '분석 기간')}")
    lines.append(f"- 최저 종가 대비: {_signed(rg['from_low_pct'])} {_range_text(rg, 'low', '분석 기간')}")
    r52 = ind.get("range52")
    if r52:
        lines.append(f"- 52주 최고 종가 대비: {_signed(r52['from_high_pct'])} {_range_text(r52, 'high', '52주')}")
        lines.append(f"- 52주 최저 종가 대비: {_signed(r52['from_low_pct'])} {_range_text(r52, 'low', '52주')}")
    return lines


def market_lines(company, ind: dict, fx: dict | None) -> list[str]:
    lines = []
    rel = ind.get("relative")
    miss = ind.get("relative_missing") or {}
    if rel:
        lines.append(f"- {rel['index_name']} 대비({rel['period_days']}거래일, {rel['start_date']} 종가 대비 {rel['end_date']}): "
                     f"{company.name} {_signed(rel['stock_change_pct'])}, {rel['index_name']} {_signed(rel['market_change_pct'])}, "
                     f"차이 {rel['diff_pct_point']:+.2f}%p")
    elif getattr(company, "asset_type", None) == "etf":
        lines.append("- 지수 대비: ETF라 상장 시장 지수와 비교하지 않았습니다.")
    elif miss.get("index_last"):
        lines.append(f"- 지수 대비: {miss.get('index_name', '지수')} 데이터가 {miss['index_last']}까지만 있어 기준일({ind['base_date']}) "
                     "기준으로 비교하지 못했습니다.")
    elif miss:
        lines.append(f"- 지수 대비: {miss.get('index_name', '지수')} 데이터가 없어 비교하지 못했습니다.")
    else:
        lines.append("- 지수 대비: 상장 시장 정보가 없어 비교할 지수를 정하지 못했습니다.")
    if fx:
        lines.append(f"- 원/달러 환율: {fx['date']} {fx['close']:,.2f}원" +
                     (f", {fx['from_date']} 대비 {_signed(fx['pct'])}" if fx.get("pct") is not None else ""))
    else:
        lines.append("- 원/달러 환율: 데이터 없음")
    return lines


def indirect_lines(section: str | None, topic: str | None, items: list[dict], period: Period) -> list[str]:
    """간접 관련 뉴스(업종·기초지수). 이 종목 기사가 아니며 신호 판정에 쓰지 않는다고 밝힌다."""
    if section is None:
        return []
    lines = [f"- 아래는 이 종목 기사가 아니라 {topic} 관련 기사입니다(제목 기준, 최근 {period.news_days}일). 신호 판정에는 쓰지 않습니다."]
    for n in items:
        src = f"({n['source']}) " if n.get("source") else ""
        lines.append(f"- 뉴스 {_item_date(n)} {src}\"{n['title']}\"라는 제목의 기사가 있었습니다.")
    if not items:
        lines.append(f"- 최근 {period.news_days}일 안에 {topic} 관련 기사를 찾지 못했습니다.")
    return lines


def earnings_lines(rows: list[dict] | None) -> list[str]:
    """실적(참고, 신호 판정에 쓰지 않음). rows는 app.earnings.recent_for_answer 결과. None이면 섹션 생략."""
    if rows is None:
        return []
    if not rows:
        return ["- 실적 데이터 없음(수집된 DART 주요 계정이 없습니다)."]
    lines = ["- 신호 판정에는 쓰지 않는 참고 정보입니다. 분기(3개월) 금액이며, 확정은 정기보고서(4분기는 연간 금액에서 3분기 누적을 뺀 값), "
             "잠정은 거래소 잠정실적 공정공시(감사 전 잠정치)입니다. 같은 분기의 정기보고서가 나오면 확정치로 바꿉니다. "
             "전년 동기는 같은 기준(분기 3개월, 같은 연결·별도)의 금액입니다."]
    lines += [f"- {earnings_row_text(r)}" for r in rows]
    if any(r.get("revenue") and r.get("net_income") is not None and r["revenue"] > 0 and abs(r["net_income"]) > r["revenue"]
           for r in rows):
        # 2026-11-04 점검: 1분기 + 2분기 = 반기 누적으로 확인했다(누적값이 섞인 것이 아니다). 영업외 손익이 큰 분기다.
        lines.append("- 당기순이익이 매출액보다 큰 분기는 DART 공시 값 그대로입니다(분기 합이 누적과 맞는 것을 확인했습니다). "
                     "당기순이익에는 영업외 손익이 포함됩니다.")
    return lines


REPORT_NAMES = {"11013": "1분기보고서", "11012": "반기보고서", "11014": "3분기보고서", "11011": "사업보고서"}


def earnings_row_text(r: dict) -> str:
    """실적 한 분기: "2026년 3분기(연결, 잠정): 매출액 …억 원, 전년 동기 대비 …%; … (출처, 접수·발표일, 접수번호)"."""
    pre = r.get("source") == "preliminary"
    basis = r.get("basis") if pre else ("연결" if r["fs_div"] == "CFS" else "별도")
    head = f"{r['period_label']}({basis}, {'잠정' if pre else '확정'}{', 연간 - 3분기 누적' if r.get('derived') else ''})"
    if pre:
        src = f"잠정실적 공정공시, 접수·발표일 {r['disclosed_date']}, DART 접수번호 {r['rcept_no']}, 감사 전 잠정치"
        if r.get("corrected"):
            c = r["corrected"]
            return (f"{head}: 정정공시({c['date'].isoformat()}, DART 접수번호 {c['rcept_no']})가 있어 원 공시 수치를 싣지 않았습니다 "
                    f"({src})")
    else:
        src = f"{REPORT_NAMES.get(r.get('reprt_code'), '정기보고서')}, 접수일 {r['disclosed_date']}, DART 접수번호 {r['rcept_no']}"
    parts = []
    for key, name in (("revenue", "매출액"), ("operating_income", "영업이익"), ("net_income", "당기순이익")):
        cur, prev = r.get(key), r.get(f"{key}_prev")
        if cur is None:
            continue
        yoy = f", 전년 동기 대비 {(cur / prev - 1) * 100:+.1f}%" if prev is not None and prev > 0 and cur >= 0 else ""
        parts.append(f"{name} {_eok(cur)}{yoy}")
    return f"{head}: " + "; ".join(parts) + f" ({src})"


def _eok(v: float) -> str:
    """원 단위 금액을 억 원 단위로(소수 없이)."""
    return f"{v / 1e8:,.0f}억 원"


def _item_date(item: dict) -> str:
    """뉴스·공시 한 건의 표시 날짜(evidence()의 "date", 없으면 같은 규칙으로 계산)."""
    if item.get("date"):
        return item["date"]
    if "disclosed_at" in item or "source_url" in item:
        return disclosure_date(item.get("source_url"), item.get("disclosed_at")) or ""
    return kst_date(item.get("published_at")) or ""


def evidence_lines(news: list[dict], disclosures: list[dict], is_etf: bool, period: Period) -> list[str]:
    lines = []
    for n in news:
        src = f"({n['source']}) " if n.get("source") else ""
        lines.append(f"- 뉴스 {_item_date(n)} {src}\"{n['title']}\"라는 제목의 기사가 있었습니다.")
    if not news and not is_etf:
        lines.append(f"- 관련 뉴스를 찾지 못했습니다(최근 {period.news_days}일, 제목에 종목 이름이 있는 기사 기준).")
    for d in disclosures:
        lines.append(f"- 공시 {_item_date(d)} 접수: \"{d['title']}\"")
    if not disclosures and not is_etf:
        lines.append(f"- 최근 {period.news_days}일 안에 접수된 공시를 찾지 못했습니다.")
    return lines


# ---------- 결론 먼저(2026-11-04): 제목 줄·상태 줄·종합 판단·근거 항목·확인할 사항. 문장은 모두 코드 템플릿이다 ----------


def title_line(company, period: Period) -> str:
    return f"{company.name}({company.ticker}) · 향후 {period.days}거래일"


def state_line(regime: str, ind: dict) -> str:
    return f"현재는 {regime} (기준일 {ind['base_date']} 종가)"


def _ma_names(ns: list[int]) -> str:
    return "·".join(f"{n}일선" for n in ns)


def _ma_values(ind: dict, period: Period) -> dict[int, float]:
    """보여 줄 이동평균: 분석 기간의 단기·장기선과 국면 판단의 60일선."""
    out = {n: ind[f"ma{n}"]["value"] for n in period.ma if ind.get(f"ma{n}")}
    ri = ind.get("regime_inputs") or {}
    if 60 not in out and ri.get("ma60") is not None:
        out[60] = ri["ma60"]
    return dict(sorted(out.items()))


def _drop_clause(ind: dict) -> str:
    ri = ind["regime_inputs"]
    if ri["r20"] <= REGIME_DROP20_PCT:
        return f"20거래일 {_signed(ri['r20'])} 급락"
    return f"60거래일 {_signed(ri['r60'])} 하락"


def _trend_clause(ind: dict) -> str:
    ri = ind["regime_inputs"]
    return f"20거래일 {_signed(ri['r20'])}, 60거래일 {_signed(ri['r60'])}"


def _below_clause(ind: dict, period: Period) -> str | None:
    below = [n for n, v in _ma_values(ind, period).items() if ind["close"] < v]
    return f"주가가 {_ma_names(below)} 아래에 있어" if below else None


def signal_sentence(res: dict, regime: str, period: Period) -> str:
    """종합 판단의 신호·국면 문장(1~2문장). 묶음 판정이 한쪽으로 뚜렷하고 국면과 맞을 때만 "우세합니다"라고 쓴다."""
    ind, label = res["indicators"], res["label"]
    tally = tally_text(vote_tally(res["signals"]))
    if label == LABEL_HOLD:
        return f"데이터가 부족해 신호 판정을 유보합니다({tally}, 데이터 경고 참고)."
    if regime == REGIME_HOLD:
        side = {LABEL_UP: "상승 쪽이 많지만", LABEL_DOWN: "하락 쪽이 많지만", LABEL_NEUTRAL: "어느 쪽으로도 뚜렷하지 않고"}[label]
        return f"현재 신호는 {side}({tally}), 국면을 판단할 시세(60거래일)가 부족합니다."
    below = _below_clause(ind, period)
    if label == LABEL_UP:
        if regime == REGIME_UP:
            return f"현재 신호는 상승 쪽이 우세합니다({tally}). {_trend_clause(ind)}로 상승 추세가 이어지는 상태입니다."
        if regime == REGIME_REBOUND:
            mid = f"{_drop_clause(ind)} 뒤의 반등이고 {below}" if below else f"{_drop_clause(ind)} 뒤의 반등이라"
            return f"단기 신호는 상승 쪽이 많지만({tally}), {mid} 추세 전환은 확인되지 않았습니다."
        if regime == REGIME_DOWN:
            return f"단기 신호는 상승 쪽이 많지만({tally}), {_trend_clause(ind)}의 하락 추세 안이라 추세가 바뀌었다고 보기는 어렵습니다."
        if regime == REGIME_PULLBACK:
            return f"신호는 상승 쪽이 많고({tally}), 60거래일 {_signed(ind['regime_inputs']['r60'])} 상승 추세 안에서 최근 조정을 받은 상태입니다."
        return f"신호는 상승 쪽이 많지만({tally}), 20거래일 {_signed(ind['regime_inputs']['r20'])}로 뚜렷한 추세가 없는 횡보 구간입니다."
    if label == LABEL_DOWN:
        if regime == REGIME_DOWN:
            return f"현재 신호는 하락 쪽이 우세합니다({tally}). {_trend_clause(ind)}로 하락 추세가 이어지는 상태입니다."
        if regime == REGIME_REBOUND:
            return f"신호는 하락 쪽이 많고({tally}), {_drop_clause(ind)} 뒤 반등을 시도했지만 추세 전환은 확인되지 않았습니다."
        if regime == REGIME_PULLBACK:
            return (f"단기 신호는 하락 쪽이 많지만({tally}), 60거래일 {_signed(ind['regime_inputs']['r60'])} 상승 추세 안의 조정이라 "
                    "추세가 꺾였다고 보기는 어렵습니다.")
        if regime == REGIME_UP:
            return f"단기 신호는 하락 쪽이 많지만({tally}), {_trend_clause(ind)}의 상승 추세는 아직 유지되고 있습니다."
        return f"신호는 하락 쪽이 많지만({tally}), 20거래일 {_signed(ind['regime_inputs']['r20'])}로 뚜렷한 추세가 없는 횡보 구간입니다."
    head = f"현재 신호는 상승·하락 어느 쪽으로도 뚜렷하지 않습니다({tally})."
    tail = {
        REGIME_REBOUND: f" {_drop_clause(ind)} 뒤 반등을 시도하는 중이지만 추세 전환은 확인되지 않았습니다.",
        REGIME_UP: f" 다만 {_trend_clause(ind)}로 상승 추세는 이어지고 있습니다.",
        REGIME_PULLBACK: f" 60거래일 {_signed(ind['regime_inputs']['r60'])} 상승 추세 안에서 조정을 받는 중입니다.",
        REGIME_DOWN: f" 다만 {_trend_clause(ind)}로 하락 추세는 이어지고 있습니다.",
        REGIME_FLAT: f" 20거래일 {_signed(ind['regime_inputs']['r20'])}로 뚜렷한 추세가 없는 횡보 구간입니다.",
    }[regime]
    return head + tail


def _backtest_row(period: Period, label: str) -> dict | None:
    v = ((backtest_summary() or {}).get("periods", {}).get(period.label, {}).get("valid") or {})
    r = (v.get("labels") or {}).get(label)
    return r if r and r.get("n") else None


def backtest_sentence(period: Period, label: str) -> str | None:
    """종합 판단의 과거 검증 문장. 판단 유보도 그렇다고 한 문장 쓴다(26차: 종합 판단이 한 문장뿐이던 것)."""
    if label == LABEL_HOLD:
        return "판단 유보는 과거 검증 대상이 아니라 방향 예측력을 말할 수 없습니다."
    r = _backtest_row(period, label)
    if r is None:
        return "과거 검증 결과가 아직 없어 이 신호 상태가 과거에 방향을 맞혔는지는 말할 수 없습니다."
    if not r.get("significant"):
        # 26차: 숫자(상승 비율·표본·구간)는 "과거 검증" 섹션에 한 번만 쓴다
        return f"과거 검증에서 이 신호 조합({label})의 {period.label} 뒤 방향 예측력은 확인되지 않았습니다(아래 과거 검증)."
    rates = f"상승 비율 {r['up_rate'] * 100:.1f}% vs 전체 {r['base_rate'] * 100:.1f}%, {_power_stats(r)}"
    if r.get("significant"):
        return (f"과거 검증에서 이 신호 상태({label})의 {period.label} 뒤 {rates}로 차이가 있었지만, "
                "과거 빈도일 뿐 확률이나 예측이 아닙니다.")
    if label == LABEL_NEUTRAL:
        return f"과거 검증에서도 이런 중립 상태의 {period.label} 뒤 방향은 전체 평균과 뚜렷한 차이가 없었습니다({rates})."
    return f"과거 검증에서 이 신호 조합({label})은 {period.label} 뒤 방향을 전체 평균보다 잘 맞히지 못했습니다({rates})."


def summary_lines(res: dict, regime: str, period: Period) -> list[str]:
    """종합 판단 2~3문장(한 문단)."""
    parts = [signal_sentence(res, regime, period), backtest_sentence(period, res["label"])]
    return [" ".join(p for p in parts if p)]


def _signal_text(s: dict, ind: dict, period: Period, by_kind: dict) -> str:
    """신호 한 개를 숫자·날짜가 있는 짧은 문장으로."""
    k = s["kind"]
    if k == "period_return":
        r = ind["returns"][period.days]
        return f"최근 {period.days}거래일 수익률 {_signed(r['pct'])}({r['from_date']} 종가 대비)"
    if k in ("close_vs_ma_short", "close_vs_ma_long"):
        n = period.ma[0] if k == "close_vs_ma_short" else period.ma[1]
        return f"종가 {_won(ind['close'])}이 {n}일선({_won(round(ind[f'ma{n}']['value']))}) {'위' if s['side'] == UP else '아래'}"
    if k == "ma_alignment":
        short, long = period.ma
        c = by_kind.get("cross")
        cross = f", {c['name']}" if c and c["side"] == s["side"] else ""
        return (f"{short}일선 {_won(round(ind[f'ma{short}']['value']))}이 {long}일선 {_won(round(ind[f'ma{long}']['value']))} "
                f"{'위' if s['side'] == UP else '아래'}{cross}")
    if k == "relative":
        rel = ind["relative"]
        return f"같은 {rel['period_days']}거래일 {rel['index_name']} 대비 {rel['diff_pct_point']:+.2f}%p"
    if k == "macd":
        m = ind["macd"]
        caveat = ", 단 MACD는 0 아래" if s["side"] == UP and m["macd"] < 0 else ", 단 MACD는 0 위" if s["side"] == DOWN and m["macd"] > 0 else ""
        return f"{s['name']}(MACD {m['macd']:,.2f}, 시그널 {m['signal']:,.2f}{caveat})"
    if k == "rsi":
        zone = "과매도" if s["side"] == UP else "과열" if s["side"] == DOWN else "중간"
        return f"RSI(14) {ind['rsi']:.2f}, {zone} 구간"
    if k == "volume" and ind.get("volume"):
        return f"{s['name']}(기준일 하루 {_signed(ind.get('day_change_pct') or 0)}, 거래량은 직전 {VOLUME_AVG_DAYS}거래일 평균의 {ind['volume']['ratio']:.2f}배)"
    return s["name"]


def group_evidence(ind: dict, signals: list[dict], period: Period) -> dict[str, list[str]]:
    """근거를 묶음 판정과 같은 원천에서 만든다(2026-11-04 26차). 상승 근거 = 상승 쪽 묶음의 상승 신호, 하락 위험 = 하락 쪽 묶음의 하락 신호,
    엇갈린 묶음 = 상승·하락 신호가 섞여 중립이 된 묶음, 추세 참고 = 신호 판정 표에 넣지 않는 20·60거래일 수익률·큰 하루 등락.
    교차는 같은 사실(이동평균 배열)이 있으면 배열 문장 안에만 쓰고 따로 세우지 않는다(SAME_FACT_AS). 항목 앞 [묶음]은 판정 상세의 묶음 이름."""
    votes = group_votes(signals)
    by_kind = {s["kind"]: s for s in signals}
    out: dict[str, list[str]] = {"up": [], "down": [], "mixed": [], "context": []}
    for g, vote in votes.items():
        members = [s for s in signals if s["group"] == g and not counted_elsewhere(s, signals)]
        ups = [_signal_text(s, ind, period, by_kind) for s in members if s["side"] == UP]
        downs = [_signal_text(s, ind, period, by_kind) for s in members if s["side"] == DOWN]
        if vote == UP and ups:
            out["up"].append(f"[{g}] {'; '.join(ups)}" + (f" (같은 묶음의 반대 신호: {'; '.join(downs)})" if downs else ""))
        elif vote == DOWN and downs:
            out["down"].append(f"[{g}] {'; '.join(downs)}" + (f" (같은 묶음의 반대 신호: {'; '.join(ups)})" if ups else ""))
        elif vote == NEUTRAL and ups and downs:
            out["mixed"].append(f"[{g}] {'; '.join(ups)}(상승)지만 {'; '.join(downs)}(하락)라 묶음은 중립입니다")
    ri = ind.get("regime_inputs") or {}
    for n, threshold in ((20, REGIME_TREND20_PCT), (60, REGIME_TREND60_PCT)):
        if n == period.days or ri.get(f"r{n}") is None or abs(ri[f"r{n}"]) < threshold:
            continue
        d, c = ri[f"r{n}_from"]
        out["context"].append(f"{n}거래일 수익률 {_signed(ri[f'r{n}'])}({d} 종가 {_won(c)} 대비)")
    if 60 not in period.ma and ri.get("ma60") is not None:
        out["context"].append(f"종가가 60일선({_won(round(ri['ma60']))}) {'위' if ind['close'] > ri['ma60'] else '아래'}")
    day = ind.get("day_change_pct")
    if "volume" not in by_kind and day is not None and abs(day) >= BIG_DAY_MOVE_PCT:
        out["context"].append(f"기준일({ind['base_date']}) 하루 {_signed(day)}")
    return out


def evidence_section_lines(ge: dict) -> tuple[list[str], list[str]]:
    """상세 답의 "상승을 뒷받침하는 근거"와 "하락 위험 근거" 섹션 줄."""
    up = [f"- {x}" for x in ge["up"]] or ["- 상승 쪽으로 판정된 묶음은 없습니다."]
    down = [f"- {x}" for x in ge["down"]] or ["- 하락 쪽으로 판정된 묶음은 없습니다."]
    down += [f"- 엇갈린 묶음: {x}" for x in ge["mixed"]]
    if ge["context"]:
        down.append(f"- 추세 참고(신호 판정 표 밖): {'; '.join(ge['context'])}")
    return up, down


def _risk_text(ge: dict, n: int = 2) -> str:
    """간결 답·후속 답의 하락 위험 한 줄: 하락 쪽 묶음 근거, 없으면 그렇게 말하고 엇갈린 묶음·추세 참고."""
    head = "; ".join(ge["down"][:n]) if ge["down"] else "하락 쪽으로 판정된 묶음은 없습니다."
    extra = []
    if ge["mixed"]:
        extra.append(f"엇갈린 묶음: {ge['mixed'][0]}")
    if ge["context"]:
        extra.append(f"추세 참고(신호 판정 표 밖): {'; '.join(ge['context'][:2])}")
    if not extra:
        return head
    return " ".join([head if head.endswith(".") else head + "."] + [x + "." for x in extra])


def _support_text(ge: dict, n: int = 2) -> str:
    return "; ".join(ge["up"][:n]) if ge["up"] else "상승 쪽으로 판정된 묶음은 없습니다."


def check_items(ind: dict, res: dict, regime: str, period: Period, is_etf: bool) -> list[str]:
    """앞으로 확인할 사항(가격 수준으로)."""
    lines = []
    mas = _ma_values(ind, period)
    above = [n for n, v in mas.items() if v > ind["close"]]
    below = [n for n, v in mas.items() if v < ind["close"]]
    parts = []
    if above:
        parts.append("·".join(f"{n}일선({_won(round(mas[n]))})" for n in above) + " 회복 여부")
    if below:
        parts.append("·".join(f"{n}일선({_won(round(mas[n]))})" for n in below) + " 지지 여부")
    if parts:
        lines.append("- 이동평균: " + ", ".join(parts))
    rg = ind["range"]
    window = f"분석 기간(최근 {rg['days']}거래일, {rg['from_date']}~{rg['to_date']})"
    lines.append(f"- {window} 최저 종가 {_won(rg['low'])}({rg['low_date']}) 아래로 내려가는지, "
                 f"같은 기간 최고 종가 {_won(rg['high'])}({rg['high_date']}) 위로 올라서는지")
    vol = ind.get("volume")
    ratio = f"(기준일 거래량은 직전 {VOLUME_AVG_DAYS}거래일 평균의 {vol['ratio']:.2f}배)" if vol else ""
    if regime == REGIME_REBOUND or res["label"] == LABEL_UP:
        lines.append(f"- 반등이 이어질 때 거래량이 함께 늘어나는지{ratio}")
    elif regime == REGIME_DOWN or res["label"] == LABEL_DOWN:
        lines.append(f"- 하락할 때 거래량이 늘어나는지{ratio}")
    else:
        lines.append(f"- 거래량 변화{ratio}")
    lines.append("- 기초지수·업종 뉴스" if is_etf else "- 업종 뉴스와 다음 실적 공시(분기보고서·잠정실적)")
    return lines


_KEY_NEWS_RE = re.compile(r"실적|컨콜|컨퍼런스\s*콜|자사주|자기주식|배당|수주|공급\s*계약|증설|HBM|수율|인수|합병|유상증자|분할|가이던스")
_KEY_DISCLOSURE_RE = re.compile(r"실적|배당|자기주식|합병|분할|유상증자|무상증자|공급계약|단일판매")
KEY_NEWS_LIMIT = 2


def key_news(items: list[dict]) -> list[dict]:
    """판정에 넣지 않는 사실로 쓸 기사: 제목에 실적·컨콜·자사주·수주 같은 말이 있는 기사, 최근 것부터 KEY_NEWS_LIMIT건."""
    return [n for n in items if _KEY_NEWS_RE.search(n.get("title") or "")][:KEY_NEWS_LIMIT]


def fact_lines(news: list[dict], disclosures: list[dict], earnings: list[dict] | None) -> list[str]:
    """신호 판정에 넣지 않은 최근 사실(공시·기사·실적). 기사 제목은 "~라는 기사" 형태로만 쓴다."""
    subs = []
    for d in [d for d in disclosures if _KEY_DISCLOSURE_RE.search(d.get("title") or "")][:1]:
        subs.append(f"  - {_item_date(d)} \"{d['title']}\" 공시가 있었습니다.")
    for n in key_news(news):
        subs.append(f"  - {_item_date(n)} \"{n['title']}\"라는 기사가 있었습니다.")
    if earnings and not earnings[0].get("corrected"):
        r = earnings[0]
        cur, prev = r.get("operating_income"), r.get("operating_income_prev")
        if cur is not None:
            yoy = f", 전년 동기 대비 {(cur / prev - 1) * 100:+.1f}%" if prev is not None and prev > 0 and cur >= 0 else ""
            pre = r.get("source") == "preliminary"
            basis = r.get("basis") if pre else ("연결" if r["fs_div"] == "CFS" else "별도")
            when = f", {r['disclosed_date']} 잠정실적 공정공시" if pre else ""
            subs.append(f"  - 실적: {r['period_label']}({basis}, {'잠정' if pre else '확정'}{when}) 영업이익 {_eok(cur)}{yoy}.")
    return ["- 신호 판정에 넣지 않은 최근 사실:", *subs] if subs else []


def limit_lines(company) -> list[str]:
    lines = [f"- {NOT_A_FORECAST}",
             "- 위 신호는 과거 시세에서 계산한 것이며 미래 방향을 보장하지 않습니다. 실적 발표·공시·시장 이벤트로 방향이 바뀔 수 있습니다.",
             f"- {NO_PROBABILITY}"]
    if getattr(company, "asset_type", None) == "etf" and (kind := leverage_kind(company.name)):
        lines.append("- " + leverage_notice(company.name, kind).lstrip("※ "))
    lines.append(f"- {DISCLAIMER}")
    return lines


def source_lines(ind: dict, data: dict, period: Period, now: datetime) -> list[str]:
    lines = [f"- 답변 생성: {now.astimezone(KST):%Y-%m-%d %H:%M} KST(시장 데이터 기준일 {ind['base_date']} 종가와 다릅니다)",
             f"- 기준일(최신 거래일): {ind['base_date']}, 오늘(Asia/Seoul): {now.astimezone(KST).date().isoformat()}, "
             f"기대 거래일: {data['expected_date']}" + (f", 시세 상태: {data['status']}" if data.get("status") else "")
             + (f", 시세 신선도: {data['freshness']}" if data.get("freshness") else "")]
    # 26차: 데이터 경고 문장은 머리말에 한 번만 쓴다(여기에는 시세 상태·신선도만)
    lines.append("- 수익률 계산식: (기준일 종가 ÷ N거래일 전 종가 - 1) × 100. 최고·최저 종가 대비도 같은 식이며 수익률이 아닙니다.")
    lines.append(f"- 지표 계산에 쓴 가격 범위(이동평균·MACD·RSI 등): {ind['first_date']}~{ind['base_date']} 종가 {ind['n_rows']}거래일. "
                 "최고·최저 종가는 각 줄에 적은 조회 기간만 봅니다.")
    lines.append(f"- 근거 뉴스·공시 범위: 최근 {period.news_days}일 발행·접수분")
    lines.append("- 출처: 시세 stock_price(종가), 지수 market_index, 환율 exchange_rate, 뉴스 news(발행일), 공시 disclosure(DART 접수일), "
                 "실적 quarterly_earnings(DART 주요 계정)·잠정실적 공정공시(disclosure), 과거 검증 app/data/outlook_backtest.json")
    return lines


def rule_lines(company, period: Period, res: dict) -> list[str]:
    """신호 판정 상세: 표식 줄(후속 질문이 기간을 이어받는다), 묶음 표, 개별 지표와 규칙, 판정·국면 규칙. 규칙 설명은 여기에만 둔다."""
    sig, label = res["signals"], res["label"]
    lines = [_marker_line(company, period, label),
             "- 위 라벨은 묶음 표를 센 결과이며, 방향을 예측하는 값이 아닙니다(과거 검증 참고).",
             *group_lines(sig), _count_line(sig)]
    lines += [f"- [{s['group']}] {s['name']}: {_SIDE_WORD[s['side']]} ({s['value']}; 규칙: {s['rule']})" for s in sig]
    t = vote_tally(sig)
    lines.append(f"- 신호 강도: {tally_text(t)}({strength_label(label, t)}). 기술적 신호의 우위이며, 미래 주가 예측력은 과거 검증으로 따로 본다.")
    lines.append(f"- 판정 규칙: {JUDGE_RULE}")
    lines.append(f"- 방향 판단 기준: {DIRECTION_RULE}")
    lines.append(f"- 신뢰도 기준: {CONFIDENCE_RULE}")
    lines.append(f"- 국면 규칙: {REGIME_RULE}")
    return lines


def _join(sections: list[tuple[str, list[str]]]) -> str:
    return "\n\n".join(f"## {title}\n" + "\n".join(lines) for title, lines in sections if lines)


def forecast_answer(company, period: Period, res: dict, news: list[dict], disclosures: list[dict], fx: dict | None,
                    now: datetime, indirect: tuple | None = None, earnings: list[dict] | None = None) -> str:
    """결론 먼저(2026-11-04, API.md "답변 구조"): 제목 줄 · 상태 줄 -> 종합 판단 -> 상승 근거 -> 하락 위험 -> 확인할 사항 -> 과거 검증
    -> 상세 근거(지표, 뉴스·공시, 실적, 시장, 판정 상세, 한계, 기준일·출처).
    indirect = (섹션 이름, 업종/기초지수, 기사들) 또는 None, earnings = 실적 행 목록(None이면 섹션 생략)."""
    ind, label, data = res["indicators"], res["label"], res["data"]
    regime = classify_regime(ind, label)
    is_etf = getattr(company, "asset_type", None) == "etf"
    # 26차: 제목 다음 첫 줄이 방향, 바로 다음 데이터 경고, 그다음 국면. 근거 요약 줄은 종합 판단·판정 상세와 겹쳐 뺐다.
    intro = [direction_title(company, period, res), "", direction_headline(res, regime, period)]
    intro += [f"데이터 경고: {w}" for w in data["warnings"]]
    intro.append(state_line(regime, ind))
    up, down = evidence_section_lines(group_evidence(ind, res["signals"], period))
    section, topic, items = indirect or (None, None, [])
    direct = evidence_lines(news, disclosures, is_etf, period)
    if is_etf and not news and section is not None:
        direct = ["- 제목에 이 ETF 이름이 있는 기사를 찾지 못했습니다(간접 관련 기사는 아래 섹션에 따로 있습니다)."]
    summary = summary_lines(res, regime, period)
    facts = fact_lines(news, disclosures, earnings)
    sections = [
        ("종합 판단", summary + ([""] + facts if facts else [])),
        ("상승을 뒷받침하는 근거", up),
        ("하락 위험 근거", down),
        ("앞으로 확인할 사항", check_items(ind, res, regime, period, is_etf)),
        ("과거 검증", [backtest_line(period, label)]),
        ("기술적 지표", indicator_lines(ind, period)),
        ("뉴스·공시(직접 관련)", direct),
        (section or "", indirect_lines(section, topic, items, period)),
        ("실적(참고)", earnings_lines(earnings)),
        ("시장 상황", market_lines(company, ind, fx)),
        ("신호 판정 상세", rule_lines(company, period, res)),
        ("불확실성과 한계", limit_lines(company)),
        ("데이터 기준일·출처", source_lines(ind, data, period, now)),
    ]
    return "\n".join(intro) + "\n\n" + _join(sections)


FOLLOWUP_POINTER = "자세한 근거는 바로 위 분석에 있습니다."
DATA_POLICY = ("시세가 1~5영업일 늦으면 기준일과 늦은 이유(미수집·수집 실패·휴장일 추정·종목만 누락)를 밝히고 최신 전망이라고 하지 않습니다. "
               f"{MAX_LAG_BUSINESS_DAYS}영업일을 넘게 늦거나 계산 구간에 빠진 거래일이 있으면 판단을 유보합니다. "
               "KRX 휴장일 달력이 없어 휴장일은 수집 기록으로 추정합니다.")


def _level_line(ind: dict, period: Period) -> str:
    """확인할 가격대 한 줄(후속 답): 종가 위쪽 이동평균은 회복, 아래쪽은 지지."""
    mas = _ma_values(ind, period)
    above = [n for n, v in mas.items() if v > ind["close"]]
    below = [n for n, v in mas.items() if v < ind["close"]]
    if above:
        return "·".join(f"{n}일선 {_won(round(mas[n]))}" for n in above) + " 회복 여부"
    if below:
        return "·".join(f"{n}일선 {_won(round(mas[n]))}" for n in below) + " 지지 여부"
    rg = ind["range"]
    return f"분석 기간 최저 종가 {_won(rg['low'])}({rg['low_date']})"


def _compare_clause(t: dict) -> str:
    if t["up"] > t["down"]:
        return "묶음 표로는 상승 근거가 더 많습니다"
    if t["down"] > t["up"]:
        return "묶음 표로는 하락 위험이 더 많습니다"
    return "묶음 표로는 상승·하락이 비슷합니다"


def _price_status_line(data: dict, base: str) -> str:
    if data.get("status") in (None, PRICE_STATUS_LATEST):
        return f"- 지금 상태: 기대 거래일 {data['expected_date']} 시세까지 반영돼 있습니다(최신)."
    return (f"- 지금 상태: 기대 거래일 {data['expected_date']} 시세가 없습니다({data['status']}: {data['status_detail']}). "
            f"그래서 이 판단은 {base} 종가 기준이며 최신 거래일 전망이 아닙니다.")


def _reaction_line(r: dict, reaction: dict | None) -> str | None:
    """실적 발표(접수)일 직전 거래일 종가 -> 기준일 종가. reaction = {"before_date", "before_close", "base_date", "base_close"}."""
    if not reaction:
        return None
    pct = (reaction["base_close"] / reaction["before_close"] - 1) * 100
    return (f"- 발표 뒤 주가: 발표일({r['disclosed_date']}) 직전 거래일 {reaction['before_date']} 종가 {_won(reaction['before_close'])} → "
            f"기준일 {reaction['base_date']} 종가 {_won(reaction['base_close'])}({pct:+.2f}%). 시장·업종 움직임이 섞인 값이며, "
            "실적이 이후 주가 방향을 예측하는지는 검증하지 않았습니다.")


def _group_names(res: dict, side: str) -> list[str]:
    return [g for g, v in group_votes(res["signals"]).items() if v == side]


def why_sentence(res: dict, regime: str, period: Period) -> str:
    """"왜?": 방향을 먼저 말하고, 묶음별 표(어느 묶음이 어느 쪽인지)로 이유를 한 문장에 쓴다."""
    d, _, t = _direction(res, period)
    ups, downs = _group_names(res, UP), _group_names(res, DOWN)
    first = direction_sentence(res, regime, period).split(" 다만 ")[0].split("(")[0].rstrip(".") + "."
    up_txt = f"상승 쪽 묶음이 {len(ups)}개({', '.join(ups)})이고" if ups else "상승 쪽 묶음은 없고"
    down_txt = f"하락 쪽 묶음이 {len(downs)}개({', '.join(downs)})이며" if downs else "하락 쪽 묶음은 없으며"
    if d == DIR_HOLD:
        return f"{first} 그렇게 본 이유는 {_hold_reason(res, t)}이기 때문입니다({tally_text(t)})."
    gap = abs(t["up"] - t["down"])
    reach = "에 이르기" if gap >= GROUP_MARGIN else "에 못 미치기"
    return (f"{first} 그렇게 본 이유는 {up_txt} {down_txt} 중립이 {t['neutral']}개로, 상승·하락 묶음 차이 {gap}개가 "
            f"판정 기준({GROUP_MARGIN}개){reach} 때문입니다({tally_text(t)}).")


def oneline_answer(company, period: Period, res: dict, regime: str) -> str:
    """"한 줄로만": 제목(방향 표식)과 방향 한 문장, 국면을 한 줄에."""
    d, conf, t = _direction(res, period)
    lead = t["up"] if d == DIR_UP else t["down"] if d == DIR_DOWN else None
    if d in (DIR_UP, DIR_DOWN):
        weak = ", 약한 신호" if lead * 2 < t["total"] else ""
        core = f"{d}입니다({tally_text(t)}{weak}, 신뢰도 {conf})."
    elif d == DIR_NEUTRAL:
        core = f"현재는 상승과 하락 중 어느 쪽도 뚜렷하게 우세하지 않습니다({tally_text(t)})."
    else:
        core = f"데이터가 부족해 현재는 방향 판단을 보류하겠습니다({_hold_reason(res, t)}, {tally_text(t)})."
    return f"{direction_title(company, period, res)} · {core} {state_line(regime, res['indicators'])}."


_SIGNAL_MEANING = {
    "cross": "골든크로스는 단기 이동평균선이 장기 이동평균선을 아래에서 위로 뚫고 올라가는 것, 데드크로스는 위에서 아래로 내려가는 것입니다",
    "macd": "MACD는 12일·26일 지수이동평균의 차이이고 시그널은 MACD의 9일 지수이동평균입니다. MACD가 시그널 위면 단기 상승 탄력이 커지는 쪽으로 봅니다",
    "rsi": "RSI(14)는 최근 14거래일 상승폭과 하락폭으로 계산한 0~100 사이 값이며, 30 아래는 과매도·70 위는 과열 구간으로 봅니다",
    "volume": "거래량 배수는 기준일 거래량을 직전 20거래일 평균 거래량으로 나눈 값입니다",
    "relative": "지수 대비는 같은 기간 종목 수익률에서 지수(코스피·코스닥) 수익률을 뺀 값입니다",
    "ma": "이동평균선은 최근 N거래일 종가의 평균이며, 종가가 그 위면 그 기간 평균보다 강한 흐름으로 봅니다",
}
_SIGNAL_KINDS = {"cross": ("cross",), "macd": ("macd",), "rsi": ("rsi",), "volume": ("volume",), "relative": ("relative",),
                 "ma": ("ma_alignment", "close_vs_ma_short", "close_vs_ma_long")}


def signal_answer(head: str, res: dict, period: Period, term: tuple[str, str] | None) -> str:
    """신호 의미 질문(26차): 정의 -> 지금 값 -> 판정(개별 신호와 그 신호가 속한 묶음의 판정을 구분)."""
    kind, word = term or ("ma", "이동평균")
    sig = [s for s in res["signals"] if s["kind"] in _SIGNAL_KINDS[kind]]
    votes = group_votes(res["signals"])
    lines = [f"{word}: {_SIGNAL_MEANING[kind]}."]
    if not sig:
        lines.append(f"- 지금: 이 분석 기간({period.label})에는 해당 신호가 없습니다.")
        return _blocks([[head], lines[:1], lines[1:]])
    s = sig[0]
    lines.append(f"- 지금: {s['name']}({s['value']}). 이 분석의 기준: {s['rule']}.")
    g = s["group"]
    note = (" 같은 묶음의 이동평균 배열과 같은 사실이라 표에 따로 세지 않습니다." if counted_elsewhere(s, res["signals"]) else "")
    lines.append(f"- 판정: 개별 신호로는 {_SIDE_WORD[s['side']]}이고, 이 신호가 속한 {g} 묶음은 {_SIDE_WORD[votes.get(g, NEUTRAL)]}입니다.{note} "
                 "방향 판정에는 묶음 표만 씁니다.")
    return _blocks([[head], lines[:1], lines[1:]])


def filing_answer(company, head: str, rows: list[dict] | None) -> str:
    """공시 날짜 질문(26차): 최신 실적 공시의 접수일·접수번호를 먼저. 거래소 잠정실적 공시는 접수일이 곧 발표일이다."""
    if not rows:
        return _blocks([[head], [f"{company.name}의 실적 공시를 찾지 못했습니다(수집된 정기보고서·잠정실적 공시 없음)."]])
    r = rows[0]
    pre = r.get("source") == "preliminary"
    kind = "잠정실적 공정공시" if pre else REPORT_NAMES.get(r.get("reprt_code"), "정기보고서")
    first = (f"{company.name}의 최신 실적 공시는 {r['disclosed_date']}에 접수된 {r['period_label']} {kind}입니다"
             f"(DART 접수번호 {r['rcept_no']}" + (", 거래소 공시라 접수일이 곧 발표일입니다)." if pre else ")."))
    lines = [f"- 이 공시의 실적: {earnings_row_text(r)}"]
    finals = [x for x in rows if x.get("source") == "final"]
    if pre:
        last = (f" 최근 정기보고서는 {finals[0]['period_label']}({REPORT_NAMES.get(finals[0].get('reprt_code'), '정기보고서')}, "
                f"{finals[0]['disclosed_date']} 접수)입니다." if finals else "")
        lines.append(f"- 정기보고서: {r['period_label']} 정기보고서는 아직 수집되지 않았습니다.{last}")
        c = r.get("corrected")
        lines.append(f"- 정정공시: {c['date'].isoformat()} 정정공시(DART 접수번호 {c['rcept_no']})가 있습니다." if c else "- 정정공시: 없습니다.")
    return _blocks([[head], [first], lines])


def followup_answer(company, period: Period, res: dict, kind: str, now: datetime, extra: dict | None = None) -> str:
    """후속 답(2026-11-04 25차): 첫 줄 제목에 방향, 둘째 줄 첫 문장이 방향(상승 쪽입니다/하락 쪽입니다/우세하지 않습니다/보류).
    국면은 첫 줄의 "현재는 …"로 따로 보인다. 질문 종류별로 필요한 줄만: 한 줄 1줄, 고르기 2줄, 판단 4줄, 그 밖 6줄 이하.
    kind: verdict/pick/why/oneline/compare/probability/buy/risk/earnings/data. extra: earnings·quarter·reaction(실적)."""
    extra = extra or {}
    ind, label, data = res["indicators"], res["label"], res["data"]
    regime = classify_regime(ind, label)
    if kind == "oneline":
        return oneline_answer(company, period, res, regime)
    t = vote_tally(res["signals"])
    base = ind["base_date"]
    fresh = data.get("freshness") or ""
    stale = f" · 시세: {fresh}, {base} 종가 기준" if fresh and fresh != "최신 시세" and kind != "data" else ""
    head = f"{direction_title(company, period, res)} · {state_line(regime, ind)}{stale}"
    first = direction_sentence(res, regime, period, pick=(kind == "pick"))
    ge = group_evidence(ind, res["signals"], period)
    # 26차: 근거는 묶음 판정과 같은 원천(상승 근거 = 상승 쪽 묶음, 위험 = 하락 쪽 묶음, 없으면 엇갈린 묶음·추세 참고로 따로)
    reasons = f"- 근거: {_support_text(ge, 1)} / 위험: {_risk_text(ge, 1)}"
    power = f"- 과거 검증: {power_sentence(period, label)}"
    level = f"- 확인할 가격대: {_level_line(ind, period)}"
    if kind == "pick":
        return _blocks([[head], [first]])
    if kind == "filing":
        return filing_answer(company, head, extra.get("earnings"))
    if kind == "signal":
        return signal_answer(head, res, period, extra.get("term"))
    if kind == "why":
        lines = [f"- 상승 근거: {_support_text(ge)}", f"- 하락 위험: {_risk_text(ge)}",
                 f"- 추세(따로 봄): 현재는 {regime}입니다. 추세는 방향 판정에 넣지 않습니다."]
        return _blocks([[head], [why_sentence(res, regime, period)], lines])
    if kind == "data":
        lines = [f"이 답변은 {now.astimezone(KST):%Y-%m-%d %H:%M} KST에 만들었고, 시세 기준일은 {base} 종가입니다.",
                 _price_status_line(data, base), f"- 답하는 방식: {DATA_POLICY}", f"- 방향: {first}"]
        return _blocks([[head], lines[:1], lines[1:]])
    if kind == "earnings":
        rows = extra.get("earnings")
        lead = f"{first} 실적은 신호 판정 표에 넣지 않아 최신 실적을 반영해도 방향 판단은 그대로입니다."
        if not rows:
            why = "ETF는 실적을 다루지 않습니다" if getattr(company, "asset_type", None) == "etf" else "매출액·영업이익 계정을 확인하지 못했습니다"
            return _blocks([[head], [lead], [f"- 실적 정보를 보여 주지 않습니다({why}).", power]])
        body = [f"- 최신 실적: {earnings_row_text(rows[0])}"]
        quarter = extra.get("quarter")
        asked = next((r for r in rows if (r["bsns_year"], r["quarter"]) == quarter), None) if quarter else None
        if quarter and asked is None:
            body.append(f"- {quarter[0]}년 {quarter[1]}분기 실적: 아직 공시(잠정·확정)가 없습니다.")
        elif asked is not None and asked is not rows[0]:
            body.append(f"- 물은 분기 실적: {earnings_row_text(asked)}")
        elif len(rows) > 1:
            body.append(f"- 직전 실적: {earnings_row_text(rows[1])}")
        react = _reaction_line(rows[0], extra.get("reaction"))
        body += ([react] if react else []) + [power]
        return _blocks([[head], [lead], body])
    if kind == "risk":
        lines = [f"- 하락 위험: {x}" for x in ge["down"][:2]] or ["- 하락 위험: 하락 쪽으로 판정된 묶음은 없습니다."]
        if ge["mixed"]:
            lines.append(f"- 하락 위험(엇갈린 묶음): {ge['mixed'][0]}")
        if ge["context"] and len(lines) < 3:
            lines.append(f"- 하락 위험(추세 참고, 신호 판정 표 밖): {'; '.join(ge['context'][:2])}")
        rg, r52 = ind["range"], ind.get("range52")
        levels = []
        if (ri := ind.get("regime_inputs") or {}).get("ma20") is not None:
            pos = "아래" if ind["close"] < ri["ma20"] else "위"
            levels.append(f"20일선 {_won(round(ri['ma20']))}(종가가 {pos})")
        levels.append(f"분석 기간 최저 종가 {_won(rg['low'])}({rg['low_date']}, 최근 {rg['days']}거래일 기준)")
        if r52:
            levels.append(f"52주 최저 종가 {_won(r52['low'])}({r52['low_date']}, 최근 {r52['days']}거래일 기준)")
        lines.append("- 확인할 가격대: " + ", ".join(levels))
        return _blocks([[head], [first], lines])
    if kind == "probability":
        r = _backtest_row(period, label) if label != LABEL_HOLD else None
        if r is not None:
            v = backtest_summary()["periods"][period.label]["valid"]
            freq = (f"- 과거 빈도: 검증 구간({v['first_date']}~{v['last_date']})에서 지금과 같은 신호 상태({label})였던 날의 "
                    f"{v['horizon']}거래일 뒤 상승 비율은 과거에 {r['up_rate'] * 100:.1f}%, 같은 기간 전체는 {r['base_rate'] * 100:.1f}%였습니다"
                    f"({_power_stats(r)}). 과거에 관측된 빈도이며 미래 확률이 아닙니다.")
        else:
            freq = "- 과거 빈도: 이 신호 상태의 과거 검증 결과가 없습니다."
        return _blocks([[head], [first], ["- 검증된 상승·하락 확률은 없어 가능성을 숫자로 말하지 않습니다.", freq, reasons, level]])
    if kind == "compare":
        return _blocks([[head], [first], [f"- {_compare_clause(t)}({tally_text(t)}), 국면은 '{regime}'입니다.", reasons, power]])
    if kind == "buy":
        return _blocks([[head], [first], ["- 매수·매도 여부는 권하지 않습니다. 이 방향은 현재 신호 상태이며 미래 수익을 보장하지 않습니다.",
                                          reasons, power]])
    return _blocks([[head], [first], [reasons, power]])


def _blocks(blocks: list[list[str]]) -> str:
    """빈 줄로 블록(문단·목록)을 나눈다. 프론트(markdownSections.toBlocks)는 블록 첫 줄이 "- "면 목록으로 그린다."""
    return "\n\n".join("\n".join(b) for b in blocks if b)


def no_data_answer(company) -> str:
    return (f"## 최종 신호 판단\n- {company.name}({company.ticker}): {LABEL_HOLD}\n"
            "- 신호를 계산할 시세 데이터가 부족합니다(2거래일 미만). 데이터가 쌓인 뒤 다시 질문해 주세요.")


# ---------- DB ----------


def price_rows(db, company, now: datetime) -> list[tuple]:
    """최근 PRICE_ROWS개 시세 [(date, close, volume)] 날짜 오름차순(장중 행 제외)."""
    from app.models import StockPrice

    rows = (db.query(StockPrice.price_date, StockPrice.close_price, StockPrice.volume)
            .filter(StockPrice.company_id == company.id)
            .order_by(StockPrice.price_date.desc()).limit(PRICE_ROWS).all())
    return list(reversed(drop_open_session(rows, now, lambda r: r[0])))


def analyze(db, company, period: Period, now: datetime | None = None, rows: list[tuple] | None = None) -> dict | None:
    """시세(장중 행 제외)·지수를 읽어 지표·신호·판정·데이터 점검을 만든다. 시세가 2거래일 미만이면 None."""
    from sqlalchemy import func

    from app.models import MarketIndex

    now = now or datetime.now(KST)
    rows = rows if rows is not None else price_rows(db, company, now)
    if len(rows) < 2:
        return None
    code = index_code_for_market(getattr(company, "market", None))
    # 거래일 누락 점검은 코스피 지수 달력으로 한다(ETF도 같은 거래소 달력)
    calendar = [d for (d,) in db.query(MarketIndex.price_date)
                .filter(MarketIndex.index_code == "KS11", MarketIndex.price_date >= rows[0][0], MarketIndex.price_date <= rows[-1][0])]
    have = {r[0] for r in rows}
    missing = sorted(d for d in calendar if d not in have)
    index_rows = None
    if code:
        index_rows = (db.query(MarketIndex.price_date, MarketIndex.close_price)
                      .filter(MarketIndex.index_code == code, MarketIndex.price_date >= rows[0][0], MarketIndex.price_date <= rows[-1][0])
                      .order_by(MarketIndex.price_date).all())
    ind = compute_indicators(rows, period, index_rows)
    if ind.get("relative"):
        ind["relative"].update(index_code=code, index_name=MARKET_INDEXES[code])
    elif code:
        # 지수 대비를 못 낸 이유를 날짜로 밝힌다: 지수 마지막 날짜가 종목 기준일보다 이르다(수집 도중 등)
        last = db.query(func.max(MarketIndex.price_date)).filter(MarketIndex.index_code == code).scalar()
        ind["relative_missing"] = {"index_name": MARKET_INDEXES[code], "index_last": last.isoformat() if last else None}
    signals = classify_signals(ind, period)
    data = data_check(rows[-1][0], now, missing, price_status(db, rows[-1][0], now))
    if calendar and calendar[-1] >= rows[-1][0] and rows[-1][0] not in set(calendar):
        data["warnings"].append(f"기준일({rows[-1][0].isoformat()})이 코스피 지수 거래일에 없어 실제 거래일인지 확인하지 못했습니다.")
    data["base_is_trading_day"] = rows[-1][0] in set(calendar) if calendar and calendar[-1] >= rows[-1][0] else None
    return {"indicators": ind, "signals": signals, "label": judge(signals, data), "data": data}


def fx_summary(db, period: Period, base_date: str) -> dict | None:
    """원/달러 환율(DB에 있는 범위): 기준일 이하 마지막 값과 분석 기간 전 값. 없으면 None."""
    from app.models import ExchangeRate

    rows = (db.query(ExchangeRate.price_date, ExchangeRate.close_price)
            .filter(ExchangeRate.pair_code == "USD/KRW", ExchangeRate.price_date <= date.fromisoformat(base_date))
            .order_by(ExchangeRate.price_date.desc()).limit(period.days + 1).all())
    if not rows:
        return None
    last = rows[0]
    out = {"date": last[0].isoformat(), "close": float(last[1])}
    if len(rows) == period.days + 1:
        out.update(from_date=rows[-1][0].isoformat(), pct=_pct(float(last[1]), float(rows[-1][1])))
    return out


def filter_titles(items: list[dict]) -> list[dict]:
    return [i for i in items if not _EXCLUDED_TITLE_RE.search(i.get("title") or "")]


def kst_date(value) -> str | None:
    """datetime 또는 ISO 문자열 -> 한국 시간 날짜(YYYY-MM-DD). 2026-11-04(26차): 공시는 한국 시간 자정(UTC 전날 15:00)으로 저장돼
    UTC 문자열 앞 10자를 쓰면 하루 이른 날짜가 나왔다(SK하이닉스 잠정실적 20261028800036이 "2026-10-27")."""
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        return dt.date().isoformat()
    return dt.astimezone(KST).date().isoformat()


def disclosure_date(source_url: str | None, disclosed_at) -> str | None:
    """공시 날짜 = DART 접수번호(앞 8자리)의 날짜. 접수번호가 없으면 한국 시간 접수일. 같은 공시는 답변 어디서나 이 날짜다."""
    m = re.search(r"rcptNo=(\d{8})", source_url or "")
    if m:
        d = m.group(1)
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return kst_date(disclosed_at)


def evidence(db, company, period: Period, now: datetime) -> tuple[list[dict], list[dict]]:
    """근거 뉴스·공시. 뉴스: 이 종목(company_id) 행 중 is_relevant가 False가 아닌 최근 NEWS_FETCH건 -> app.news_relevance.evidence_news
    (제목에 이 종목 이름, 최근 period.news_days일, 중복 제목 하나) -> 목표가·확률 제목 제외 -> key_news 먼저, NEWS_LIMIT건.
    공시: 이 종목(company_id)의 최근 period.news_days일 접수분 DISCLOSURE_LIMIT건(ETF는 공시를 수집하지 않는다)."""
    from app.models import Company, Disclosure, News
    from app.news_relevance import evidence_news

    since = now - timedelta(days=period.news_days)
    rows = (db.query(News).filter(News.company_id == company.id, News.is_relevant.isnot(False))
            .order_by(News.published_at.desc()).limit(NEWS_FETCH).all())
    items = [{"company_name": company.name, "title": r.title, "source": r.source, "url": r.url,
              "published_at": r.published_at.isoformat() if r.published_at else None, "date": kst_date(r.published_at)} for r in rows]
    others = ([n for (n,) in db.query(Company.name).filter(Company.asset_type == "etf", Company.id != company.id)]
              if getattr(company, "asset_type", None) == "etf" else [])
    found = filter_titles(evidence_news(company.name, company.asset_type, items, since=since, other_etf_names=others))
    # 2026-11-04: 실적·컨콜·자사주 같은 기사(key_news)를 먼저 고르고 나머지는 최근 순으로 채운다. 표시는 발행일 내림차순이다.
    key = key_news(found)
    rest = [n for n in found if n not in key][:max(0, NEWS_LIMIT - len(key))]
    news = sorted(key + rest, key=lambda n: n.get("published_at") or "", reverse=True)
    disclosures = []
    if getattr(company, "asset_type", None) != "etf":
        drows = (db.query(Disclosure).filter(Disclosure.company_id == company.id, Disclosure.disclosed_at >= since)
                 .order_by(Disclosure.disclosed_at.desc()).limit(DISCLOSURE_LIMIT).all())
        disclosures = [{"company_name": company.name, "title": d.title, "disclosure_type": d.disclosure_type,
                        "disclosed_at": d.disclosed_at.isoformat() if d.disclosed_at else None, "source_url": d.source_url,
                        "date": disclosure_date(d.source_url, d.disclosed_at)}
                       for d in drows]
    return news, disclosures
