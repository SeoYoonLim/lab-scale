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
    # 42차(2026-11-11): 최고·최저는 기준일을 포함한 최근 N개 종가(app/price_flow·quality_fixtures와 같은 한 정의)
    win = rows[-spec["range"]:]
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
    rows = [l for l in lines if re.match(r"- \d{4}년 (?:1분기|2분기|3분기|4분기|연간)\(", l)]
    if not rows:
        return ["실적 줄 없음: " + " ".join(lines)[:80]]
    code = {"1분기": "11013", "2분기": "11012", "3분기": "11014", "연간": "11011"}
    errs = []
    for line in rows:
        if re.search(r"\((?:연결|별도|기준 미표기), (?:잠정|확정)", line):  # 24차 형식(출처 표시, 잠정실적 포함)
            errs += check_earnings_line24(db, line, company_name)
            continue
        m = re.match(r"- (\d{4})년 (1분기|2분기|3분기|4분기|연간)\((연결|별도)(?:, 연간 - 3분기 누적)?\): (.+) \(접수일 (\S+), DART 접수번호 (\d+)\)", line)
        if not m:
            errs.append(f"실적 줄 형식: {line[:60]}")
            continue
        year, q, basis, body, d, rcept = m.groups()
        if q == "4분기":  # 2026-11-03 지시 A: 4분기 = 사업보고서 연간 - 같은 해 3분기 누적(SQL로 따로 계산)
            errs += _check_q4(db, company_name, int(year), basis, body, d, rcept)
            continue
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


def _check_q4(db, company_name: str, year: int, basis: str, body: str, d: str, rcept: str) -> list[str]:
    """4분기 = 연간(11011) - 3분기 누적(11014의 *_cum). 전년 동기는 전년도 같은 계산."""
    def q4(y):
        r = db.execute(text(
            "select a.fs_div, a.revenue - q.revenue_cum, a.operating_income - q.operating_income_cum, a.net_income - q.net_income_cum, "
            "a.rcept_no, a.disclosed_date from quarterly_earnings a join quarterly_earnings q on q.company_id = a.company_id "
            "and q.bsns_year = a.bsns_year and q.reprt_code = '11014' and q.fs_div = a.fs_div join company c on c.id = a.company_id "
            "where c.name = :n and a.bsns_year = :y and a.reprt_code = '11011'"), {"n": company_name, "y": y}).first()
        return r
    cur, prev = q4(year), q4(year - 1)
    if cur is None:
        return [f"{year} 4분기: DB에서 계산 불가"]
    errs = []
    if (basis == "연결") != (cur[0] == "CFS") or rcept != cur[4] or d != cur[5].isoformat():
        errs.append(f"{year} 4분기 기준·접수번호·접수일 불일치")
    for i, name in ((1, "매출액"), (2, "영업이익"), (3, "당기순이익")):
        mm = re.search(rf"{name} (-?[\d,]+)억 원(?:, 전년 동기 대비 ([+-][\d.]+)%)?", body)
        if cur[i] is None:
            continue
        if not mm or mm.group(1) != f"{float(cur[i]) / 1e8:,.0f}":
            errs.append(f"{year} 4분기 {name} {mm.group(1) if mm else None} != {float(cur[i]) / 1e8:,.0f}")
            continue
        p = prev[i] if prev is not None and prev[0] == cur[0] else None
        want = f"{(float(cur[i]) / float(p) - 1) * 100:+.1f}" if p is not None and float(p) > 0 and float(cur[i]) >= 0 else None
        if mm.group(2) != want:
            errs.append(f"{year} 4분기 {name} 증감률 {mm.group(2)} != {want}")
    return errs


# ---------- batch 18(2026-11-04, 23차): 결론 먼저 구조, 국면(독립 SQL), 같은 사실 1표, 짧은 후속 답, 숫자·조회 기간 ----------
# 국면 규칙은 API.md "국면 규칙"을 그대로 옮겼다(app/outlook.py를 import하지 않는다).

REGIMES = ("단기 반등 시도, 추세 전환은 미확인", "상승 추세 지속", "상승 추세 중 조정", "하락 추세 지속", "횡보, 방향 불분명",
           "판단 유보(데이터 부족)")
_TITLE = re.compile(r"^(?P<name>.+?)\((?P<ticker>[0-9A-Z]{6})\) · 향후 (?P<days>\d+)거래일")
# 46차: 국면 안에 다른 "현재는 "이 들어가지 못하게 한다. 한 줄 답에서 방향이 중립이면 "현재는 상승과 하락 중 …(중립 문장). 현재는 횡보, …
# (기준일 …)"처럼 "현재는"이 두 번 나오는데, 예전 식은 첫 "현재는"부터 잡아 중립 문장까지 국면으로 읽었다(교정 뒤 삼성바이오로직스에서 드러남)
_STATE = re.compile(r"현재는 (?P<regime>(?:(?!현재는 ).)+?) \(기준일 (?P<date>\d{4}-\d{2}-\d{2}) 종가\)")
_SENTENCE_END = re.compile(r"(?:다|요)(?:\([^)]*\))?\.(?=\s|$)")  # "...못했습니다(상승 비율 51.8% vs 전체 49.9%)."도 한 문장


def regime_expected(db, company_name: str, base_date: str, hold: bool = False) -> str:
    """API.md "국면 규칙"을 SQL 종가로 다시 계산한다."""
    closes = [c for _, c, _ in _rows(db, company_name, base_date, 61)]
    if hold or len(closes) < 61:
        return REGIMES[5]
    close = closes[-1]
    r = {n: (close / closes[-1 - n] - 1) * 100 for n in (5, 20, 60)}
    ma = {n: sum(closes[-n:]) / n for n in (5, 20, 60)}
    low20 = (close / min(closes[-20:]) - 1) * 100  # 최근 20거래일 = 기준일 포함 20개 종가(42차)
    big_drop = r[20] <= -10 or r[60] <= -15
    rebound = r[5] > 0 or ma[5] > ma[20] or low20 >= 5
    if big_drop and rebound and (close < ma[20] or close < ma[60]):
        return REGIMES[0]
    if close < ma[20] < ma[60] and (r[20] <= -5 or r[60] <= -10):
        return REGIMES[3]
    if r[60] >= 10 and close > ma[60] and ma[20] > ma[60] and (close < ma[20] or r[5] <= -3):
        return REGIMES[2]
    if close > ma[20] > ma[60] and (r[20] >= 5 or r[60] >= 10):
        return REGIMES[1]
    return REGIMES[4]


def title_of(answer: str) -> dict | None:
    m = _TITLE.search(answer or "")
    if not m:
        return None
    period = next((p for p, s in SPEC.items() if str(s["days"]) == m.group("days")), None)
    return {"name": m.group("name"), "ticker": m.group("ticker"), "period": period}


def check_layout(answer: str, company_name: str, period: str) -> list[str]:
    """(a) 제목 줄 -> 상태 줄 -> 첫 섹션 "종합 판단"(2~3문장). 제목·상태 줄에 우위 라벨을 단독으로 내세우지 않는다."""
    lines = (answer or "").splitlines()
    errs = []
    t = title_of(lines[0] if lines else "")
    if not t or t["name"] != company_name or t["period"] != period:
        errs.append(f"제목 줄 {lines[0][:40] if lines else None!r}")
    intro = []
    for line in lines[1:]:
        if line.startswith("## "):
            break
        if line.strip():
            intro.append(line)
    if not intro or not _STATE.match(intro[0]) or _STATE.match(intro[0]).group("regime") not in REGIMES:
        errs.append(f"상태 줄 {intro[0][:40] if intro else None!r}")
    if any(w in " ".join([lines[0] if lines else ""] + intro[:1]) for w in ("상승 우위", "하락 우위")):
        errs.append("제목·상태 줄에 우위 라벨")
    sec = sections(answer)
    first = next(iter(sec), None)
    if first != "종합 판단":
        errs.append(f"첫 섹션 {first}")
    else:
        para = next((x for x in sec[first] if not x.startswith(("-", " "))), "")
        n = len(_SENTENCE_END.findall(para))
        if not 2 <= n <= 3:
            errs.append(f"종합 판단 {n}문장")
    return errs


def check_regime(db, answer: str, company_name: str) -> list[str]:
    """(c) 상태 줄의 국면 = 독립 SQL 계산."""
    m = _STATE.search(answer or "")
    if not m:
        return ["상태 줄 없음"]
    hold = (marker(answer) or {}).get("label") == "판단 유보"
    want = regime_expected(db, company_name, m.group("date"), hold)
    return [] if m.group("regime") == want else [f"국면 {m.group('regime')} != {want}"]


