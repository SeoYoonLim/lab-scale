"""최근 N거래일 주가 흐름 질문(수익률·이동평균·거래량·국면)을 코드가 계산해 답한다(2026-11-06).

"삼성전자의 최근 20거래일 주가 흐름을 분석해 줘. 1일·5일·20일 수익률, 5일·20일 이동평균, 최근 거래량과 20거래일 평균 거래량을
비교해 줘 ... 어떤 국면인지" 같은 질문이 LLM 경로로 가서, 기간 해석이 "1일·5일·20일 수익률"의 "1일"을 조회 기간으로 읽고
1일 시세만 조회했다. LLM이 쓴 5·20일 값은 도구 결과에 없어 숫자 검증이 1일 요약으로 바꿨고, 요청한 항목이 모두 빠졌다.

이 경로는 이동평균을 직접 묻는(또는 수익률 창을 둘 이상 나열한) 종목 하나짜리 질문만 받는다. 값은 저장된 일별 시세
(outlook.price_rows: 장중 행 제외)로 계산하고, 기준일·지연·누락 점검과 국면은 전망 경로와 같은 함수
(outlook.analyze의 data_check, classify_regime)를 쓴다. 수익률·이동평균은 행(거래일) 수 기준이다.
"""

from __future__ import annotations

import re
from datetime import datetime

from app import outlook as o
from app.backtest import sma
from app.briefing import KST

DEFAULT_RETURNS = (1, 5, 20)
DEFAULT_MAS = (5, 20)
DEFAULT_VOLUME_AVG = 20
DEFAULT_WINDOW = 20
MAX_WINDOW = 120  # 120일선까지(시세는 outlook.PRICE_ROWS=260행까지 읽는다)

_MA_RE = re.compile(r"이동\s*평균|이평선?|(?<!\d)\d{1,3}\s*일\s*선")
_NUM_LIST = r"(?:\d{1,3}\s*(?:거래)?일\s*(?:[·,/、]|와|과|및|그리고)?\s*)+"
_RETURNS_RE = re.compile(rf"({_NUM_LIST})(?:간\s*|동안\s*|의\s*)?(?:누적\s*)?수익률")
_MAS_RE = re.compile(rf"({_NUM_LIST})(?:이동\s*평균|이평)")
_MA_LINE_RE = re.compile(r"(?<!\d)(\d{1,3})\s*일\s*선")
_VOLUME_AVG_RE = re.compile(r"(?<!\d)(\d{1,3})\s*(?:거래)?일\s*(?:간\s*)?평균\s*거래량")
_WINDOW_RE = re.compile(r"최근\s*(\d{1,3})\s*(?:거래일|영업일|일)")
_NUM_RE = re.compile(r"(\d{1,3})\s*(?:거래)?일")
_WANTS_RE = re.compile(r"수익률|거래량|국면|추세|흐름|분석")
_CAUSE_RE = re.compile(r"왜|이유|원인|때문")

# 사용자가 고르는 국면 이름(질문 예: 상승 추세, 하락 추세, 횡보, 단기 반등 시도, 근거가 부족하면 추세 미확정)
_REGIME_WORD = {
    o.REGIME_REBOUND: "단기 반등 시도",
    o.REGIME_UP: "상승 추세",
    o.REGIME_PULLBACK: "상승 추세(조정 중)",
    o.REGIME_DOWN: "하락 추세",
    o.REGIME_FLAT: "횡보(방향 불분명)",
    o.REGIME_HOLD: "추세 미확정",
}
_REGIME_WHY = {
    o.REGIME_REBOUND: (f"20거래일 수익률 {o.REGIME_DROP20_PCT:.0f}% 이하 또는 60거래일 {o.REGIME_DROP60_PCT:.0f}% 이하의 큰 하락 뒤 "
                       f"반등 신호(5거래일 수익률 > 0, 5일선 > 20일선, 20거래일 최저 종가 대비 +{o.REGIME_REBOUND_FROM_LOW_PCT:.0f}% 이상 중 하나)가 "
                       "있지만 종가가 20일선이나 60일선 아래라서 추세 전환은 확인되지 않았습니다."),
    o.REGIME_DOWN: (f"종가 < 20일선 < 60일선이고 20거래일 수익률 -{o.REGIME_TREND20_PCT:.0f}% 이하 또는 "
                    f"60거래일 -{o.REGIME_TREND60_PCT:.0f}% 이하입니다."),
    o.REGIME_PULLBACK: (f"60거래일 수익률 +{o.REGIME_TREND60_PCT:.0f}% 이상, 종가 > 60일선, 20일선 > 60일선인 상승 추세에서 "
                        f"종가가 20일선 아래이거나 5거래일 수익률이 {o.REGIME_DIP5_PCT:.0f}% 이하입니다."),
    o.REGIME_UP: (f"종가 > 20일선 > 60일선이고 20거래일 수익률 +{o.REGIME_TREND20_PCT:.0f}% 이상 또는 "
                  f"60거래일 +{o.REGIME_TREND60_PCT:.0f}% 이상입니다."),
    o.REGIME_FLAT: "상승 추세·하락 추세·반등 시도 조건에 모두 해당하지 않아 방향이 분명하지 않습니다.",
}
_HOLD_WHY = {
    "data": "데이터 경고 때문에 판단하지 않습니다.",
    "rows": "60일선·60거래일 수익률을 계산할 시세가 부족해 판단하지 않습니다.",
    "signals": "판단에 쓸 지표 묶음이 부족해 판단하지 않습니다.",
}
_REGIME_NEED = ("r5", "r20", "r60", "ma5", "ma20", "ma60")


