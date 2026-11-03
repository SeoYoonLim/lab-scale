"""(2026-11-03, batch 14) 전망·후속 결론 답변 채점. 기대 숫자는 app/outlook.py를 쓰지 않고 SQL과 pandas로 따로 계산한다.

기간 정의는 API.md "전망·후속 결론 질문" 절을 그대로 옮겼다(앱 상수를 import하지 않는다): 1주일 = 수익률 5·20거래일, 이동평균 5·20일,
고점·저점 20거래일, 1개월 = 20·60, 20·60, 60, 3개월 = 60·120, 60·120, 120. 공통: RSI(14, 단순평균), MACD(12,26,9, 첫 값 시작 EMA),
거래량 = 기준일 ÷ 직전 20거래일 평균, 변동성 = 최근 20거래일 일간 수익률(%)의 표본표준편차.
"""

import re
import statistics

from sqlalchemy import text

SPEC = {
    "1주일": {"days": 5, "returns": (5, 20), "ma": (5, 20), "range": 20},
    "1개월": {"days": 20, "returns": (20, 60), "ma": (20, 60), "range": 60},
    "3개월": {"days": 60, "returns": (60, 120), "ma": (60, 120), "range": 120},
}
LABELS = ("상승 우위", "하락 우위", "중립", "판단 유보")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MARKER = re.compile(r"- (?P<name>.+?)\((?P<ticker>[0-9A-Z]{6})\), 분석 기간 (?P<period>1주일|1개월|3개월)\(\d+거래일\): (?P<label>\S+(?: \S+)?)")
_ASSERTION = re.compile(
    r"(?:오를|떨어질|상승할|하락할|내릴|오른다|내린다)\s*(?:것입니다|것이다|겁니다|거예요)|"
    r"목표\s*주?가|\d+(?:\.\d+)?\s*%\s*(?:의\s*)?(?:확률|가능성)|(?:확률|가능성)(?:은|이|는)?\s*\d|기대\s*수익률(?:은|이)?\s*[+-]?\d"
)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def sections(answer: str) -> dict[str, list[str]]:
    out, cur = {}, None
    for line in (answer or "").splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur is not None and line.strip():
            out[cur].append(line)
    return out


def marker(answer: str) -> dict | None:
    m = _MARKER.search(answer or "")
    return m.groupdict() if m else None


def assertion_hits(answer: str) -> list[str]:
    return [m.group(0) for m in _ASSERTION.finditer(answer or "")]


def structure(answer: str) -> dict:
    """(a): 첫 섹션이 '최종 신호 판단'이고 라벨이 있는지, 기술적 지표 섹션에서 숫자와 날짜가 함께 있는 줄 수."""
    sec = sections(answer)
    first = next(iter(sec), None)
    mk = marker(answer)
    tech = sec.get("기술적 지표", [])
    n = sum(1 for line in tech if re.search(r"\d", line) and _DATE.search(line))
    return {"label_first": first == "최종 신호 판단" and mk is not None and mk["label"] in LABELS, "indicators_with_date": n,
            "marker": mk}


def _rows(db, company_name: str, base_date: str, limit: int = 260) -> list[tuple]:
    rows = db.execute(text(
        "select s.price_date, s.close_price, s.volume from stock_price s join company c on c.id = s.company_id "
        "where c.name = :n and s.price_date <= :d order by s.price_date desc limit :k"), {"n": company_name, "d": base_date, "k": limit}).fetchall()
    return [(r[0], float(r[1]), r[2]) for r in reversed(rows)]


def expected(db, company_name: str, period: str, base_date: str) -> dict:
    """SQL·pandas로 계산한 기대값."""
    import pandas as pd

    spec = SPEC[period]
    rows = _rows(db, company_name, base_date)
    closes = [c for _, c, _ in rows]
    out = {"base_date": rows[-1][0].isoformat(), "returns": {}, "ma": {}}
    for n in sorted(set(spec["returns"]) | {spec["days"]}):
        if len(closes) > n:
            out["returns"][n] = round((closes[-1] / closes[-1 - n] - 1) * 100, 2)
    for n in spec["ma"]:
        if len(closes) >= n:
            out["ma"][n] = round(sum(closes[-n:]) / n)
    diffs = [b - a for a, b in zip(closes[-15:-1], closes[-14:])]
    g, l_ = sum(d for d in diffs if d > 0) / 14, sum(-d for d in diffs if d < 0) / 14
    out["rsi"] = round(g / (g + l_) * 100, 2) if g + l_ else None
    s = pd.Series(closes)
    line = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    out["macd"] = (round(float(line.iloc[-1]), 2), round(float(line.ewm(span=9, adjust=False).mean().iloc[-1]), 2))
    rets = [(b / a - 1) * 100 for a, b in zip(closes[-21:-1], closes[-20:])]
    out["volatility"] = round(statistics.stdev(rets), 2)
    win = rows[-(spec["range"] + 1):]
    hi, lo = max(c for _, c, _ in win), min(c for _, c, _ in win)
    out["range"] = {"from": win[0][0].isoformat(), "high": hi, "low": lo,
                    "from_high": round((closes[-1] / hi - 1) * 100, 2), "from_low": round((closes[-1] / lo - 1) * 100, 2)}
    vols = [v for _, _, v in rows[-21:-1]]
    out["volume_ratio"] = round(rows[-1][2] / (sum(vols) / 20), 2) if all(v is not None for v in vols) and rows[-1][2] else None
    return out


