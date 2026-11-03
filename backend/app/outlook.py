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
from app.briefing import KST, drop_open_session, session_is_open
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
SIGNAL_GROUPS: dict[str, str] = {
    "close_vs_ma_short": "단기 흐름", "period_return": "단기 흐름", "relative": "단기 흐름",
    "close_vs_ma_long": "중기 추세", "ma_alignment": "중기 추세",
    "cross": "이동평균 교차", "rsi": "RSI", "macd": "MACD", "volume": "거래량",
}
GROUP_LABELS: dict[str, str] = {
    "단기 흐름": "단기 흐름(단기 이동평균 위치·기간 수익률·지수 대비)",
    "중기 추세": "중기 추세(장기 이동평균 위치·이동평균 배열)",
    "이동평균 교차": "이동평균 교차", "RSI": "RSI(14)", "MACD": "MACD(12,26,9)", "거래량": "거래량",
}
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
NO_PROBABILITY = "검증된 상승·하락 확률이나 기대수익률은 계산하지 않습니다. 대신 현재 신호의 개수로 답합니다."
ASK_SUBJECT_ANSWER = ("질문에서 종목을 찾지 못했습니다. 어떤 종목에 대한 질문인지 종목명 또는 종목코드로 알려주세요. "
                      "예: \"SK하이닉스 일주일 안에 오를까?\"")
JUDGE_RULE = (f"성격이 겹치는 지표를 묶어 묶음당 1표로 세고, 상승·하락 표 차이가 {GROUP_MARGIN}표 이상이면 많은 쪽의 우위, 그보다 작으면 중립. "
              f"계산한 묶음이 {MIN_GROUPS}개 미만이거나 마지막 시세가 {MAX_LAG_BUSINESS_DAYS}영업일을 넘게 늦거나 거래일이 빠졌으면 판단 유보")

# ---------- 질문 판별 ----------

_FORECAST_RE = re.compile(
    r"오를까|오를지|오르려나|오르나요|오를\s*(?:것|거)\s*같|오를\s*수\s*있을|올라갈까|올라갈지|오른다고|"
    r"내릴까|내릴지|내려갈까|내려갈지|내린다고|떨어질까|떨어질지|빠질까|빠질지|하락할까|상승할까|반등할까|"
    r"전망|앞으로|향후|어떻게\s*될|어떨\s*(?:것|거)\s*같|어떨까|방향|"
    r"사도\s*(?:될|돼|되|괜찮)|살까|사야\s*(?:할까|하나|돼|되나)|매수해도|매수할까|매수하는\s*게|들어가도|팔까|팔아야\s*(?:할까|하나)|"
    r"매도할까|매도해도|위험할까|위험한가|위험해\?"
)
# 직전 종목을 이어받는 결론·판단·확률·위험 표현. "그래서/그럼"만으로는 후속 결론이 아니다("그럼 최근 뉴스는?").
_VERDICT_RE = re.compile(
    r"결론|한마디로|요약|정리하면|정리해|올라|내려|오른다는|내린다는|오를\s*거|내릴\s*거|상승이야|하락이야|상승이냐|하락이냐|"
    r"생각에|생각은|판단으로|판단은|어떻게\s*해야|어떻게\s*하라|어떡해|사\s*\?*\s*말아|살까\s*말까|어느\s*쪽|"
    r"가능성|확률|위험|리스크"
)
_RISK_RE = re.compile(r"위험|리스크|손실|낙폭")
_PROBABILITY_RE = re.compile(r"가능성|확률|몇\s*(?:%|퍼센트|프로)")
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
    ("3개월", re.compile(r"3\s*개월|석\s*달|세\s*달|분기|90\s*일")),
    ("1개월", re.compile(r"1\s*개월|한\s*달|다음\s*달|이번\s*달|한달|30\s*일|4\s*주")),
    ("1주일", re.compile(r"일주일|1\s*주일|한\s*주|다음\s*주|이번\s*주|1\s*주|7\s*일|며칠")),
)
# 이 경로가 만든 답변의 첫 항목: "- 이름(종목코드), 분석 기간 1주일(5거래일): 라벨". 후속 질문이 기간(과 종목)을 이어받는 표식이다.
_MARKER_RE = re.compile(r"^- (?P<name>.+?)\((?P<ticker>[0-9A-Z]{6})\), 분석 기간 (?P<period>1주일|1개월|3개월)\(", re.MULTILINE)


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


