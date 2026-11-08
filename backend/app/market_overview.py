"""종목 없는 시장 상황 질문(2026-11-07 35차): "지금 시장 상황이 어때?", "요즘 증시 분위기 어때?".

33~35차 live: 이 질문은 LLM 경로에서 "해당 종목을 찾지 못했습니다"로 끝났다(market_tool은 종목이 있어야 한다).
저장된 market_index(KS11·KQ11)와 같은 날 stock_price(주식, ETF 제외)의 상승·하락 종목 수로 코드가 요약한다. 전망·권유는 하지 않는다.
수익률은 행(거래일) 기준이다(1·5·20거래일, 기준일 종가 / N거래일 전 종가 - 1). 기준일·지연은 app.quote_freshness.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import text

from app import quote_freshness

_MARKET_RE = re.compile(r"시장|증시|장\s*(?:분위기|상황)|주식\s*시장|코스피|코스닥|kospi|kosdaq", re.I)
_STATE_RE = re.compile(r"상황|분위기|어때|어떻|어떤가|흐름|동향|요즘|근황|현황")
_NOT_RE = re.compile(r"대비|보다|비교|종목\s*만|개별|왜|이유|원인|전망|앞으로|향후|오를|내릴|사도|살까|사야|추천")
_NAMES = {"KS11": "코스피", "KQ11": "코스닥"}
WINDOWS = (1, 5, 20)


def is_overview_question(question: str) -> bool:
    q = question or ""
    return bool(_MARKET_RE.search(q) and _STATE_RE.search(q) and not _NOT_RE.search(q))


def compute(db) -> dict:
    out = {"indexes": {}, "breadth": None}
    for code in _NAMES:
        rows = db.execute(text("SELECT price_date, close_price FROM market_index WHERE index_code = :c "
                               "ORDER BY price_date DESC LIMIT 21"), {"c": code}).all()
        if not rows:
            continue
        closes = [float(r[1]) for r in rows]
        out["indexes"][code] = {"date": rows[0][0], "close": closes[0],
                                "returns": {n: (closes[0] / closes[n] - 1) * 100 if len(closes) > n and closes[n] else None
                                            for n in WINDOWS}}
    d = db.execute(text("SELECT max(p.price_date) FROM stock_price p JOIN company c ON c.id = p.company_id WHERE c.asset_type = 'stock'")).scalar()
    if d is not None:
        row = db.execute(text("SELECT count(*) FILTER (WHERE p.change_pct > 0), count(*) FILTER (WHERE p.change_pct < 0), "
                              "count(*) FILTER (WHERE p.change_pct = 0), count(*) FROM stock_price p JOIN company c ON c.id = p.company_id "
                              "WHERE c.asset_type = 'stock' AND p.price_date = :d"), {"d": d}).first()
        out["breadth"] = {"date": d, "up": row[0], "down": row[1], "flat": row[2], "total": row[3]}
    return out


def answer(data: dict, now: datetime) -> str:
    if not data["indexes"]:
        return "시장 지수 데이터가 없어 시장 상황을 정리할 수 없습니다."
    lines = ["## 시장 상황(저장된 지수 종가 기준)", ""]
    dates = []
    for code, v in data["indexes"].items():
        dates.append(v["date"])
        r = v["returns"]
        parts = [f"{n}거래일 {r[n]:+.2f}%" for n in WINDOWS if r.get(n) is not None]
        lines.append(f"- {_NAMES[code]}: {v['date'].isoformat()} 종가 {v['close']:,.2f}" + (f" ({', '.join(parts)})" if parts else ""))
    b = data.get("breadth")
    if b and b["total"]:
        lines.append(f"- 같은 날 주식 {b['total']}종목(수집 대상) 중 상승 {b['up']}, 하락 {b['down']}, 보합 {b['flat']}종목"
                     f"({b['date'].isoformat()} 종가 기준)")
    lines += ["", f"- {quote_freshness.line(min(dates), now)}",
              "- 지난 시세를 정리한 것이며 앞으로의 방향을 예측하거나 매매를 권유하는 것이 아닙니다. 종목과 시장을 비교하려면 "
              "\"삼성전자 최근 한 달 코스피 대비 어때?\"처럼 물어보세요."]
    return "\n".join(lines)