def check_numbers(db, answer: str, company_name: str, period: str) -> list[str]:
    """(b): 답변의 지표 숫자가 SQL 기대값과 같은지, 같은 기간의 수치가 답변 안에서 서로 다르지 않은지. 틀린 항목 목록."""
    m = re.search(r"기준일\(최신 거래일\): (\d{4}-\d{2}-\d{2})", answer or "")
    if not m:
        return ["기준일 없음"]
    exp = expected(db, company_name, period, m.group(1))
    errs = []
    if exp["base_date"] != m.group(1):
        errs.append(f"기준일 {m.group(1)} != {exp['base_date']}")
    seen: dict[int, set] = {}
    for n, v in re.findall(r"(\d+)거래일 수익률:? ([+-]\d+\.\d{2})%", answer):
        seen.setdefault(int(n), set()).add(float(v))
    for n, vals in seen.items():
        if len(vals) > 1:
            errs.append(f"{n}거래일 수익률이 서로 다름 {sorted(vals)}")
        if n in exp["returns"] and any(abs(v - exp["returns"][n]) > 0.011 for v in vals):
            errs.append(f"{n}거래일 수익률 {sorted(vals)} != {exp['returns'][n]}")
    for n, v in re.findall(r"- (\d+)일 이동평균: ([\d,]+)원", answer):
        if int(n) in exp["ma"] and abs(_num(v) - exp["ma"][int(n)]) > 1:
            errs.append(f"{n}일 이동평균 {v} != {exp['ma'][int(n)]}")
    if (r := re.search(r"- RSI\(14\): (\d+\.\d{2})", answer)) and exp["rsi"] is not None and abs(float(r.group(1)) - exp["rsi"]) > 0.011:
        errs.append(f"RSI {r.group(1)} != {exp['rsi']}")
    if (r := re.search(r"- MACD\(12,26,9\): MACD (-?[\d,]+\.\d{2}), 시그널 (-?[\d,]+\.\d{2})", answer)):
        tol = max(0.05, abs(exp["macd"][0]) * 1e-6)
        if abs(_num(r.group(1)) - exp["macd"][0]) > tol or abs(_num(r.group(2)) - exp["macd"][1]) > tol:
            errs.append(f"MACD {r.group(1)}/{r.group(2)} != {exp['macd']}")
    if (r := re.search(r"표준편차 (\d+\.\d{2})%", answer)) and abs(float(r.group(1)) - exp["volatility"]) > 0.011:
        errs.append(f"변동성 {r.group(1)} != {exp['volatility']}")
    if (r := re.search(r"평균 [\d,]+주의 (\d+\.\d{2})배", answer)) and exp["volume_ratio"] is not None \
            and abs(float(r.group(1)) - exp["volume_ratio"]) > 0.011:
        errs.append(f"거래량 배수 {r.group(1)} != {exp['volume_ratio']}")
    for kind, key in (("최고", "from_high"), ("최저", "from_low")):
        for v, frm in re.findall(rf"- {kind} 종가 대비: ([+-]\d+\.\d{{2}})% \(조회 기간 (\S+?)~", answer):
            if abs(float(v) - exp["range"][key]) > 0.011 or frm != exp["range"]["from"]:
                errs.append(f"{kind} 종가 대비 {v}({frm}~) != {exp['range'][key]}({exp['range']['from']}~)")
    return errs


def evidence_errors(sources: list[dict], company_name: str, forms: list[str]) -> list[str]:
    """(g): 근거 문서 중 요청 종목과 무관한 항목. 문서의 종목이 요청 종목 하나가 아니거나, 뉴스 제목에 그 종목 표기가 없으면 무관으로 센다."""
    errs = []
    for s in sources or []:
        if s.get("company_names") != [company_name]:
            errs.append(f"종목 {s.get('company_names')}: {s.get('title')}")
        elif s.get("type") == "news" and not any(f.casefold() in (s.get("title") or "").casefold() for f in forms):
            errs.append(f"제목에 종목 없음: {s.get('title')}")
    return errs