_GROUP_LINE23 = re.compile(r"^  - (?P<group>[^:]+): (?P<vote>상승 쪽|하락 쪽|중립) 1표 ← (?P<members>.+)$", re.MULTILINE)
_MEMBER23 = re.compile(r"(?P<name>[^,(]+?)\((?P<side>상승 쪽|하락 쪽|중립)(?P<note>, '[^']+'와 같은 사실이라 세지 않음)?\)(?:, |$)")
_CROSS = re.compile(r"골든크로스|데드크로스")
_ALIGN = re.compile(r"\d+일선이 \d+일선 (?:위|아래)")


def check_double_vote(answer: str) -> list[str]:
    """(b) 교차와 이동평균 배열(같은 사실)이 2표가 되지 않는다: 같은 묶음에 있고, 배열이 있으면 교차는 세지 않는다.
    그 규칙으로 묶음 표·집계·라벨·종합 판단의 표 수를 다시 계산해 대조한다."""
    errs, votes = [], []
    groups = list(_GROUP_LINE23.finditer(answer or ""))
    if not groups:
        return ["묶음 줄 없음"]
    cross_groups, align_groups = set(), set()
    for g in groups:
        members = list(_MEMBER23.finditer(g.group("members")))
        names = [m.group("name").strip() for m in members]
        if any(_CROSS.search(n) for n in names):
            cross_groups.add(g.group("group"))
        if any(_ALIGN.search(n) for n in names):
            align_groups.add(g.group("group"))
        has_align = any(_ALIGN.search(n) for n in names)
        total = 0
        for m in members:
            if _CROSS.search(m.group("name")) and has_align:
                if not m.group("note"):
                    errs.append(f"{g.group('group')}: 교차를 배열과 따로 셈")
                continue
            total += _SIDE[m.group("side")]
        want = 1 if total > 0 else -1 if total < 0 else 0
        if _SIDE[g.group("vote")] != want:
            errs.append(f"{g.group('group')}: 표 {g.group('vote')} != 재계산 {want}")
        votes.append(want)
    if cross_groups and align_groups and cross_groups != align_groups:
        errs.append(f"교차 {sorted(cross_groups)}와 배열 {sorted(align_groups)}가 다른 묶음(2표)")
    m = _VOTE_LINE.search(answer or "")
    if not m:
        return errs + ["묶음 집계 줄 없음"]
    up, down = votes.count(1), votes.count(-1)
    if (int(m.group(1)), int(m.group(2))) != (up, down):
        errs.append(f"집계 {(m.group(1), m.group(2))} != {(up, down)}")
    t = re.search(r"묶음 표 상승 (\d+), 하락 (\d+)", answer or "")
    if t and (int(t.group(1)), int(t.group(2))) != (up, down):
        errs.append(f"종합 판단 표 수 {t.groups()} != {(up, down)}")
    return errs


def check_short_followup(answer: str, company_name: str, period: str, kind: str) -> list[str]:
    """(d) 6줄 이하(빈 줄 제외), 규칙 설명 0, 섹션 없음, 직전 종목·기간 유지(첫 줄 제목), 종류별 첫 내용."""
    lines = [x for x in (answer or "").splitlines() if x.strip()]
    errs = []
    if len(lines) > 6:
        errs.append(f"{len(lines)}줄")
    if re.search(r"규칙", answer or ""):
        errs.append("규칙 설명")
    if "## " in (answer or ""):
        errs.append("섹션 제목")
    t = title_of(lines[0] if lines else "")
    if not t or t["name"] != company_name or t["period"] != period:
        errs.append(f"종목·기간 {t}")
    second = lines[1] if len(lines) > 1 else ""
    if kind == "verdict" and not second.startswith(("지금 데이터로는", "과거 검증에서 이 신호 상태", "데이터가 부족해")):
        errs.append(f"첫 문장이 질문에 바로 답하지 않음: {second[:30]}")
    if kind == "risk":
        level = next((x for x in lines if x.startswith("- 확인할 가격대: ")), "")
        if not any(x.startswith("- 하락 위험: ") for x in lines) or not all(w in level for w in ("20일선", "분석 기간 최저 종가", "52주 최저 종가")):
            errs.append("하락 위험·가격대")
    if kind == "probability" and (second != "검증된 상승·하락 확률은 없습니다." or "과거 빈도" not in (answer or "")):
        errs.append("확률 없음 안내·과거 빈도")
    return errs


def _closes_between(db, company_name: str, start: str, end: str) -> list[tuple]:
    rows = db.execute(text(
        "select s.price_date, s.close_price from stock_price s join company c on c.id = s.company_id "
        "where c.name = :n and s.price_date between :a and :b order by s.price_date"), {"n": company_name, "a": start, "b": end}).fetchall()
    return [(r[0], float(r[1])) for r in rows]


def check_numbers23(db, answer: str, company_name: str) -> list[str]:
    """(e) 숫자 SQL 검산(모든 N거래일 수익률, N일선·N일 이동평균, 하루 등락, 거래량 배수, 최고·최저 종가와 조회 기간)과
    최고·최저 값마다 조회 기간 표시."""
    m = _STATE.search(answer or "") or re.search(r"기준일\(최신 거래일\): (?P<date>\d{4}-\d{2}-\d{2})", answer or "")
    if not m:
        return ["기준일 없음"]
    base = m.group("date")
    rows = _rows(db, company_name, base)
    closes = [c for _, c, _ in rows]
    errs = []
    for n, v in set(re.findall(r"(\d+)거래일 수익률:? ([+-]\d+\.\d{2})%", answer)):
        n = int(n)
        if len(closes) > n and abs(float(v) - round((closes[-1] / closes[-1 - n] - 1) * 100, 2)) > 0.011:
            errs.append(f"{n}거래일 수익률 {v}")
    for n, v in set(re.findall(r"(\d+)일선\(?\s?([\d,]+)원", answer) + re.findall(r"(\d+)일 이동평균: ([\d,]+)원", answer)):
        n = int(n)
        if len(closes) >= n and abs(_num(v) - round(sum(closes[-n:]) / n)) > 1:
            errs.append(f"{n}일선 {v}")
    for v in set(re.findall(r"하루 (?:등락률: )?([+-]\d+\.\d{2})%", answer)):
        if abs(float(v) - round((closes[-1] / closes[-2] - 1) * 100, 2)) > 0.011:
            errs.append(f"하루 등락 {v}")
    vols = [v for _, _, v in rows[-21:-1]]
    if all(v is not None for v in vols) and rows[-1][2]:
        ratio = round(rows[-1][2] / (sum(vols) / 20), 2)
        for v in set(re.findall(r"평균의 (\d+\.\d{2})배", answer)):
            if abs(float(v) - ratio) > 0.011:
                errs.append(f"거래량 배수 {v} != {ratio}")
    rng = re.compile(r"- (?:52주 )?(최고|최저) 종가 대비: ([+-]\d+\.\d{2})% \(조회 기간 (\S+?)~(\S+?), (?:분석 기간|52주) 기준 최근 (\d+)거래일, "
                     r"(?:최고|최저) 종가 ([\d,]+)원, (\S+?)\)")
    for kind, pct, a, b, n, v, d in rng.findall(answer):
        win = _closes_between(db, company_name, a, b)
        if len(win) != int(n) or b != base:
            errs.append(f"{kind} 조회 기간 {a}~{b} 거래일 {len(win)} != {n}")
            continue
        pick = (max if kind == "최고" else min)(c for _, c in win)
        days = [dd.isoformat() for dd, c in win if c == pick]
        if abs(_num(v) - pick) > 0.5 or d not in days or abs(float(pct) - round((closes[-1] / pick - 1) * 100, 2)) > 0.011:
            errs.append(f"{kind} 종가 {v}({d}) {pct}% != {pick}({days})")
    def last_window(n):
        return [(d, c) for d, c, _ in rows[-n:]]

    checks = [(kind, v, d, last_window(int(n))) for _, kind, v, d, n in
              re.findall(r"(분석 기간|52주) (최고|최저) 종가 ([\d,]+)원\((\S+?), 최근 (\d+)거래일 기준\)", answer)]
    for n, a, b, v, d, v2, d2 in re.findall(r"분석 기간\(최근 (\d+)거래일, (\S+?)~(\S+?)\) 최저 종가 ([\d,]+)원\((\S+?)\)[^\n]*?"
                                            r"같은 기간 최고 종가 ([\d,]+)원\((\S+?)\)", answer):
        win = _closes_between(db, company_name, a, b)
        if len(win) != int(n):
            errs.append(f"확인할 사항 조회 기간 {a}~{b} 거래일 {len(win)} != {n}")
        checks += [("최저", v, d, win), ("최고", v2, d2, win)]
    for kind, v, d, win in checks:
        pick = (max if kind == "최고" else min)(c for _, c in win)
        days = [dd.isoformat() for dd, c in win if c == pick]
        if abs(_num(v) - pick) > 0.5 or d not in days:
            errs.append(f"{kind} 종가 {v}({d}) != {pick}({days})")
    for line in (answer or "").splitlines():
        if re.search(r"(최고|최저) 종가 [\d,]+원", line) and not re.search(r"\d{4}-\d{2}-\d{2}~\d{4}-\d{2}-\d{2}|최근 \d+거래일 기준", line):
            errs.append(f"조회 기간 없는 최고·최저 값: {line[:50]}")
    return errs


