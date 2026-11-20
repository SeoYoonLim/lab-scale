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


# 71차: 환율(fx_tool) 답도 기준일을 밝힌다. 70차 실측 "현재 환율은 약 1,325.52원에 거래되고 있습니다"는 11-02 값(11영업일 전)이었다.
FX_CLOSE_HOUR = 15  # 서울 외환시장 종가(15:30) 전이면 그날 값은 아직 없다
_FX_NOW_RE = re.compile(r"현재\s*(?=(?:원\s*[·/]?\s*달러|원달러|달러\s*[·/]?\s*원)?\s*환율)")
_FX_TRADING_RE = re.compile(r"에\s*거래되고\s*있습니다")


def fx_lag(latest: date, now: datetime) -> int:
    """환율 마지막 값 뒤로 지난 평일 수(기대 기준일까지). 0이면 최신."""
    from datetime import timedelta

    today = now.date()
    expected = today if now.hour > FX_CLOSE_HOUR or (now.hour == FX_CLOSE_HOUR and now.minute >= 30) else today - timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    n, d = 0, latest + timedelta(days=1)
    while d <= expected:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


# 72차: LLM 경로(환율이 종목·업종에 미치는 영향 같은 설명 질문)에서 하루 등락률을 기간 등락률처럼 쓴 문장을 뺀다.
# 70차 실측 "최근 5일간 -0.93% 하락"(11-02 하루 값). 기간 표현과 하루 값이 한 문장에 있고 하루 표현이 없으면 잘못 붙인 것으로 본다.
_FX_PERIOD_WORD_RE = re.compile(r"\d+\s*(?:거래일|영업일|일|주|개월)\s*(?:간|동안|사이)|최근\s*\d+|일주일|한\s*달|한\s*주|주간|기간\s*(?:동안|중)?|추세")
_FX_DAY_WORD_RE = re.compile(r"하루|전일|전날|일일|당일|직전\s*(?:기록|거래일)|대비")
# 문장 경계 = 마침표·물음표·느낌표 뒤에 공백이 오는 자리("-0.93%"의 소수점에서 자르지 않는다). split 위치로만 쓴다
_SENT_RE = re.compile(r"(?<=[.!?])(?=\s)")


def _pct_forms(v: float) -> list[str]:
    a = abs(v)
    return [f"{a:.2f}%", f"{a:.1f}%"] if round(a, 1) == a else [f"{a:.2f}%"]


def drop_fx_mislabels(answer: str, fx: dict) -> tuple[str, list[str]]:
    """하루 등락률(daily_change_pct)을 기간 표현과 함께 쓴 문장을 뺀다. 기간 등락률과 값이 같으면 건드리지 않는다."""
    day, per = fx.get("daily_change_pct"), fx.get("period_change_pct")
    if day is None or (per is not None and round(abs(day), 2) == round(abs(per), 2)):
        return answer, []
    forms = _pct_forms(day)
    dropped, out = [], []
    for line in (answer or "").split("\n"):
        keep, cut = [], False
        for sent in _SENT_RE.split(line):
            if (any(f in sent for f in forms) and _FX_PERIOD_WORD_RE.search(sent) and not _FX_DAY_WORD_RE.search(sent)
                    and re.search(r"환율|원\s*/?\s*달러|달러", sent)):
                dropped.append(sent.strip())
                cut = True
                continue
            keep.append(sent)
        joined = "".join(keep)
        out.append(joined.strip() if cut else joined)  # 뺀 문장 뒤에 남는 앞 공백 정리
    return "\n".join(out), dropped


def fx_basis(answer: str, records: list[dict], now: datetime) -> str:
    """환율 도구 결과가 있으면 기준일(없으면)과 지연(있으면)을 밝힌다. 지연이면 '현재 환율'·'거래되고 있습니다' 같은 최신 표현을 바꾼다."""
    fx = next((r.get("result") for r in records or [] if r.get("tool_name") == "fx_tool" and isinstance(r.get("result"), dict)
               and r["result"].get("found") and r["result"].get("latest_date")), None)
    if not answer or fx is None:
        return answer
    latest = date.fromisoformat(str(fx["latest_date"])[:10])
    lag = fx_lag(latest, now)
    out, _dropped = drop_fx_mislabels(answer, fx)
    if lag > 0:
        out = _FX_NOW_RE.sub(f"{latest.isoformat()} 종가 기준 ", out)
        out = _FX_TRADING_RE.sub("입니다", out)
    note = []
    if not states_date(out, latest):
        note.append(f"환율 기준일: {latest.isoformat()} 종가(DB에 저장된 값이며 실시간 환율이 아닙니다).")
    if lag > 0:
        note.append(f"마지막 환율({latest.isoformat()})은 {lag}영업일 전 값이라 최신 환율이 아닙니다.")
    from app.answer_fx import facts_line

    facts = facts_line(fx)  # 72차: 하루·기간 변동률을 이름을 붙여 코드 값으로 따로 밝힌다(LLM 문장의 수치보다 이 줄이 기준)
    if facts and facts not in out:
        note.append(facts)
    return out if not note else out.rstrip() + "\n\n" + " ".join(note)


def ensure_basis(answer: str, records: list[dict], now: datetime) -> str:
    """답에 데이터 기준 시점이 없으면 표준 문장을 덧붙인 답. 붙일 것이 없으면 answer 그대로."""
    if not answer:
        return answer
    answer = fx_basis(answer, records, now)
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