def is_forecast_question(question: str) -> bool:
    q = question or ""
    return bool(_FORECAST_RE.search(q)) and not _excluded(q)


def is_followup_verdict_question(question: str) -> bool:
    q = question or ""
    return bool(_VERDICT_RE.search(q)) and not _excluded(q)


def followup_kind(question: str) -> str:
    q = question or ""
    if _PROBABILITY_RE.search(q):
        return "probability"
    if _RISK_RE.search(q):
        return "risk"
    return "verdict"


def asks_subject(question: str) -> bool:
    """종목 없이 결론·방향만 묻는 질문인가("그래서 결론이 뭐야?"). 시장·지수·내 포트폴리오 질문은 아니다."""
    q = question or ""
    return (is_forecast_question(q) or is_followup_verdict_question(q)) and not _MARKET_WORD_RE.search(q)


def period_in(question: str) -> str | None:
    for label, rx in _PERIOD_PATTERNS:
        if rx.search(question or ""):
            return label
    return None


def marker_of(answer: str | None) -> dict | None:
    """이 경로가 만든 답변이면 {"name", "ticker", "period"}, 아니면 None."""
    m = _MARKER_RE.search(answer or "")
    return m.groupdict() if m else None


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
    window = list(zip(days, closes))[-(period.range_days + 1):]
    hi_d, hi = max(window, key=lambda x: (x[1], x[0]))
    lo_d, lo = min(window, key=lambda x: (x[1], -x[0].toordinal()))
    out["range"] = {"days": len(window), "from_date": window[0][0].isoformat(), "to_date": window[-1][0].isoformat(),
                    "high": hi, "high_date": hi_d.isoformat(), "from_high_pct": _pct(close, hi),
                    "low": lo, "low_date": lo_d.isoformat(), "from_low_pct": _pct(close, lo)}
    prior = [v for v in volumes[-1 - VOLUME_AVG_DAYS:-1] if v is not None]
    if volumes[-1] is not None and len(prior) == VOLUME_AVG_DAYS and sum(prior) > 0:
        avg = sum(prior) / VOLUME_AVG_DAYS
        out["volume"] = {"value": int(volumes[-1]), "avg": round(avg), "ratio": round(volumes[-1] / avg, 2)}
    if index_rows:
        cmp = compare_returns(list(zip(days, closes)), [(d, float(c)) for d, c in index_rows], period.days)
        if cmp is not None and cmp["end_date"] == out["base_date"]:
            out["relative"] = cmp
    return out


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
        tally[g] = tally.get(g, 0) + (1 if s["side"] == UP else -1 if s["side"] == DOWN else 0)
    return {g: UP if v > 0 else DOWN if v < 0 else NEUTRAL for g, v in tally.items()}


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


def expected_trading_day(now: datetime) -> date:
    """지금(KST) 기준으로 종가가 있어야 할 가장 최근 평일. 장중(평일 15:30 전)이면 직전 평일."""
    today = now.astimezone(KST).date()
    d = last_business_day(today)
    if d == today and session_is_open(now):
        d = last_business_day(today - timedelta(days=1))
    return d


