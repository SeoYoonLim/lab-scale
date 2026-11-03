"""(2026-11-03, batch 14) 전망·후속 결론 답변 채점. 기대 숫자는 app/outlook.py를 쓰지 않고 SQL과 pandas로 따로 계산한다.

기간 정의는 API.md "전망·후속 결론 질문" 절을 그대로 옮겼다(앱 상수를 import하지 않는다): 1주일 = 수익률 5·20거래일, 이동평균 5·20일,
고점·저점 20거래일, 1개월 = 20·60, 20·60, 60, 3개월 = 60·120, 60·120, 120. 공통: RSI(14, 단순평균), MACD(12,26,9, 첫 값 시작 EMA),
거래량 = 기준일 ÷ 직전 20거래일 평균, 변동성 = 최근 20거래일 일간 수익률(%)의 표본표준편차.
"""

import json
import re
import statistics
from pathlib import Path

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


def _fold(text: str | None) -> str:
    """비교용 정규화: 전각/반각(NFKC), 공백 제거, 대소문자 무시. (2026-11-03 승인: "TIGER미국S&P500" 같은 띄어쓰기 차이를 무관으로 셌다)"""
    import unicodedata

    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).casefold()


def evidence_errors(sources: list[dict], company_name: str, forms: list[str]) -> list[str]:
    """(g): 근거 문서 중 요청 종목과 무관한 항목. 문서의 종목이 요청 종목 하나가 아니거나, 뉴스 제목에 그 종목 표기가 없으면 무관으로 센다."""
    errs = []
    for s in sources or []:
        if s.get("company_names") != [company_name]:
            errs.append(f"종목 {s.get('company_names')}: {s.get('title')}")
        elif s.get("type") == "news" and not any(_fold(f) in _fold(s.get("title")) for f in forms):
            errs.append(f"제목에 종목 없음: {s.get('title')}")
    return errs


# ---------- batch 16(2026-11-03): 묶음 판정, 과거 검증 줄, 직접·간접 뉴스 분리, 실적 ----------
# 앱 코드를 쓰지 않는다: 답변 문장을 읽어 다시 계산하고, 과거 검증은 JSON 파일을, 실적은 SQL을 직접 읽는다.

GROUP_MARGIN = 2  # API.md "신호 묶음": 상승·하락 표 차이 2표 이상이면 우위
MIN_GROUPS = 3
_SIDE = {"상승 쪽": 1, "하락 쪽": -1, "중립": 0}
_GROUP_LINE = re.compile(r"^  - (?P<group>[^:]+): (?P<vote>상승 쪽|하락 쪽|중립) 1표 ← (?P<members>.+)$", re.MULTILINE)
_MEMBER = re.compile(r"\((상승 쪽|하락 쪽|중립)\)(?:, |$)")
_VOTE_LINE = re.compile(r"묶음 판정\(묶음당 1표\): 상승 쪽 (\d+)표, 하락 쪽 (\d+)표, 중립 (\d+)표 \(계산한 묶음 (\d+)개\)")
BACKTEST_JSON = Path(__file__).resolve().parents[1] / "app" / "data" / "outlook_backtest.json"


def check_groups(answer: str) -> list[str]:
    """(a) 묶음마다 구성 지표의 쪽으로 1표를 다시 계산하고, 표 집계와 최종 라벨이 그 표로 나오는지."""
    errs = []
    groups = list(_GROUP_LINE.finditer(answer or ""))
    if not groups:
        return ["묶음 줄 없음"]
    votes = []
    for g in groups:
        members = [_SIDE[m] for m in _MEMBER.findall(g.group("members"))]
        if not members:
            errs.append(f"{g.group('group')}: 지표 없음")
            continue
        total = sum(members)
        want = 1 if total > 0 else -1 if total < 0 else 0
        if _SIDE[g.group("vote")] != want:
            errs.append(f"{g.group('group')}: 표 {g.group('vote')} != 재계산 {want}")
        votes.append(want)
    m = _VOTE_LINE.search(answer)
    if not m:
        return errs + ["묶음 집계 줄 없음"]
    up, down, neu, n = (int(x) for x in m.groups())
    if (up, down, neu, n) != (votes.count(1), votes.count(-1), votes.count(0), len(votes)):
        errs.append(f"집계 {(up, down, neu, n)} != 재계산 {(votes.count(1), votes.count(-1), votes.count(0), len(votes))}")
    mk = marker(answer) or {}
    if mk.get("label") != "판단 유보":
        want_label = "상승 우위" if up - down >= GROUP_MARGIN else "하락 우위" if down - up >= GROUP_MARGIN else "중립"
        if n < MIN_GROUPS:
            want_label = "판단 유보"
        if mk.get("label") != want_label:
            errs.append(f"라벨 {mk.get('label')} != 묶음 표로 계산한 {want_label}")
    return errs