# ---------- batch 20(2026-11-04, 24차): 현재 판단(결론 먼저), 신호 강도, 예측력, 잠정실적, 시세 지연 구분 ----------
# 규칙은 API.md "전망 4차" 절을 그대로 옮겼다(app/outlook.py·app/preliminary.py를 import하지 않는다).

_JUDGMENTS = ("상승", "하락", "중립", "판단 유보")
_TALLY24 = re.compile(r"(\d+)개 묶음 중 상승 (\d+)·하락 (\d+)·중립 (\d+)")
_JUDGE24 = re.compile(r"현재 판단: (상승|하락|중립|판단 유보)(?:\(굳이 고르면 (상승|하락) 쪽\))?")
_PICK24 = re.compile(r"^굳이 하나를 (?:고르면 (상승|하락) 쪽입니다|고를 근거가 부족합니다)\(현재 판단: (상승|하락|중립|판단 유보)\)")
_FAKE_PROB = re.compile(r"(?:상승|하락|오를|내릴)\s*(?:확률|가능성)(?:은|이|는)?\s*(?:약\s*)?\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*%\s*(?:의\s*)?(?:확률|가능성)")
_BUY_PUSH = re.compile(r"매수하세요|사세요|살 만합니다|매수를 (?:추천|권)합니다|매수 추천|지금이 매수 기회|팔아야 합니다|매도하세요")
_STATUS24 = ("최신", "미수집", "수집 실패", "휴장일 추정", "종목만 누락")
_MARKET_CLOSE_MIN = 15 * 60 + 30
TREND_OF = {"상승 추세 지속": 1, "상승 추세 중 조정": 1, "하락 추세 지속": -1}


def label_from_tally(up: int, down: int, total: int) -> str:
    """API.md "신호 묶음": 계산한 묶음 3개 미만이면 판단 유보, 표 차이 2 이상이면 많은 쪽 우위, 아니면 중립(데이터 유보는 따로)."""
    if total < MIN_GROUPS:
        return "판단 유보"
    return "상승 우위" if up - down >= GROUP_MARGIN else "하락 우위" if down - up >= GROUP_MARGIN else "중립"


def judgment_rule(label: str, regime: str) -> tuple[str, str | None]:
    """현재 판단과 기울기. 묶음 판정과 국면이 같은 방향(상승 우위 + 상승 추세 지속, 하락 우위 + 하락 추세 지속)일 때만 상승·하락,
    판정·국면 중 하나라도 유보면 판단 유보, 그 밖은 중립. 중립의 기울기는 묶음 판정 방향, 판정이 중립이면 추세 방향(상승 추세 지속·
    상승 추세 중 조정 = 상승, 하락 추세 지속 = 하락), 둘 다 없으면 없음."""
    if label == "판단 유보" or regime == REGIMES[5]:
        return "판단 유보", None
    if label == "상승 우위" and regime == "상승 추세 지속":
        return "상승", None
    if label == "하락 우위" and regime == "하락 추세 지속":
        return "하락", None
    lean = {"상승 우위": "상승", "하락 우위": "하락"}.get(label)
    if lean is None and TREND_OF.get(regime):
        lean = "상승" if TREND_OF[regime] > 0 else "하락"
    return "중립", lean


def strength_word(label: str, up: int, down: int, total: int) -> str:
    """우세한 쪽 묶음 수가 계산한 묶음의 절반 미만이면 "약한 " 접두어."""
    lead = up if label == "상승 우위" else down if label == "하락 우위" else None
    return "약한 " if lead is not None and total and lead * 2 < total else ""


def _backtest_row24(period: str, label: str) -> dict | None:
    data = json.loads(BACKTEST_JSON.read_text(encoding="utf-8"))["periods"][period]["valid"]
    r = (data.get("labels") or {}).get(label)
    return r if r and r.get("n") else None


def _check_power(answer: str, period: str, label: str) -> list[str]:
    """예측력: 그 라벨의 검증 구간 표본 수·95% 구간을 쓰고, 유의하지 않으면 예측력이 확인되지 않았다고 밝힌다."""
    if label == "판단 유보":
        return []
    r = _backtest_row24(period, label)
    if r is None:
        return [] if "과거 검증 결과가 아직 없" in answer else ["과거 검증 없음 안내 없음"]
    errs = []
    if f"표본 {r['n']:,}" not in answer:
        errs.append(f"표본 {r['n']:,} 없음")
    ci = f"95% 구간 {r['diff_ci95'][0] * 100:+.1f}~{r['diff_ci95'][1] * 100:+.1f}%p"
    if ci not in answer:
        errs.append(f"{ci} 없음")
    if not r.get("significant") and "예측력은 확인되지 않았습니다" not in answer:
        errs.append("유의하지 않은데 '예측력은 확인되지 않았습니다' 없음")
    return errs


def _head_regime(db, answer: str, company_name: str) -> tuple[str | None, list[str]]:
    m = _STATE.search(answer or "")
    if not m:
        return None, ["상태(국면·기준일) 없음"]
    want = regime_expected(db, company_name, m.group("date"))
    if m.group("regime") != want and not (m.group("regime") == REGIMES[5]):
        return m.group("regime"), [f"국면 {m.group('regime')} != {want}"]
    return m.group("regime"), []


def check_judgment24(db, answer: str, company_name: str, period: str, kind: str) -> list[str]:
    """(24차) 현재 판단·기울기·묶음 비중·예측력. kind: standalone(단독 전망) 또는 후속 답 종류
    (verdict/pick/compare/probability/buy/risk/earnings/data)."""
    a = answer or ""
    lines = [x for x in a.splitlines() if x.strip()]
    errs = []
    t = title_of(lines[0] if lines else "")
    if not t or t["name"] != company_name or t["period"] != period:
        errs.append(f"제목(종목·기간) {lines[0][:40] if lines else None!r}")
    regime, e = _head_regime(db, a, company_name)
    errs += e
    tally = _TALLY24.search(a)
    if not tally:
        return errs + ["'N개 묶음 중 상승 a·하락 b·중립 c' 없음"]
    total, up, down, neutral = map(int, tally.groups())
    if up + down + neutral != total:
        errs.append(f"묶음 비중 합 {up}+{down}+{neutral} != {total}")
    label = label_from_tally(up, down, total)
    if re.search(r"(?:데이터가 부족합니다|시세가 없습니다\([^)]*\))\. 판단을 유보합니다", a):  # 시세 지연(5영업일 초과)·거래일 누락이면 묶음 표와 관계없이 유보(API.md 데이터 점검)
        label = "판단 유보"
    want_j, want_lean = judgment_rule(label, regime or REGIMES[5])
    if kind == "pick":
        m = _PICK24.match(lines[1] if len(lines) > 1 else "")
        if not m:
            errs.append(f"둘째 줄이 '굳이 하나를 …(현재 판단: …)'이 아님: {(lines[1] if len(lines) > 1 else '')[:40]}")
        else:
            got_pick, got_j = m.group(1), m.group(2)
            want_pick = want_j if want_j in ("상승", "하락") else want_lean
            if got_j != want_j or got_pick != want_pick:
                errs.append(f"고른 쪽·판단 ({got_pick}, {got_j}) != ({want_pick}, {want_j})")
    else:
        m = _JUDGE24.search(a)
        if not m:
            errs.append("'현재 판단: …' 없음")
        else:
            if (m.group(1), m.group(2)) != (want_j, want_lean):
                errs.append(f"현재 판단 ({m.group(1)}, {m.group(2)}) != 규칙 ({want_j}, {want_lean})")
            where = next((i for i, x in enumerate(lines) if m.group(0) in x), 99)
            limit = 4 if kind == "data" else 1 if kind != "standalone" else None
            if kind == "standalone":
                first_sec = next((i for i, x in enumerate(lines) if x.startswith("## ")), 99)
                if where > first_sec:
                    errs.append("현재 판단이 첫 섹션 뒤에 있음")
            elif where > limit:
                errs.append(f"현재 판단이 {where + 1}째 줄(결론 먼저 아님)")
    if kind != "standalone":
        second = lines[1] if len(lines) > 1 else ""
        if second.startswith("지금 데이터로는") or "판단할 수 없습니다" in second.split(".")[0]:
            errs.append("첫 문장이 판단 회피")
        if len(lines) > 6:
            errs.append(f"{len(lines)}줄(6줄 이하)")
        if "## " in a or "규칙" in a:
            errs.append("후속 답에 섹션·규칙 설명")
    else:
        sw = strength_word(label, up, down, total)
        if label in ("상승 우위", "하락 우위") and f"{total}개 묶음 중 상승 {up}·하락 {down}·중립 {neutral}({sw}{label})" not in a:
            errs.append(f"묶음 비중 뒤 강도 '({sw}{label})' 없음")
        if not re.search(r"답변 생성: \d{4}-\d{2}-\d{2} \d{2}:\d{2} KST", a):
            errs.append("답변 생성 시각 없음")
    if kind in ("standalone", "verdict", "pick", "compare", "buy", "earnings"):
        errs += _check_power(a, period, label)
    if kind == "probability" and "검증된 상승·하락 확률은 없" not in a:
        errs.append("검증된 확률 없음 안내 없음")
    if kind == "compare":
        want_cmp = ("상승 근거가 더 많습니다" if up > down else "하락 위험이 더 많습니다" if down > up else "상승·하락이 비슷합니다")
        if f"묶음 표로는 {want_cmp}" not in a:
            errs.append(f"'묶음 표로는 {want_cmp}' 없음")
    if kind == "buy" and "권하지 않습니다" not in a:
        errs.append("매수·매도 권유 안 함 안내 없음")
    errs += [f"확률 지어냄: {x}" for x in _FAKE_PROB.findall(a)]
    errs += [f"매수·매도 권유: {x}" for x in _BUY_PUSH.findall(a)]
    return errs


