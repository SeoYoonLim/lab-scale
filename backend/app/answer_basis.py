"""LLM 답의 데이터 기준 시점 보장(48차, 2026-11-13).

48차 실측: 종목 분석 답이 시세 수치를 말하면서 그 데이터의 날짜를 밝히지 않았다("셀트리온의 주가는 최근 1일 동안 1.21% 상승하였습니다.").
데이터가 오래됐을 때만 붙던 안내(app.answer_guard.freshness_note)로는 어제 종가처럼 최신 데이터의 기준일이 빠졌다.

ensure_basis(answer, records, now):
- 주가 도구 결과(stock_tool, found)가 있는데 답에 그 마지막 시세 날짜가 없으면, 단순 시세 답과 같은 표준 한 줄
  (app.quote_freshness.line: "데이터 기준일: YYYY-MM-DD 종가(DB에 저장된 종가이며 실시간 시세가 아닙니다)." + 지연이면 지연 안내)을 붙인다.
  날짜는 2026-11-12 / 2026년 11월 12일 / 2026.11.12 / 11월 12일 표기를 모두 인정한다. 다른 날짜(기간 최고가 날 등)는 인정하지 않는다.
  종목마다 마지막 날짜가 다르면 종목 이름을 앞에 붙여 종목별로 쓴다.
- 뉴스·공시 결과만 쓴 답이 날짜를 하나도 말하지 않으면 참고한 기사·공시의 게시일 범위(KST)를 한 줄로 붙인다.
- 숫자를 새로 만들지 않는다: 붙이는 날짜는 도구 결과의 날짜 그대로다.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from app import quote_freshness

_ANY_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{4}\s*년\s*\d{1,2}\s*월|\d{4}\.\s*\d{1,2}\.\s*\d{1,2}|\d{1,2}\s*월\s*\d{1,2}\s*일")


def _date_forms(d: date) -> list[str]:
    return [d.isoformat(), f"{d.year}.{d.month:02d}.{d.day:02d}", f"{d.year}.{d.month}.{d.day}"]


_KOREAN_DATE_RE = re.compile(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def states_date(text: str, d: date) -> bool:
    """text가 날짜 d를 말하는지(ISO·점·한국어 표기). 연도 없는 'M월 D일'은 월·일이 같으면 인정한다."""
    t = text or ""
    if any(f in t for f in _date_forms(d)):
        return True
    for m in _KOREAN_DATE_RE.finditer(t):
        y, mo, dd = m.groups()
        if int(mo) == d.month and int(dd) == d.day and (y is None or int(y) == d.year):
            return True
    return False


def _price_bases(records: list[dict]) -> dict[str, date]:
    out: dict[str, date] = {}
    for r in records or []:
        if r.get("tool_name") != "stock_tool":
            continue
        res = r.get("result")
        base = quote_freshness.base_of(res) if isinstance(res, dict) else None
        if base is not None:
            name = res.get("company_name") or ""
            out[name] = max(out.get(name, base), base)
    return out


def _evidence_dates(records: list[dict]) -> list[date]:
    days = []
    for r in records or []:
        res = r.get("result")
        if not isinstance(res, dict) or r.get("tool_name") not in ("news_tool", "disclosure_tool", "rag_search_tool"):
            continue
        for key in ("news", "disclosures", "results"):
            for it in res.get(key) or []:
                v = isinstance(it, dict) and (it.get("published_at") or it.get("disclosed_at"))
                if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
                    days.append(date.fromisoformat(v[:10]))
    return days


def ensure_basis(answer: str, records: list[dict], now: datetime) -> str:
    """답에 데이터 기준 시점이 없으면 표준 문장을 덧붙인 답. 붙일 것이 없으면 answer 그대로."""
    if not answer:
        return answer
    lines = []
    bases = _price_bases(records)
    missing = {n: d for n, d in bases.items() if not states_date(answer, d)}
    if missing:
        if len(set(bases.values())) == 1:
            lines.append(quote_freshness.line(next(iter(missing.values())), now))
        else:
            lines += [f"{n}: {quote_freshness.line(d, now)}" if n else quote_freshness.line(d, now) for n, d in missing.items()]
    elif not bases:
        days = _evidence_dates(records)
        if days and not _ANY_DATE_RE.search(answer):
            lo, hi = min(days), max(days)
            span = lo.isoformat() if lo == hi else f"{lo.isoformat()}~{hi.isoformat()}"
            lines.append(f"참고한 뉴스·공시 게시일: {span}(한국 시간).")
    return answer if not lines else answer.rstrip() + "\n\n" + "\n".join(lines)