def data_check(base_date: date, now: datetime, missing_days: list[date]) -> dict:
    """데이터 점검: 늦은 영업일 수, 경고 문장, 판단 유보 여부."""
    expected = expected_trading_day(now)
    lag = business_days_behind(base_date, expected)
    warnings = []
    if 1 <= lag <= MAX_LAG_BUSINESS_DAYS:
        warnings.append(f"마지막 시세일({base_date.isoformat()})이 기대 거래일({expected.isoformat()})보다 {lag}영업일 늦습니다. "
                        "그 뒤의 가격 움직임은 반영되지 않았습니다.")
    elif lag > MAX_LAG_BUSINESS_DAYS:
        warnings.append(f"마지막 시세일({base_date.isoformat()})이 기대 거래일({expected.isoformat()})보다 {lag}영업일 늦어 "
                        "데이터가 부족합니다. 판단을 유보합니다.")
    if missing_days:
        shown = ", ".join(d.isoformat() for d in missing_days[:3]) + (" 등" if len(missing_days) > 3 else "")
        warnings.append(f"계산 구간에 지수에는 있는 거래일 {len(missing_days)}일의 시세가 없습니다({shown}). 판단을 유보합니다.")
    return {"expected_date": expected.isoformat(), "lag_business_days": lag, "missing_days": len(missing_days),
            "warnings": warnings, "hold": lag > MAX_LAG_BUSINESS_DAYS or bool(missing_days)}


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
        members = ", ".join(f"{s['name']}({_SIDE_WORD[s['side']]})" for s in signals if s["group"] == g)
        lines.append(f"  - {GROUP_LABELS[g]}: {_SIDE_WORD[votes[g]]} 1표 ← {members}")
    return lines


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


def indicator_lines(ind: dict, period: Period) -> list[str]:
    lines = []
    for n, r in ind["returns"].items():
        lines.append(f"- {n}거래일 수익률: {_signed(r['pct'])} ({r['from_date']} 종가 {_won(r['from_close'])} 대비 "
                     f"{ind['base_date']} 종가 {_won(ind['close'])})")
    if ind.get("day_change_pct") is not None:
        lines.append(f"- 기준일 하루 등락률: {_signed(ind['day_change_pct'])} ({ind['prev_date']} 종가 대비)")
    for n in period.ma:
        ma = ind.get(f"ma{n}")
        if ma:
            side = "위" if ma["gap_pct"] > 0 else ("아래" if ma["gap_pct"] < 0 else "같은 수준")
            lines.append(f"- {n}일 이동평균: {_won(round(ma['value']))} (종가가 {abs(ma['gap_pct']):.2f}% {side}, {ind['base_date']} 기준)")
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
    lines.append(f"- 최고 종가 대비: {_signed(rg['from_high_pct'])} (조회 기간 {rg['from_date']}~{rg['to_date']}, "
                 f"최고 종가 {_won(rg['high'])}, {rg['high_date']})")
    lines.append(f"- 최저 종가 대비: {_signed(rg['from_low_pct'])} (조회 기간 {rg['from_date']}~{rg['to_date']}, "
                 f"최저 종가 {_won(rg['low'])}, {rg['low_date']})")
    return lines


def market_lines(company, ind: dict, fx: dict | None) -> list[str]:
    lines = []
    rel = ind.get("relative")
    if rel:
        lines.append(f"- {rel['index_name']} 대비({rel['period_days']}거래일, {rel['start_date']} 종가 대비 {rel['end_date']}): "
                     f"{company.name} {_signed(rel['stock_change_pct'])}, {rel['index_name']} {_signed(rel['market_change_pct'])}, "
                     f"차이 {rel['diff_pct_point']:+.2f}%p")
    elif getattr(company, "asset_type", None) == "etf":
        lines.append("- 지수 대비: ETF라 상장 시장 지수와 비교하지 않았습니다.")
    else:
        lines.append("- 지수 대비: 같은 기간의 지수 데이터가 없습니다.")
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
        lines.append(f"- 뉴스 {(n.get('published_at') or '')[:10]} {src}\"{n['title']}\"라는 제목의 기사가 있었습니다.")
    if not items:
        lines.append(f"- 최근 {period.news_days}일 안에 {topic} 관련 기사를 찾지 못했습니다.")
    return lines