# ---- 잠정실적(거래소 공정공시) 독립 파서: disclosure.content(DART 원문 앞 2,000자) ----

_UNIT24 = {"조원": 1e12, "억원": 1e8, "백만원": 1e6, "천원": 1e3, "원": 1.0}
_REPORT_NAME = {"11013": "1분기보고서", "11012": "반기보고서", "11014": "3분기보고서", "11011": "사업보고서"}


def _tok(s: str) -> float | None:
    s = s.replace(",", "")
    return float(s) if re.fullmatch(r"-?\d+(?:\.\d+)?", s) else None


def parse_prelim24(title: str, content: str) -> dict | None:
    """분기(3개월) 잠정실적이면 {year, quarter, basis, unit, 계정: (당해, 전년 동기, 누계)}. 자회사 실적·정정·월별·판매 실적은 None."""
    if "자회사" in title or title.startswith("[") or "잠정" not in title or "실적" not in title:
        return None
    p = re.search(r"당기실적 (\d{4})-(\d{2})-(\d{2}) ~ (\d{4})-(\d{2})-(\d{2})", content or "")
    u = re.search(r"단위 : (조원|억원|백만원|천원|원)", content or "")
    if not p or not u:
        return None
    y1, m1, d1, y2, m2, _ = map(int, p.groups())
    if d1 != 1 or m1 not in (1, 4, 7, 10) or y1 != y2 or m2 != m1 + 2:
        return None
    # 기준: 제목·표 머리 -> 공시가 자기를 설명하는 문장("별도재무제표기준 영업(잠정)실적") -> 표 머리 "별도실적내용"
    self_basis = re.search(r"(연결|별도)\s*(?:재무제표)?\s*기준(?:의|으로)?\s*(?:영업\s*)?\(?\s*잠정\s*\)?\s*(?:영업\s*)?실적", content)
    if "연결" in title or "연결실적내용" in content:
        basis = "연결"
    elif self_basis:
        basis = self_basis.group(1)
    elif "별도실적내용" in content:
        basis = "별도"
    else:
        basis = "기준 미표기"
    out = {"year": y1, "quarter": (m1 - 1) // 3 + 1, "basis": basis}
    mult = _UNIT24[u.group(1)]
    for key, name in (("revenue", "매출액"), ("operating_income", "영업이익"), ("net_income", "당기순이익")):
        m = re.search(rf"(?<!\S){name} 당해실적 (\S+) \S+ \S+ \S+ (\S+) \S+ \S+ 누계실적 (\S+)", content)
        if not m:
            out[key] = (None, None, None)
            continue
        cur, prev, cum = (_tok(x) for x in m.groups())
        out[key] = tuple(v * mult if v is not None else None for v in (cur, prev, cum))
    out["unit"] = mult
    return out


def _unit_of24(p: dict) -> float:
    return p.get("unit", 1.0)


def prelims24(db, company_name: str) -> list[dict]:
    rows = db.execute(text(
        "select d.title, d.content, d.disclosed_at, d.source_url from disclosure d join company c on c.id = d.company_id "
        "where c.name = :n and d.title like '%잠정%' order by d.disclosed_at"), {"n": company_name}).fetchall()
    out = []
    for title, content, at, url in rows:
        p = parse_prelim24(title, content or "")
        rcept = (re.search(r"rcptNo=(\d{14})", url or "") or [None, None])[1]
        if p and rcept:
            p.update(rcept=rcept, date=f"{rcept[:4]}-{rcept[4:6]}-{rcept[6:8]}", title=title)
            out.append(p)
        elif title.startswith("[") and "잠정" in title and rcept:
            ref = re.search(r"정정관련 공시서류제출일 (\d{4})(?:년 |-)(\d{1,2})(?:월 |-)(\d{1,2})", content or "")
            out.append({"correction": True, "rcept": rcept, "date": f"{rcept[:4]}-{rcept[4:6]}-{rcept[6:8]}",
                        "ref": f"{ref.group(1)}-{int(ref.group(2)):02d}-{int(ref.group(3)):02d}" if ref else None})
    return out


def _final_quarters(db, company_name: str) -> dict[tuple[int, int], dict]:
    """정기보고서 분기 값(1~3분기 = 당기, 4분기 = 연간 - 3분기 누적, 연결·별도 섞이면 없음)."""
    rows = db.execute(text(
        "select e.bsns_year, e.reprt_code, e.fs_div, e.revenue, e.operating_income, e.net_income, e.revenue_cum, e.operating_income_cum, "
        "e.net_income_cum, e.revenue_prev, e.operating_income_prev, e.net_income_prev, e.rcept_no, e.disclosed_date "
        "from quarterly_earnings e join company c on c.id = e.company_id where c.name = :n"), {"n": company_name}).fetchall()
    by = {(r[0], r[1]): r for r in rows}
    out = {}
    q_of = {"11013": 1, "11012": 2, "11014": 3}
    for (y, code), r in by.items():
        if code in q_of:
            out[(y, q_of[code])] = {"fs": r[2], "vals": [r[3], r[4], r[5]], "prev": [r[9], r[10], r[11]], "rcept": r[12],
                                    "date": r[13].isoformat(), "code": code}
    for (y, code), a in by.items():
        if code != "11011":
            continue
        qs = [by.get((y, c)) for c in ("11013", "11012", "11014")]
        if any(q is not None and q[2] != a[2] for q in qs):
            continue
        q3 = by.get((y, "11014"))
        vals = []
        for i in range(3):
            cum = q3[6 + i] if q3 is not None else None
            if cum is None and all(q is not None and q[3 + i] is not None for q in qs):
                cum = sum(float(q[3 + i]) for q in qs)
            vals.append(float(a[3 + i]) - float(cum) if a[3 + i] is not None and cum is not None else None)
        out[(y, 4)] = {"fs": a[2], "vals": vals, "prev": [None, None, None], "rcept": a[12], "date": a[13].isoformat(), "code": "11011"}
    for (y, q), v in out.items():
        p = out.get((y - 1, q))
        v["prev"] = [v["prev"][i] if v["prev"][i] is not None else (p["vals"][i] if p and p["fs"] == v["fs"] else None) for i in range(3)]
    return out


def eligible_prelims24(db, company_name: str) -> dict[tuple[int, int], dict]:
    """답변에 쓸 수 있는 잠정치(API.md 전망 4차): 같은 분기 정기보고서 없음, 정기보고서와 같은 기준(최근 정기보고서가 연결이면 연결,
    정기보고서가 없으면 연결), 2~4분기는 직전 분기 정기보고서 매출 누적 + 당해 = 잠정 누계(0.5% 또는 공시 단위 2개 허용).
    같은 분기·같은 기준 여러 건이면 나중 것."""
    finals = _final_quarters(db, company_name)
    basis = "별도" if finals and finals[max(finals)]["fs"] == "OFS" else "연결"
    cums = dict(((r[0], {"11013": 1, "11012": 2, "11014": 3}[r[1]]), r[2]) for r in db.execute(text(
        "select e.bsns_year, e.reprt_code, e.revenue_cum from quarterly_earnings e join company c on c.id = e.company_id "
        "where c.name = :n and e.reprt_code in ('11013', '11012', '11014')"), {"n": company_name}).fetchall())
    out = {}
    for p in prelims24(db, company_name):
        if p.get("correction") or p["basis"] != basis or (p["year"], p["quarter"]) in finals:
            continue
        out[(p["year"], p["quarter"])] = p
    for (y, q), p in list(out.items()):
        prev_cum, cur, cum = cums.get((y, q - 1)), p["revenue"][0], p["revenue"][2]
        if q > 1 and prev_cum is not None and cur is not None and cum is not None:
            unit = _unit_of24(p)
            if abs(float(prev_cum) + cur - cum) > max(abs(cum) * 0.005, 2 * unit):
                del out[(y, q)]
    return out


def latest_quarter24(db, company_name: str) -> tuple[tuple[int, int] | None, str | None, dict | None]:
    """가장 최근 분기와 출처("확정" 정기보고서 / "잠정" 공정공시). 같은 분기에 정기보고서가 있으면 잠정치는 쓰지 않는다."""
    finals = _final_quarters(db, company_name)
    pre = {k: v for k, v in eligible_prelims24(db, company_name).items()
           if v["revenue"][0] is not None and v["operating_income"][0] is not None}
    keys = set(finals) | set(pre)
    if not keys:
        return None, None, None
    k = max(keys)
    return (k, "확정", finals[k]) if k in finals else (k, "잠정", pre[k])


def _eok24(v: float | None) -> str | None:
    return None if v is None else f"{float(v) / 1e8:,.0f}"


def _yoy24(cur, prev) -> str | None:
    return f"{(float(cur) / float(prev) - 1) * 100:+.1f}" if cur is not None and prev is not None and float(prev) > 0 and float(cur) >= 0 else None


def _check_values24(line: str, vals: list, prevs: list, tag: str) -> list[str]:
    errs = []
    for i, name in enumerate(("매출액", "영업이익", "당기순이익")):
        if vals[i] is None:
            continue
        mm = re.search(rf"{name} (-?[\d,]+)억 원(?:, 전년 동기 대비 ([+-][\d.]+)%)?", line)
        if not mm:
            errs.append(f"{tag} {name} 없음")
            continue
        if mm.group(1) != _eok24(vals[i]):
            errs.append(f"{tag} {name} {mm.group(1)} != {_eok24(vals[i])}")
        if mm.group(2) != _yoy24(vals[i], prevs[i]):
            errs.append(f"{tag} {name} 증감률 {mm.group(2)} != {_yoy24(vals[i], prevs[i])}")
    return errs


def check_earnings_line24(db, line: str, company_name: str) -> list[str]:
    """실적 한 줄: "YYYY년 Q분기(연결|별도|기준 미표기, 잠정|확정[, 연간 - 3분기 누적]): …" 값·증감률·출처를 SQL과 독립 파서로 대조."""
    m = re.search(r"(\d{4})년 ([1-4])분기\((연결|별도|기준 미표기), (잠정|확정)(?:, 연간 - 3분기 누적)?\)", line)
    if not m:
        return [f"실적 줄 형식: {line[:60]}"]
    y, q, basis, kind = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
    tag = f"{y} {q}분기 {kind}"
    if kind == "확정":
        f = _final_quarters(db, company_name).get((y, q))
        if f is None:
            return [f"{tag}: DB 정기보고서 없음"]
        errs = []
        if (basis == "연결") != (f["fs"] == "CFS") or f"DART 접수번호 {f['rcept']}" not in line or f"접수일 {f['date']}" not in line:
            errs.append(f"{tag} 기준·접수번호·접수일 불일치")
        if _REPORT_NAME[f["code"]] not in line:
            errs.append(f"{tag} 보고서 이름({_REPORT_NAME[f['code']]}) 없음")
        return errs + _check_values24(line, f["vals"], f["prev"], tag)
    p = eligible_prelims24(db, company_name).get((y, q))
    if p is None:
        return [f"{tag}: 쓸 수 있는 잠정실적 공시 없음(기준·누적 조건)"]
    if _final_quarters(db, company_name).get((y, q)) is not None:
        return [f"{tag}: 정기보고서가 있는데 잠정치를 씀"]
    errs = []
    if basis != p["basis"] or f"DART 접수번호 {p['rcept']}" not in line or f"발표일 {p['date']}" not in line:
        errs.append(f"{tag} 기준·접수번호·발표일 불일치(기대 {p['basis']}, {p['rcept']}, {p['date']})")
    if "잠정실적 공정공시" not in line:
        errs.append(f"{tag} 출처(잠정실적 공정공시) 없음")
    corrected = [c for c in prelims24(db, company_name) if c.get("correction") and c.get("ref") == p["date"]]
    if corrected:
        return errs + ([] if "정정공시" in line else [f"{tag}: 정정공시({corrected[-1]['rcept']}) 안내 없음"])
    vals = [p[k][0] for k in ("revenue", "operating_income", "net_income")]
    prevs = [p[k][1] for k in ("revenue", "operating_income", "net_income")]
    return errs + _check_values24(line, vals, prevs, tag)


def check_earnings24(db, answer: str, company_name: str, quarter: str | None = None) -> list[str]:
    """(24차 earnings 후속 답) 최신 실적(잠정 포함)·출처·발표 뒤 주가. quarter("2026-3")를 물었는데 없으면 없다고 밝혀야 한다."""
    a = answer or ""
    k, src, info = latest_quarter24(db, company_name)
    if k is None:
        return [] if "실적 데이터 없음" in a or "실적 정보를 보여 주지 않습니다" in a else ["실적 없음 안내 없음"]
    line = next((x for x in a.splitlines() if x.startswith("- 최신 실적: ")), None)
    if line is None:
        return ["'- 최신 실적: ' 줄 없음"]
    errs = []
    if f"{k[0]}년 {k[1]}분기" not in line or f", {src}" not in line:
        errs.append(f"최신 분기 {k[0]}년 {k[1]}분기({src})가 아님: {line[:40]}")
    errs += check_earnings_line24(db, line, company_name)
    if "신호 판정 표에 넣지 않" not in a:
        errs.append("실적이 신호 판정에 들어가지 않는다는 안내 없음")
    if quarter:
        y, q = map(int, quarter.split("-"))
        have = (y, q) in _final_quarters(db, company_name) or any(
            not p.get("correction") and (p["year"], p["quarter"]) == (y, q) for p in prelims24(db, company_name))
        if not have and f"{y}년 {q}분기 실적: 아직 공시(잠정·확정)가 없습니다" not in a:
            errs.append(f"{y}년 {q}분기 없음 안내 없음")
    mm = re.search(r"발표 뒤 주가: 발표일\((\S+)\) 직전 거래일 (\S+) 종가 ([\d,]+)원 → 기준일 (\S+) 종가 ([\d,]+)원\(([+-][\d.]+)%\)", a)
    if not mm:
        errs.append("발표 뒤 주가 줄 없음")
    else:
        d, before, c0, base, c1, pct = mm.groups()
        want_d = info["date"]
        rows = _rows(db, company_name, base, 260)
        prior = [r for r in rows if r[0].isoformat() < want_d]
        if d != want_d:
            errs.append(f"발표일 {d} != {want_d}")
        elif not prior or prior[-1][0].isoformat() != before or _num(c0) != prior[-1][1]:
            errs.append(f"직전 거래일 종가 {before} {c0} != {prior[-1] if prior else None}")
        elif rows[-1][0].isoformat() != base or _num(c1) != rows[-1][1] or pct != f"{(rows[-1][1] / prior[-1][1] - 1) * 100:+.2f}":
            errs.append("기준일 종가·등락률 불일치")
    return errs


def _before_close24(gen: str) -> bool:
    from datetime import datetime
    t = datetime.strptime(gen, "%Y-%m-%d %H:%M")
    return t.weekday() < 5 and t.hour * 60 + t.minute < _MARKET_CLOSE_MIN


def _expected_day24(gen: str):
    """종가가 있어야 할 가장 최근 평일. 평일 15:30 전이면 직전 평일(KRX 휴장일 달력은 없다)."""
    from datetime import datetime, timedelta
    d = datetime.strptime(gen, "%Y-%m-%d %H:%M").date()
    if _before_close24(gen):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def status24(db, company_name: str, base: str, gen: str) -> tuple[str, str]:
    """시세 상태(기대 거래일, 분류). 최신 / 종목만 누락(지수에는 그 뒤 날짜가 있음) / 수집 실패(기대 거래일 장 마감 뒤 prices 실패 기록) /
    휴장일 추정(장 마감 뒤 prices 성공 기록이 있는데 시장 전체에 그날 시세 없음) / 미수집(장 마감 뒤 prices 실행 기록 없음)."""
    exp = _expected_day24(gen)
    if base >= exp.isoformat():
        return exp.isoformat(), "최신"
    idx = db.execute(text("select max(price_date) from market_index where index_code = 'KS11'")).scalar()
    if idx is not None and idx.isoformat() > base:
        return exp.isoformat(), "종목만 누락"
    runs = db.execute(text(
        "select status from data_refresh_log where step = 'prices' and status in ('success', 'failed') "
        "and started_at >= (cast(:d as date) + time '15:30') at time zone 'Asia/Seoul' order by id desc"), {"d": exp.isoformat()}).fetchall()
    if runs and runs[0][0] == "failed":
        return exp.isoformat(), "수집 실패"
    if runs:
        return exp.isoformat(), "휴장일 추정"
    return exp.isoformat(), "미수집"


def check_data24(db, answer: str, company_name: str) -> list[str]:
    """(24차 data 후속 답) 답변 생성 시각과 시세 기준일을 따로 쓰고, 지연 분류가 독립 계산과 같은지."""
    a = answer or ""
    m = re.search(r"이 답변은 (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) KST에 만들었고, 시세 기준일은 (\d{4}-\d{2}-\d{2}) 종가입니다", a)
    if not m:
        return ["생성 시각·시세 기준일 문장 없음"]
    gen, base = m.groups()
    errs = []
    op = "<" if _before_close24(gen) else "<="  # 장중이면 그날 행(장중 값)은 기준일이 아니다
    last = db.execute(text(
        "select max(s.price_date) from stock_price s join company c on c.id = s.company_id where c.name = :n "
        f"and s.price_date {op} cast(:day as date)"), {"n": company_name, "day": gen[:10]}).scalar()
    if last is None or last.isoformat() != base:
        errs.append(f"시세 기준일 {base} != {last}")
    exp, status = status24(db, company_name, base, gen)
    if f"({status}" not in a:
        errs.append(f"지연 분류 ({status}) 없음")
    if status != "최신" and f"기대 거래일 {exp}" not in a:
        errs.append(f"기대 거래일 {exp} 없음")
    return errs


# ---------- batch 22(2026-11-04, 25차): 단기 방향과 국면 분리, 첫 문장 방향, 신뢰도, 짧은 후속 답, 시세 신선도 문구 ----------
# 규칙은 API.md "전망 5차" 절을 그대로 옮겼다(app/outlook.py를 import하지 않는다).

DIRECTIONS = ("상승 쪽", "하락 쪽", "중립", "판단 유보")
_DIR_TITLE = re.compile(r"^.+?\([0-9A-Z]{6}\) · 향후 \d+거래일 · 방향: (상승 쪽|하락 쪽|중립|판단 유보)(?:\(신뢰도 (낮음|보통|높음)\))?")
_DIR_FIRST = {
    "상승 쪽": ("상승 쪽입니다", "하나만 고르면 상승 쪽입니다"),
    "하락 쪽": ("하락 쪽입니다", "하나만 고르면 하락 쪽입니다"),
    "중립": ("현재는 상승과 하락 중 어느 쪽도 뚜렷하게 우세하지 않습니다", "하나만 고르라고 해도 현재는 상승과 하락 중 어느 쪽도 뚜렷하게 우세하지 않습니다"),
    "판단 유보": ("데이터가 부족해 현재는 방향 판단을 보류하겠습니다", "하나만 고르라고 해도 데이터가 부족해 현재는 방향 판단을 보류하겠습니다"),
}
_CONTRADICTION25 = re.compile(r"중립[^.\n]{0,25}굳이 고르면|현재 판단: 중립\(굳이")
_PROB25 = re.compile(r"(?:상승|하락|오를|내릴)\s*확률(?:은|이|는)?\s*(?:약\s*)?\d|확률(?:은|이|는)?\s*\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*%\s*(?:의\s*)?확률")
_LATE_WORD25 = re.compile(r"영업일 늦습니다|영업일 늦어")
_LAG25 = re.compile(r"기대 거래일\((\d{4}-\d{2}-\d{2})\)보다 (\d+)영업일 (이전 데이터|지연)")
MAX_LINES25 = {"oneline": 1, "pick": 3, "verdict": 5, "why": 6, "buy": 6, "compare": 6, "probability": 6, "risk": 6, "earnings": 6,
               "data": 6}


def direction_from_label(label: str) -> str:
    """단기 방향 = 묶음 판정 그대로(국면과 일치를 요구하지 않는다)."""
    return {"상승 우위": "상승 쪽", "하락 우위": "하락 쪽", "중립": "중립"}.get(label, "판단 유보")


def confidence25(period: str, label: str, up: int, down: int, total: int, lag: int) -> str | None:
    """신뢰도(방향이 상승 쪽·하락 쪽일 때만): 과거 검증이 유의하지 않거나 없으면 낮음, 유의하지만 약한 우위(우세 묶음 < 절반)이거나
    시세가 2영업일 이상 지연이면 보통, 그 밖은 높음."""
    if label not in ("상승 우위", "하락 우위"):
        return None
    r = _backtest_row24(period, label)
    if r is None or not r.get("significant"):
        return "낮음"
    lead = up if label == "상승 우위" else down
    return "보통" if lead * 2 < total or lag >= 2 else "높음"


def _lag_of(answer: str) -> int:
    m = _LAG25.search(answer or "")
    return int(m.group(2)) if m else 0


def check_direction25(db, answer: str, company_name: str, period: str, kind: str) -> list[str]:
    """(25차) 제목 줄의 방향·신뢰도 = 묶음 비중으로 다시 계산한 값, 첫 내용 문장이 그 방향으로 시작, 국면(SQL)은 따로 표시,
    모순·확률 표현 없음, 종류별 줄 수, 시세 신선도 문구. kind: standalone 또는 후속 답 종류."""
    a = answer or ""
    lines = [x for x in a.splitlines() if x.strip()]
    errs = []
    t = title_of(lines[0] if lines else "")
    if not t or t["name"] != company_name or t["period"] != period:
        errs.append(f"제목(종목·기간) {lines[0][:40] if lines else None!r}")
    m = _DIR_TITLE.match(lines[0] if lines else "")
    tally = _TALLY24.search(a)
    if not m:
        return errs + ["제목 줄에 '방향: …' 없음"]
    if not tally:
        return errs + ["'N개 묶음 중 상승 a·하락 b·중립 c' 없음"]
    total, up, down, neutral = map(int, tally.groups())
    label = label_from_tally(up, down, total)
    if re.search(r"(?:데이터가 부족합니다|시세가 없습니다\([^)]*\))\. 판단을 유보합니다", a):
        label = "판단 유보"
    want = direction_from_label(label)
    if m.group(1) != want:
        errs.append(f"제목 방향 {m.group(1)} != 묶음 비중으로 계산한 {want}")
    want_conf = confidence25(period, label, up, down, total, _lag_of(a))
    if m.group(2) != want_conf:
        errs.append(f"신뢰도 {m.group(2)} != {want_conf}")
    # 국면은 따로(상태 줄), 독립 SQL과 같아야 한다. 방향이 국면에 끌려가지 않았는지는 위 방향 비교가 본다.
    st = _STATE.search(a)
    if not st:
        errs.append("국면(현재는 … (기준일 … 종가)) 없음")
    elif db is not None:
        want_regime = regime_expected(db, company_name, st.group("date"))
        if st.group("regime") not in (want_regime, REGIMES[5]):
            errs.append(f"국면 {st.group('regime')} != {want_regime}")
    # 상태 줄("현재는 … (기준일 … 종가)")만 건너뛴다. 2026-11-04 dev23_05에서 발견: "현재는 상승과 하락 중 …"(중립 문장)까지 건너뛰던 결함
    first_line = lines[0] if kind == "oneline" else next(
        (x for x in lines[1:] if not (x.startswith(("- ", "데이터 경고")) or _STATE.match(x))), "")
    if kind == "oneline":
        first_line = first_line[m.end():].lstrip(" ·")
    if kind == "data":
        # 2026-11-04(승인 뒤 기준 조정): 데이터 질문은 생성 시각·기준일을 먼저 답하고 방향은 "- 방향: " 줄에 쓴다
        first_line = next((x[len("- 방향: "):] for x in lines if x.startswith("- 방향: ")), "")
    starts = _DIR_FIRST[want][1 if kind == "pick" else 0]
    if not first_line.startswith(starts):
        errs.append(f"첫 문장이 방향({want})으로 시작하지 않음: {first_line[:40]!r}")
    if kind == "standalone":
        first_sec = next((i for i, x in enumerate(lines) if x.startswith("## ")), 99)
        where = next((i for i, x in enumerate(lines) if x.startswith(starts)), 99)
        if where > first_sec:
            errs.append("방향 문장이 첫 섹션 뒤에 있음")
    else:
        limit = MAX_LINES25.get(kind, 6)
        if len(lines) > limit:
            errs.append(f"{len(lines)}줄({kind}는 {limit}줄 이하)")
        if "## " in a or "규칙" in a:
            errs.append("후속 답에 섹션·규칙 설명")
    if kind == "why":
        body = " ".join(lines[1:])
        if "이유" not in body:
            errs.append("이유 문장 없음")
    if kind == "buy" and "권하지 않습니다" not in a:
        errs.append("매수·매도 권유 안 함 안내 없음")
    if kind == "probability" and "확률" in a and "검증된 상승·하락 확률은 없" not in a:
        errs.append("검증된 확률 없음 안내 없음")
    errs += [f"모순 표현: {x}" for x in _CONTRADICTION25.findall(a)]
    errs += [f"확률 표현: {x}" for x in _PROB25.findall(a)]
    for line in lines:
        if "상승 비율" in line and "과거" not in line:
            errs.append(f"과거 빈도를 과거라고 밝히지 않음: {line[:40]}")
    errs += [f"시세 신선도 문구: {x}" for x in _LATE_WORD25.findall(a)]
    errs += [f"매수·매도 권유: {x}" for x in _BUY_PUSH.findall(a)]
    return errs


def check_stable25(answer: str, turn_answers: list[str], period: str) -> list[str]:
    """같은 종목·기간이면 앞 턴(표식이 있는 답)과 방향이 같아야 한다."""
    m = _DIR_TITLE.match((answer or "").splitlines()[0] if answer else "")
    t = title_of(answer or "")
    errs = []
    for prev in turn_answers:
        pm = _DIR_TITLE.match(prev.splitlines()[0] if prev else "")
        pt = title_of(prev or "")
        if not (m and pm and t and pt) or (pt["name"], pt["period"]) != (t["name"], t["period"]):
            continue
        if pm.group(1) != m.group(1):
            errs.append(f"앞 턴 방향 {pm.group(1)} -> 이번 {m.group(1)}")
    return errs


def check_v24_content(db, answer: str, company_name: str, period: str, kind: str, quarter: str | None = None) -> dict[str, list[str]]:
    """(2026-11-04, 승인 뒤 기준 조정) 24차 기준에서 형식(현재 판단 문구·옛 판단 규칙)을 뺀 내용 검사만 남긴다:
    과거 검증 표본·95% 구간·예측력 문장(그 줄이 있는 종류), 최신 실적·출처·발표 뒤 주가(earnings), 시세 상태(data)."""
    a = answer or ""
    out = {}
    if kind in ("standalone", "verdict", "compare", "buy", "earnings"):
        tally = _TALLY24.search(a)
        if not tally:
            out["power24"] = ["'N개 묶음 중 …' 없음"]
        else:
            total, up, down, _ = map(int, tally.groups())
            label = label_from_tally(up, down, total)
            if re.search(r"(?:데이터가 부족합니다|시세가 없습니다\([^)]*\))\. 판단을 유보합니다", a):
                label = "판단 유보"
            out["power24"] = _check_power(a, period, label)
    if kind == "earnings" and db is not None:
        out["earnings24"] = check_earnings24(db, a, company_name, quarter)
    if kind == "data" and db is not None:
        out["data24"] = check_data24(db, a, company_name)
    return out


# ---------- batch 24(2026-11-04 밤, 26차): 간결·상세 모드, 첫 문장 방향 배치, 근거와 묶음 판정 일치, 뉴스·공시 날짜 ----------
# 규칙은 API.md "전망 6차" 절을 그대로 옮겼다(app/outlook.py를 import하지 않는다).

DETAIL_SECTIONS26 = ("종합 판단", "상승을 뒷받침하는 근거", "하락 위험 근거", "앞으로 확인할 사항", "과거 검증", "기술적 지표", "시장 상황",
                     "신호 판정 상세", "불확실성과 한계", "데이터 기준일·출처")
BRIEF_LINES26 = ("- 상승 근거: ", "- 하락 위험: ", "- 과거 검증: ", "- 시장 국면: 현재는 ")
MAX_BRIEF_LINES26 = 8
_TAG26 = re.compile(r"\[([^\]]+)\]")
_CROSS_ALONE26 = re.compile(r"^- \[[^\]]+\] (?:\d{4}-\d{2}-\d{2} )?(?:골든크로스|데드크로스)")
_DISC_LINE26 = re.compile(r"(?:- 공시 (\d{4}-\d{2}-\d{2}) 접수: \"(.+?)\"$|^  - (\d{4}-\d{2}-\d{2}) \"(.+?)\" 공시가 있었습니다\.$)")
_NEWS_LINE26 = re.compile(r"(?:- 뉴스 (\d{4}-\d{2}-\d{2}) (?:\([^)]*\) )?\"(.+?)\"라는 제목의 기사|^  - (\d{4}-\d{2}-\d{2}) \"(.+?)\"라는 기사가 있었습니다\.$)")
_RCEPT_DATE26 = re.compile(r"(?:접수·발표일|접수일|발표일) (\d{4}-\d{2}-\d{2}), DART 접수번호 (\d{14})")


def check_mode26(answer: str, mode: str) -> list[str]:
    """brief: 섹션 없음, 빈 줄 빼고 8줄 이하, 상승 근거·하락 위험·과거 검증·시장 국면 줄. detail: 상세 섹션 유지."""
    a = answer or ""
    lines = [x for x in a.splitlines() if x.strip()]
    if mode == "brief":
        errs = []
        if "## " in a:
            errs.append("간결 답에 섹션")
        if len(lines) > MAX_BRIEF_LINES26:
            errs.append(f"간결 답 {len(lines)}줄({MAX_BRIEF_LINES26}줄 이하)")
        errs += [f"'{p.strip()}' 줄 없음" for p in BRIEF_LINES26 if not any(x.startswith(p) for x in lines)]
        return errs
    titles = [x[3:].strip() for x in lines if x.startswith("## ")]
    return [f"상세 섹션 '{t}' 없음" for t in DETAIL_SECTIONS26 if t not in titles]


def check_layout26(answer: str, company_name: str, period: str, mode: str) -> list[str]:
    """제목 줄(종목·기간·방향) 다음 첫 줄이 방향 문장, 국면(현재는 … (기준일 … 종가))은 따로, 상세면 첫 섹션 종합 판단 2~3문장."""
    lines = [x for x in (answer or "").splitlines() if x.strip()]
    errs = []
    t = title_of(lines[0] if lines else "")
    if not t or t["name"] != company_name or t["period"] != period or not _DIR_TITLE.match(lines[0]):
        errs.append(f"제목 줄 {lines[0][:50] if lines else None!r}")
    m = _DIR_TITLE.match(lines[0]) if lines else None
    if m:
        starts = _DIR_FIRST[m.group(1)][0]
        if len(lines) < 2 or not lines[1].startswith(starts):
            errs.append(f"제목 다음 줄이 방향 문장이 아님: {(lines[1] if len(lines) > 1 else '')[:40]!r}")
    first_sec = next((i for i, x in enumerate(lines) if x.startswith("## ")), len(lines))
    if not any(_STATE.search(x) for x in lines[:first_sec]):
        errs.append("국면(현재는 … (기준일 … 종가))이 머리말에 없음")
    if mode == "detail":
        sec = sections(answer)
        first = next(iter(sec), None)
        if first != "종합 판단":
            errs.append(f"첫 섹션 {first}")
        else:
            para = next((x for x in sec[first] if not x.startswith(("-", " "))), "")
            n = len(_SENTENCE_END.findall(para))
            if not 2 <= n <= 3:
                errs.append(f"종합 판단 {n}문장")
    return errs


def _tags_before(text: str, stops=("엇갈린 묶음", "추세 참고")) -> list[str]:
    cut = min([text.find(s) for s in stops if s in text] or [len(text)])
    return _TAG26.findall(text[:cut])


def check_evidence26(answer: str, mode: str) -> list[str]:
    """상승 근거는 상승 쪽 묶음, 하락 위험은 하락 쪽 묶음에서만. 엇갈린 묶음은 중립 묶음. 교차(골든·데드크로스)를 따로 세운 항목 없음.
    상세: 판정 상세의 묶음 줄로 대조. 간결: 묶음 비중(상승 a·하락 b) 안에서 서로 다른 묶음 수."""
    a = answer or ""
    errs = []
    if mode == "detail":
        full = {g.group("group").strip(): g.group("vote") for g in _GROUP_LINE23.finditer(a)}
        if not full:
            return ["묶음 줄 없음"]

        class _Votes(dict):
            """근거의 짧은 묶음 이름([MACD])을 판정 상세의 긴 이름(MACD(12,26,9))과 맞춘다(고정 뒤 결함 수정, 측정 전)."""
            def get(self, k, default=None):
                return next((v for n, v in full.items() if n == k or n.startswith(k + "(")), default)
        votes = _Votes()
        sec = sections(a)
        for title, want in (("상승을 뒷받침하는 근거", "상승 쪽"), ("하락 위험 근거", "하락 쪽")):
            for line in sec.get(title, []):
                if _CROSS_ALONE26.match(line):
                    errs.append(f"교차를 따로 세운 근거: {line[:40]}")
                if line.startswith("- 엇갈린 묶음"):
                    for g in _TAG26.findall(line):
                        if votes.get(g) != "중립":
                            errs.append(f"엇갈린 묶음 {g}의 판정이 {votes.get(g)}")
                    continue
                if line.startswith(("- 추세 참고", "  ")) or "판정된 묶음은 없습니다" in line:
                    continue
                tags = _TAG26.findall(line)
                if not tags:
                    errs.append(f"{title}에 묶음 표시 없는 항목: {line[:40]}")
                for g in tags[:1]:
                    if votes.get(g) != want:
                        errs.append(f"{title}의 [{g}] 판정이 {votes.get(g)}")
        return errs
    tally = _TALLY24.search(a)
    if not tally:
        return ["묶음 비중 없음"]
    _, up, down, _ = map(int, tally.groups())
    lines = a.splitlines()
    up_line = next((x for x in lines if x.startswith("- 상승 근거: ")), "")
    down_line = next((x for x in lines if x.startswith("- 하락 위험: ")), "")
    up_tags, down_tags = set(_tags_before(up_line)), set(_tags_before(down_line))
    if len(up_tags) > up:
        errs.append(f"상승 근거 묶음 {sorted(up_tags)} > 상승 묶음 {up}개")
    if len(down_tags) > down:
        errs.append(f"하락 위험 묶음 {sorted(down_tags)} > 하락 묶음 {down}개")
    if up_tags & down_tags:
        errs.append(f"같은 묶음이 상승·하락 양쪽: {sorted(up_tags & down_tags)}")
    if re.search(r"\[[^\]]+\] (?:\d{4}-\d{2}-\d{2} )?(?:골든크로스|데드크로스)", up_line + down_line):
        errs.append("교차를 따로 세운 근거")
    return errs


def _disclosure_dates(db, company_name: str) -> dict[str, set[str]]:
    rows = db.execute(text(
        "select d.title, d.source_url, to_char(d.disclosed_at at time zone 'Asia/Seoul', 'YYYY-MM-DD') from disclosure d "
        "join company c on c.id = d.company_id where c.name = :n"), {"n": company_name}).fetchall()
    out: dict[str, set[str]] = {}
    for title, url, kst in rows:
        m = re.search(r"rcptNo=(\d{8})", url or "")
        d = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]}" if m else kst
        out.setdefault(title, set()).add(d)
    return out