def hold_reason(res: dict) -> str:
    """국면 판단 유보 이유: 데이터 경고(지연·누락) > 60거래일 시세 부족 > 지표 묶음 부족(판정 라벨이 판단 유보)."""
    ri = res["indicators"].get("regime_inputs") or {}
    if res["data"].get("hold"):
        return "data"
    if any(ri.get(k) is None for k in _REGIME_NEED):
        return "rows"
    return "signals"


NOT_A_FORECAST ="현재까지의 시세를 정리한 것이며 미래 가격을 예측하거나 매매를 권유하는 것이 아닙니다."


def is_price_flow_question(question: str) -> bool:
    """이동평균을 직접 묻거나 수익률 창을 둘 이상 나열하고, 수익률·거래량·국면·흐름도 묻는 질문. 원인·전망 질문은 아니다."""
    q = question or ""
    if _CAUSE_RE.search(q) or o.is_forecast_question(q) or o.is_order_request(q):
        return False
    many_returns = any(len(_nums(m.group(1))) >= 2 for m in _RETURNS_RE.finditer(q))
    return bool((_MA_RE.search(q) and _WANTS_RE.search(q)) or many_returns)


def _nums(text: str) -> list[int]:
    return [int(n) for n in _NUM_RE.findall(text)]


def _uniq(ns) -> tuple[int, ...]:
    return tuple(sorted({n for n in ns if 1 <= n <= MAX_WINDOW}))


def windows(question: str) -> dict:
    """질문이 말한 창. 말하지 않은 항목은 기본값(수익률 1·5·20거래일, 5·20일선, 직전 20거래일 평균 거래량, 최근 20거래일)."""
    q = question or ""
    rets = _uniq(n for m in _RETURNS_RE.finditer(q) for n in _nums(m.group(1)))
    mas = _uniq([n for m in _MAS_RE.finditer(q) for n in _nums(m.group(1))] + [int(n) for n in _MA_LINE_RE.findall(q)])
    vol = _VOLUME_AVG_RE.search(q)
    win = _WINDOW_RE.search(q)
    window = int(win.group(1)) if win and 2 <= int(win.group(1)) <= MAX_WINDOW else DEFAULT_WINDOW
    vol_n = int(vol.group(1)) if vol and 1 <= int(vol.group(1)) <= MAX_WINDOW else DEFAULT_VOLUME_AVG
    return {"returns": rets or DEFAULT_RETURNS, "mas": mas or DEFAULT_MAS, "volume_avg": vol_n, "window": window}


def compute(rows: list[tuple], w: dict) -> dict:
    """rows: 날짜 오름차순 [(date, close, volume)]. 행이 모자란 창은 값 대신 None(답에서 '계산할 시세 부족')."""
    days = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    vols = [r[2] for r in rows]
    close = closes[-1]
    out = {"base_date": days[-1].isoformat(), "close": close, "n_rows": len(rows), "returns": {}, "mas": {}}
    for n in w["returns"]:
        out["returns"][n] = ({"pct": o._pct(close, closes[-1 - n]), "from_date": days[-1 - n].isoformat(),
                              "from_close": closes[-1 - n]} if len(rows) > n else None)
    for n in w["mas"]:
        v = sma(closes, n)
        out["mas"][n] = {"value": round(v, 2), "gap_pct": o._pct(close, v)} if v is not None else None
    n = w["volume_avg"]
    prior = vols[-1 - n:-1]
    if vols[-1] is not None and len(prior) == n and all(v is not None for v in prior) and sum(prior) > 0:
        avg = sum(prior) / n
        out["volume"] = {"value": int(vols[-1]), "avg": round(avg), "ratio": round(vols[-1] / avg, 2), "days": n,
                         "from_date": days[-1 - n].isoformat(), "to_date": days[-2].isoformat()}
    else:
        out["volume"] = {"value": int(vols[-1]) if vols[-1] is not None else None, "avg": None, "days": n}
    k = min(w["window"], len(rows) - 1)
    seg = list(zip(days, closes))[-(k + 1):]
    hi_d, hi = max(seg, key=lambda x: (x[1], x[0]))
    lo_d, lo = min(seg, key=lambda x: (x[1], -x[0].toordinal()))
    out["window"] = {"days": k, "asked": w["window"], "from_date": seg[0][0].isoformat(), "high": hi, "high_date": hi_d.isoformat(),
                     "low": lo, "low_date": lo_d.isoformat(), "pct": o._pct(close, seg[0][1])}
    return out