def check_backtest_line(answer: str, period: str) -> list[str]:
    """(b) 과거 검증 줄의 숫자가 app/data/outlook_backtest.json의 검증 구간 값과 같은지."""
    mk = marker(answer) or {}
    label = mk.get("label")
    if label == "판단 유보":
        return [] if "판단 유보는 과거 검증 대상이 아닙니다" in answer else ["유보 안내 없음"]
    m = re.search(r"과거 검증\(검증 구간 신호일 (\S+)~(\S+), (\d+)거래일 뒤\): 같은 라벨\((.+?)\)일 때 상승 비율 ([\d.]+)%\(같은 기간 전체 "
                  r"([\d.]+)%, 표본 ([\d,]+), 차이 ([+-][\d.]+)%p, 95% 구간 ([+-][\d.]+)~([+-][\d.]+)%p\)", answer or "")
    if not m:
        return ["과거 검증 줄 없음"]
    data = json.loads(BACKTEST_JSON.read_text(encoding="utf-8"))["periods"][period]["valid"]
    r = data["labels"][label]
    got = m.groups()
    want = (data["first_date"], data["last_date"], str(data["horizon"]), label, f"{r['up_rate'] * 100:.1f}", f"{r['base_rate'] * 100:.1f}",
            f"{r['n']:,}", f"{r['diff'] * 100:+.1f}", f"{r['diff_ci95'][0] * 100:+.1f}", f"{r['diff_ci95'][1] * 100:+.1f}")
    errs = [f"과거 검증 {a} != {b}" for a, b in zip(got, want) if a != b]
    no_diff = "전체 평균과 뚜렷한 차이가 없었습니다" in answer
    if no_diff == bool(r["significant"]):
        errs.append("유의성 문장이 JSON significant와 다름")
    return errs


def _section(answer: str, title: str) -> list[str]:
    return sections(answer).get(title, [])


def check_indirect(answer: str, kind: str, company_name: str, forms: list[str]) -> list[str]:
    """(c) 간접 섹션(업종·기초지수)이 따로 있고, 직접 섹션의 기사는 모두 요청 종목 표기가 있으며, 간접 기사가 직접 섹션에 섞이지 않는지."""
    title = {"업종": "업종·산업 뉴스(간접)", "기초지수": "기초지수 관련 뉴스(간접)"}[kind]
    errs = []
    indirect = _section(answer, title)
    if not indirect:
        errs.append(f"{title} 섹션 없음")
    direct = _section(answer, "뉴스·공시(직접 관련)")
    titles = lambda lines: [m.group(1) for line in lines for m in [re.search(r'"(.+)"라는 제목의 기사', line)] if m]
    for t in titles(direct):
        if not any(_fold(f) in _fold(t) for f in forms):
            errs.append(f"직접 섹션에 무관 기사: {t}")
    overlap = set(titles(direct)) & set(titles(indirect))
    if overlap:
        errs.append(f"간접 기사가 직접 섹션에도 있음: {sorted(overlap)}")
    return errs


def check_earnings(db, answer: str, company_name: str) -> list[str]:
    """(f) 실적 줄(억 원, 전년 동기 대비 %)을 quarterly_earnings SQL로 다시 계산해 대조한다."""
    lines = _section(answer, "실적(참고)")
    if not lines:
        return ["실적 섹션 없음"]
    rows = [l for l in lines if re.match(r"- \d{4}년 (?:1분기|2분기|3분기|연간)\(", l)]
    if not rows:
        return ["실적 줄 없음: " + " ".join(lines)[:80]]
    code = {"1분기": "11013", "2분기": "11012", "3분기": "11014", "연간": "11011"}
    errs = []
    for line in rows:
        m = re.match(r"- (\d{4})년 (1분기|2분기|3분기|연간)\((연결|별도)\): (.+) \(접수일 (\S+), DART 접수번호 (\d+)\)", line)
        if not m:
            errs.append(f"실적 줄 형식: {line[:60]}")
            continue
        year, q, basis, body, d, rcept = m.groups()
        r = db.execute(text(
            "select e.fs_div, e.revenue, e.operating_income, e.net_income, e.revenue_prev, e.operating_income_prev, e.net_income_prev, "
            "e.rcept_no, e.disclosed_date from quarterly_earnings e join company c on c.id = e.company_id "
            "where c.name = :n and e.bsns_year = :y and e.reprt_code = :r"), {"n": company_name, "y": int(year), "r": code[q]}).first()
        if r is None:
            errs.append(f"DB에 없음: {year} {q}")
            continue
        if (basis == "연결") != (r[0] == "CFS") or rcept != r[7] or d != r[8].isoformat():
            errs.append(f"{year} {q} 기준·접수번호·접수일 불일치")
        for name, cur, prev in (("매출액", r[1], r[4]), ("영업이익", r[2], r[5]), ("당기순이익", r[3], r[6])):
            mm = re.search(rf"{name} (-?[\d,]+)억 원(?:, 전년 동기 대비 ([+-][\d.]+)%)?", body)
            if cur is None:
                continue
            if not mm:
                errs.append(f"{year} {q} {name} 없음")
                continue
            if mm.group(1) != f"{float(cur) / 1e8:,.0f}":
                errs.append(f"{year} {q} {name} {mm.group(1)} != {float(cur) / 1e8:,.0f}")
            want_yoy = f"{(float(cur) / float(prev) - 1) * 100:+.1f}" if prev is not None and float(prev) > 0 and float(cur) >= 0 else None
            if mm.group(2) != want_yoy:
                errs.append(f"{year} {q} {name} 증감률 {mm.group(2)} != {want_yoy}")
    return errs