def check_dates26(db, answer: str, company_name: str) -> list[str]:
    """공시 날짜 = DART 접수번호의 날짜(없으면 한국 시간 접수일), 뉴스 날짜 = 한국 시간 발행일, 같은 자료는 어디서나 같은 날짜,
    실적 줄의 접수(·발표)일 = 접수번호 날짜."""
    a = answer or ""
    errs, seen = [], {}
    disc = _disclosure_dates(db, company_name) if db is not None else {}
    for line in a.splitlines():
        m = _DISC_LINE26.search(line)
        if m:
            d, title = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            if db is not None and d not in disc.get(title, set()):
                errs.append(f"공시 \"{title[:20]}\" 날짜 {d} != {sorted(disc.get(title, set()))}")
            if seen.setdefault(("공시", title), d) != d:
                errs.append(f"같은 공시 \"{title[:20]}\"가 {seen[('공시', title)]}와 {d}로 다르게 표시")
        m = _NEWS_LINE26.search(line)
        if m:
            d, title = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            if db is not None:
                # 업종·산업(간접) 뉴스는 다른 종목 기사라 종목을 가리지 않고 제목·한국 시간 발행일로 본다(고정 뒤 결함 수정, 측정 전)
                ok = db.execute(text(
                    "select count(*) from news n where n.title = :t "
                    "and to_char(n.published_at at time zone 'Asia/Seoul', 'YYYY-MM-DD') = :d"),
                    {"t": title, "d": d}).scalar()
                if not ok:
                    errs.append(f"뉴스 \"{title[:20]}\" 날짜 {d}가 한국 시간 발행일과 다름")
            if seen.setdefault(("뉴스", title), d) != d:
                errs.append(f"같은 기사 \"{title[:20]}\"가 두 날짜로 표시")
        for d, rcept in _RCEPT_DATE26.findall(line):
            if rcept[8:10] == "80" and d != f"{rcept[:4]}-{rcept[4:6]}-{rcept[6:8]}":
                errs.append(f"거래소 공시 {rcept}의 접수·발표일 {d}가 접수번호 날짜와 다름")
    return errs