def _won(v: float) -> str:
    return o._won(v)


def _ratio(r: float) -> str:
    return f"{r:.2f}배"


def _gap(pct: float | None) -> str:
    if pct is None:
        return ""
    if abs(pct) < 0.005:
        return "(종가와 같음)"
    return f"(종가가 {abs(pct):.2f}% {'위' if pct > 0 else '아래'})"


def answer(company, flow: dict, res: dict, now: datetime) -> str:
    """기준일·지연 -> 수익률 -> 이동평균 -> 거래량 -> 기간 범위 -> 국면 -> 안내."""
    data, ind = res["data"], res["indicators"]
    regime = o.classify_regime(ind, res["label"])
    lines = [f"{company.name}({company.ticker}) · 최근 {flow['window']['asked']}거래일 주가 흐름", ""]
    lines.append(f"- 데이터 기준일: {flow['base_date']} 종가({data['freshness']}, 기대 거래일 {data['expected_date']}) · "
                 f"답변 생성 {now.astimezone(KST):%Y-%m-%d %H:%M} KST")
    lines += [f"- 데이터 경고: {x}" for x in data["warnings"]]
    if not data["warnings"]:
        lines.append("- 최신 데이터 지연: 없음")

    lines += ["", f"## 수익률(기준일 종가 {_won(flow['close'])})"]
    for n, r in flow["returns"].items():
        if r is None:
            lines.append(f"- {n}거래일: 계산할 시세 부족(시세 {flow['n_rows']}거래일)")
        else:
            lines.append(f"- {n}거래일: {o._signed(r['pct'])} ({r['from_date']} 종가 {_won(r['from_close'])} 대비)")

    lines += ["", "## 이동평균"]
    for n, m in flow["mas"].items():
        lines.append(f"- {n}일 이동평균: 계산할 시세 부족" if m is None else f"- {n}일 이동평균: {_won(m['value'])}{_gap(m['gap_pct'])}")
    known = [(n, m["value"]) for n, m in flow["mas"].items() if m is not None]
    if len(known) >= 2:
        (s, sv), (lg, lv) = known[0], known[-1]
        rel = "위" if sv > lv else "아래" if sv < lv else "같은 위치"
        lines.append(f"- {s}일선이 {lg}일선{'과 ' + rel + '에' if rel == '같은 위치' else '보다 ' + rel + '에'} 있습니다.")

    v = flow["volume"]
    lines += ["", "## 거래량"]
    if v.get("value") is None:
        lines.append("- 기준일 거래량 데이터가 없습니다.")
    else:
        lines.append(f"- 기준일({flow['base_date']}) 거래량: {v['value']:,}주")
        if v.get("avg") is None:
            lines.append(f"- 직전 {v['days']}거래일 평균: 계산할 시세 부족")
        else:
            lines.append(f"- 직전 {v['days']}거래일 평균({v['from_date']}~{v['to_date']}, 기준일 제외): {v['avg']:,}주")
            lines.append(f"- 기준일 거래량은 평균의 {_ratio(v['ratio'])}입니다.")

    wd = flow["window"]
    lines += ["", f"## 최근 {wd['days']}거래일 범위"]
    if wd["days"] < wd["asked"]:
        lines.append(f"- 시세가 {flow['n_rows']}거래일뿐이라 {wd['days']}거래일 범위로 계산했습니다.")
    lines.append(f"- 기간 수익률: {o._signed(wd['pct'])} ({wd['from_date']} 종가 대비)")
    lines.append(f"- 최고 종가 {_won(wd['high'])}({wd['high_date']}), 최저 종가 {_won(wd['low'])}({wd['low_date']})")

    ri = ind.get("regime_inputs") or {}
    lines += ["", f"## 국면 판단: {_REGIME_WORD[regime]}"]
    if regime == o.REGIME_HOLD:
        lines.append(f"- 근거 부족: {_HOLD_WHY[hold_reason(res)]}")
    else:
        lines.append(f"- 코드 규칙 판정: {regime}")
        lines.append(f"- 판정 근거: {_REGIME_WHY[regime]}")
        lines.append(f"- 판정에 쓴 값: 종가 {_won(ind['close'])}, 20일선 {_won(ri['ma20'])}, 60일선 {_won(ri['ma60'])}, "
                     f"5거래일 {o._signed(ri['r5'])}, 20거래일 {o._signed(ri['r20'])}, 60거래일 {o._signed(ri['r60'])}, "
                     f"20거래일 최저 종가 대비 {o._signed(ri['from_low20_pct'])}")
    lines += ["", f"- {NOT_A_FORECAST}"]
    return "\n".join(lines)