def earnings_lines(rows: list[dict] | None) -> list[str]:
    """실적(참고, 신호 판정에 쓰지 않음). rows는 app.earnings.recent_for_answer 결과. None이면 섹션 생략."""
    if rows is None:
        return []
    if not rows:
        return ["- 실적 데이터 없음(수집된 DART 주요 계정이 없습니다)."]
    lines = ["- 신호 판정에는 쓰지 않는 참고 정보입니다. 분기보고서는 그 분기(3개월) 금액, 사업보고서는 연간 금액이고, "
             "전년 동기는 같은 보고서에 적힌 전기 금액입니다."]
    for r in rows:
        parts = []
        for key, name in (("revenue", "매출액"), ("operating_income", "영업이익"), ("net_income", "당기순이익")):
            cur, prev = r.get(key), r.get(f"{key}_prev")
            if cur is None:
                continue
            yoy = f", 전년 동기 대비 {(cur / prev - 1) * 100:+.1f}%" if prev is not None and prev > 0 and cur >= 0 else ""
            parts.append(f"{name} {_eok(cur)}{yoy}")
        basis = "연결" if r["fs_div"] == "CFS" else "별도"
        lines.append(f"- {r['period_label']}({basis}): " + "; ".join(parts) +
                     f" (접수일 {r['disclosed_date']}, DART 접수번호 {r['rcept_no']})")
    return lines


def _eok(v: float) -> str:
    """원 단위 금액을 억 원 단위로(소수 없이)."""
    return f"{v / 1e8:,.0f}억 원"


def evidence_lines(news: list[dict], disclosures: list[dict], is_etf: bool, period: Period) -> list[str]:
    lines = []
    for n in news:
        src = f"({n['source']}) " if n.get("source") else ""
        lines.append(f"- 뉴스 {(n.get('published_at') or '')[:10]} {src}\"{n['title']}\"라는 제목의 기사가 있었습니다.")
    if not news and not is_etf:
        lines.append(f"- 관련 뉴스를 찾지 못했습니다(최근 {period.news_days}일, 제목에 종목 이름이 있는 기사 기준).")
    for d in disclosures:
        lines.append(f"- 공시 {(d.get('disclosed_at') or '')[:10]} 접수: \"{d['title']}\"")
    if not disclosures and not is_etf:
        lines.append(f"- 최근 {period.news_days}일 안에 접수된 공시를 찾지 못했습니다.")
    return lines


def price_level_lines(ind: dict, period: Period) -> list[str]:
    lines = [f"- {n}일 이동평균: {_won(round(ind[f'ma{n}']['value']))}" for n in period.ma if ind.get(f"ma{n}")]
    rg = ind["range"]
    lines.append(f"- 조회 기간({rg['from_date']}~{rg['to_date']}) 최고 종가: {_won(rg['high'])} ({rg['high_date']})")
    lines.append(f"- 조회 기간({rg['from_date']}~{rg['to_date']}) 최저 종가: {_won(rg['low'])} ({rg['low_date']})")
    return lines


def limit_lines(company) -> list[str]:
    lines = ["- 위 신호는 과거 시세에서 계산한 것이며 미래 방향을 보장하지 않습니다. 실적 발표·공시·시장 이벤트로 방향이 바뀔 수 있습니다.",
             f"- {NO_PROBABILITY}"]
    if getattr(company, "asset_type", None) == "etf" and (kind := leverage_kind(company.name)):
        lines.append("- " + leverage_notice(company.name, kind).lstrip("※ "))
    lines.append(f"- {DISCLAIMER}")
    return lines