def check_consistency26(answer: str, turn_answers: list[str]) -> list[str]:
    """같은 종목·기간의 앞 턴이 간결 답이고 이번이 상세 답이면(또는 반대) 근거 묶음이 같은 쪽이어야 한다."""
    def sides(a: str) -> tuple[set, set] | None:
        if "## " in a:
            sec = sections(a)
            up = {t for x in sec.get("상승을 뒷받침하는 근거", []) for t in _tags_before(x)[:1]}
            down = {t for x in sec.get("하락 위험 근거", []) if not x.startswith(("- 엇갈린", "- 추세 참고")) for t in _tags_before(x)[:1]}
            return up, down
        lines = a.splitlines()
        up_line = next((x for x in lines if x.startswith("- 상승 근거: ")), None)
        down_line = next((x for x in lines if x.startswith("- 하락 위험: ")), None)
        if up_line is None:
            return None
        return set(_tags_before(up_line)), set(_tags_before(down_line or ""))
    errs = []
    t, cur = title_of(answer or ""), sides(answer or "")
    for prev in turn_answers:
        pt, ps = title_of(prev or ""), sides(prev or "")
        if not (t and pt and cur and ps) or (pt["name"], pt["period"]) != (t["name"], t["period"]):
            continue
        small, big = (ps, cur) if len(ps[0] | ps[1]) <= len(cur[0] | cur[1]) else (cur, ps)
        if not small[0] <= big[0] or not small[1] <= big[1]:
            errs.append(f"간결·상세 근거 묶음 불일치: {small} vs {big}")
    return errs