def source_lines(ind: dict, data: dict, period: Period, now: datetime) -> list[str]:
    lines = [f"- 기준일(최신 거래일): {ind['base_date']}, 오늘(Asia/Seoul): {now.astimezone(KST).date().isoformat()}, "
             f"기대 거래일: {data['expected_date']}"]
    lines += [f"- 경고: {w}" for w in data["warnings"]]
    lines.append("- 수익률 계산식: (기준일 종가 ÷ N거래일 전 종가 - 1) × 100. 최고·최저 종가 대비도 같은 식이며 수익률이 아닙니다.")
    lines.append(f"- 지표 계산에 쓴 가격 범위: {ind['first_date']}~{ind['base_date']} 종가 {ind['n_rows']}거래일")
    lines.append(f"- 근거 뉴스·공시 범위: 최근 {period.news_days}일 발행·접수분")
    lines.append("- 출처: 시세 stock_price(종가), 지수 market_index, 환율 exchange_rate, 뉴스 news(발행일), 공시 disclosure(DART 접수일), "
                 "실적 quarterly_earnings(DART 주요 계정), 과거 검증 app/data/outlook_backtest.json")
    return lines


def _join(sections: list[tuple[str, list[str]]]) -> str:
    return "\n\n".join(f"## {title}\n" + "\n".join(lines) for title, lines in sections if lines)


def forecast_answer(company, period: Period, res: dict, news: list[dict], disclosures: list[dict], fx: dict | None,
                    now: datetime, indirect: tuple | None = None, earnings: list[dict] | None = None) -> str:
    """indirect = (섹션 이름, 업종/기초지수, 기사들) 또는 None, earnings = 실적 행 목록(None이면 섹션 생략)."""
    ind, sig, label, data = res["indicators"], res["signals"], res["label"], res["data"]
    is_etf = getattr(company, "asset_type", None) == "etf"
    head = [_marker_line(company, period, label), f"- {NOT_A_FORECAST}"]
    head += [f"- 데이터 경고: {w}" for w in data["warnings"]]
    section, topic, items = indirect or (None, None, [])
    direct = evidence_lines(news, disclosures, is_etf, period)
    if is_etf and not news and section is not None:
        direct = ["- 제목에 이 ETF 이름이 있는 기사를 찾지 못했습니다(간접 관련 기사는 아래 섹션에 따로 있습니다)."]
    sections = [
        ("최종 신호 판단", head),
        ("판단 근거 요약", [*group_lines(sig), _count_line(sig), backtest_line(period, label), f"- 판정 규칙: {JUDGE_RULE}"]),
        ("상승 쪽 신호", _side_lines(sig, UP)),
        ("하락 쪽 신호", _side_lines(sig, DOWN)),
        ("기술적 지표", indicator_lines(ind, period)),
        ("뉴스·공시(직접 관련)", direct),
        (section or "", indirect_lines(section, topic, items, period)),
        ("실적(참고)", earnings_lines(earnings)),
        ("시장 상황", market_lines(company, ind, fx)),
        ("확인할 가격대", price_level_lines(ind, period)),
        ("불확실성과 한계", limit_lines(company)),
        ("데이터 기준일·출처", source_lines(ind, data, period, now)),
    ]
    return _join(sections)


def followup_answer(company, period: Period, res: dict, kind: str, now: datetime) -> str:
    ind, sig, label, data = res["indicators"], res["signals"], res["label"], res["data"]
    head = [_marker_line(company, period, label), f"- 직전 질문의 종목·기간을 이어받아 같은 규칙으로 다시 계산했습니다. {NOT_A_FORECAST}"]
    head += [f"- 데이터 경고: {w}" for w in data["warnings"]]
    ups = ", ".join(s["name"] for s in sig if s["side"] == UP) or "없음"
    downs = ", ".join(s["name"] for s in sig if s["side"] == DOWN) or "없음"
    summary = [*group_lines(sig), _count_line(sig), f"- 상승 쪽: {ups}", f"- 하락 쪽: {downs}", backtest_line(period, label),
               f"- 판정 규칙: {JUDGE_RULE}"]
    if kind == "probability":
        summary.insert(0, f"- {NO_PROBABILITY}")
    sections = [("최종 신호 판단", head), ("판단 근거 요약", summary)]
    if kind == "risk":
        rg = ind["range"]
        risk = [f"- 최고 종가 대비: {_signed(rg['from_high_pct'])} (조회 기간 {rg['from_date']}~{rg['to_date']}, 최고 종가 {_won(rg['high'])}, {rg['high_date']})"]
        if ind.get("volatility") is not None:
            risk.append(f"- 변동성: 최근 {VOLATILITY_DAYS}거래일 일간 수익률 표준편차 {ind['volatility']:.2f}%")
        worst = ind["returns"].get(period.days)
        if worst:
            risk.append(f"- {period.days}거래일 수익률: {_signed(worst['pct'])} ({worst['from_date']} 종가 대비)")
        sections += [("하락 쪽 신호", _side_lines(sig, DOWN)), ("위험 지표", risk), ("확인할 가격대", price_level_lines(ind, period))]
    sections += [("불확실성과 한계", limit_lines(company)), ("데이터 기준일·출처", source_lines(ind, data, period, now))]
    return _join(sections)


def no_data_answer(company) -> str:
    return (f"## 최종 신호 판단\n- {company.name}({company.ticker}): {LABEL_HOLD}\n"
            "- 신호를 계산할 시세 데이터가 부족합니다(2거래일 미만). 데이터가 쌓인 뒤 다시 질문해 주세요.")


# ---------- DB ----------


def analyze(db, company, period: Period, now: datetime | None = None) -> dict | None:
    """시세(장중 행 제외)·지수를 읽어 지표·신호·판정·데이터 점검을 만든다. 시세가 2거래일 미만이면 None."""
    from app.models import MarketIndex, StockPrice

    now = now or datetime.now(KST)
    rows = (db.query(StockPrice.price_date, StockPrice.close_price, StockPrice.volume)
            .filter(StockPrice.company_id == company.id)
            .order_by(StockPrice.price_date.desc()).limit(PRICE_ROWS).all())
    rows = list(reversed(drop_open_session(rows, now, lambda r: r[0])))
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
    signals = classify_signals(ind, period)
    data = data_check(rows[-1][0], now, missing)
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


def evidence(db, company, period: Period, now: datetime) -> tuple[list[dict], list[dict]]:
    """근거 뉴스·공시. 뉴스: 이 종목(company_id) 행 중 is_relevant가 False가 아닌 최근 NEWS_FETCH건 -> app.news_relevance.evidence_news
    (제목에 이 종목 이름, 최근 period.news_days일, 중복 제목 하나) -> 목표가·확률 제목 제외 -> NEWS_LIMIT건.
    공시: 이 종목(company_id)의 최근 period.news_days일 접수분 DISCLOSURE_LIMIT건(ETF는 공시를 수집하지 않는다)."""
    from app.models import Company, Disclosure, News
    from app.news_relevance import evidence_news

    since = now - timedelta(days=period.news_days)
    rows = (db.query(News).filter(News.company_id == company.id, News.is_relevant.isnot(False))
            .order_by(News.published_at.desc()).limit(NEWS_FETCH).all())
    items = [{"company_name": company.name, "title": r.title, "source": r.source, "url": r.url,
              "published_at": r.published_at.isoformat() if r.published_at else None} for r in rows]
    others = ([n for (n,) in db.query(Company.name).filter(Company.asset_type == "etf", Company.id != company.id)]
              if getattr(company, "asset_type", None) == "etf" else [])
    news = filter_titles(evidence_news(company.name, company.asset_type, items, since=since, other_etf_names=others))[:NEWS_LIMIT]
    disclosures = []
    if getattr(company, "asset_type", None) != "etf":
        drows = (db.query(Disclosure).filter(Disclosure.company_id == company.id, Disclosure.disclosed_at >= since)
                 .order_by(Disclosure.disclosed_at.desc()).limit(DISCLOSURE_LIMIT).all())
        disclosures = [{"company_name": company.name, "title": d.title, "disclosure_type": d.disclosure_type,
                        "disclosed_at": d.disclosed_at.isoformat() if d.disclosed_at else None, "source_url": d.source_url}
                       for d in drows]
    return news, disclosures