def check_filing26(db, answer: str, company_name: str) -> list[str]:
    """공시 날짜 질문: 첫 내용 줄에 최신 실적 공시의 접수일(접수번호 날짜)과 접수번호를 먼저 쓴다."""
    lines = [x for x in (answer or "").splitlines() if x.strip()]
    first = lines[1] if len(lines) > 1 else ""
    k, src, info = latest_quarter24(db, company_name)
    if k is None:
        return [] if "실적 공시를 찾지 못했습니다" in first else ["실적 공시 없음 안내 없음"]
    want_d, want_r = info.get("date"), info.get("rcept")
    errs = []
    if want_d not in first or (want_r and want_r not in first) or "접수" not in first:
        errs.append(f"첫 줄에 접수일 {want_d}·접수번호 {want_r} 없음: {first[:50]!r}")
    return errs


def check_signal26(answer: str, term: str) -> list[str]:
    """신호 의미 질문: 그 신호의 정의와 지금 값, 그 신호가 속한 묶음의 판정(개별 신호 ≠ 묶음 표)을 6줄 이하로."""
    a = answer or ""
    lines = [x for x in a.splitlines() if x.strip()]
    errs = []
    if term not in a:
        errs.append(f"'{term}' 설명 없음")
    if not re.search(r"묶음(?:은|의 판정은) (?:상승 쪽|하락 쪽|중립)", a):
        errs.append("속한 묶음의 판정 없음")
    if len(lines) > 6 or "## " in a:
        errs.append(f"{len(lines)}줄·섹션")
    return errs
