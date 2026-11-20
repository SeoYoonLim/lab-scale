"""원/달러 환율 질문의 코드 답(72차, 2026-11-18). LLM을 부르지 않는다.

70·71차 실검증: 환율 답 본문을 LLM이 써서 11-02 하루 등락률 -0.93%를 "최근 5일간 -0.93% 하락"으로 붙였다(실제 다른 값). 이제 환율 수준·등락·흐름
질문(종목 없음)은 코드가 계산하고 문장도 코드가 만든다. 환율이 종목·업종에 미치는 영향 같은 설명 질문은 기존 LLM 경로이고, 그 답에는
answer_basis.fx_basis가 코드 계산 수치 줄을 붙이고 하루 등락률을 기간 등락률처럼 쓴 문장을 뺀다.

환율 '거래일' = 저장된 환율 기록일(exchange_rate 행). KRX 달력을 쓰지 않는다:
- 원천(FinanceDataReader USD/KRW)은 한국 휴장일·일요일 날짜에도 값을 준다(app.ingest_validation.check_fx, 2026-04~11 실측: 주말 날짜 행 30개,
  그 대신 금요일 날짜가 자주 없다). 원천 날짜를 바꾸거나 없는 날 값을 만들지 않고, 계산에 쓴 행의 날짜를 그대로 밝힌다.
- 하루 변동률: 마지막 기록과 바로 앞 기록의 종가 비교(저장값으로 다시 계산한다. 원천이 준 change_pct는 수집 때 앞 행이 달랐으면 다르다)
- N거래일 수익률: 마지막 기록과 N개 앞 기록(N개 구간)의 비교. 주식의 "최근 5거래일"(직전 거래일 종가 대비 5일)과 같은 정의
- 달력 기간(지난주·한 달·N개월 등): 기간 시작 전 마지막 기록 → 기간 안 마지막 기록. 기간 끝까지 기록이 없으면 끝나지 않은 기간이라고 밝힌다
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.briefing import KST

PAIR_CODE = "USD/KRW"
PAIR_NAME = "원/달러"
DEFAULT_RECORDS = 5
MAX_RECORDS = 120
SOURCE_NAME = "FinanceDataReader USD/KRW 일별 종가"

_ASK_RE = re.compile(r"얼마|올랐|오른|내렸|내린|떨어|빠졌|상승|하락|변했|변동|변화|흐름|추이|어때|어떤|어떻|등락|수익률|상승률|하락률|수준|현재|"
                     r"최근|알려|시세|움직|비교|왜|이유|원인|전망|예측|오를까|내릴까|될까|앞으로|향후")
# 종목·업종 영향 설명은 이 경로가 아니다(LLM 경로 + 코드 수치 줄)
_IMPACT_RE = re.compile(r"영향|수혜|피해|업종|종목|주식|수출|수입|기업|관련주|포트폴리오|금리|물가|유가")
_MARKET_RE = re.compile(r"코스피|코스닥|지수|증시|KOSPI|KOSDAQ", re.IGNORECASE)
_CAUSE_RE = re.compile(r"왜|이유|원인")
_FORECAST_RE = re.compile(r"전망|예측|오를까|내릴까|될까|앞으로|향후|다음\s*주|다음\s*달|내일")
_TODAY_RE = re.compile(r"오늘|금일")
_YESTERDAY_RE = re.compile(r"어제")


def is_fx_question(question: str, companies: list | None) -> bool:
    """종목 없이 원/달러 환율의 수준·등락·흐름(또는 그 원인·전망)을 묻는가."""
    from app.routing import needs_fx_tool

    q = question or ""
    if companies or not needs_fx_tool(q):
        return False
    if _IMPACT_RE.search(q) or _MARKET_RE.search(q):
        return False
    return bool(_ASK_RE.search(q))


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def _rate(v: float) -> str:
    return f"{v:,.2f}원"


def _wd(d: date) -> str:
    return "월화수목금토일"[d.weekday()]


def _day(d: date) -> str:
    return f"{d.isoformat()}({_wd(d)})"


def change(a: tuple, b: tuple) -> dict:
    """두 기록 (date, close) 사이 변동: 출발·끝 날짜와 값, 변동 폭(원), 변동률(%), 방향."""
    diff = round(b[1] - a[1], 2)
    pct = round((b[1] / a[1] - 1) * 100, 2)
    return {"from_date": a[0].isoformat(), "from_rate": a[1], "end_date": b[0].isoformat(), "end_rate": b[1],
            "diff": diff, "pct": pct, "direction": "상승" if diff > 0 else ("하락" if diff < 0 else "보합")}


def spec_of(question: str, now: datetime) -> dict:
    """질문의 기간. kind: default / today / yesterday / records(N거래일) / range(달력) / future / unsupported."""
    from app.answer_cause import _special_period
    from app.period_parser import parse_period

    q = question or ""
    if _YESTERDAY_RE.search(q):
        return {"kind": "yesterday", "text": "어제"}
    if _TODAY_RE.search(q):
        return {"kind": "today", "text": "오늘"}
    p = parse_period(q, now)
    if p is None:
        special = _special_period(q)
        if special and special["kind"] in ("invalid", "unsupported"):
            return {"kind": "unsupported", "text": special["text"]}
        return {"kind": "default", "text": None, "n": DEFAULT_RECORDS}
    text = p.matched_text.strip()
    if getattr(p, "trading_days", False) or "거래일" in text or "영업일" in text:
        return {"kind": "records", "text": text, "n": p.period_days}
    return {"kind": "range", "text": text, "start": p.start_date, "end": p.end_date}


def compute(rows: list[tuple], spec: dict, now: datetime) -> dict:
    """저장 기록 rows([(date, close, source_change_pct)] 오름차순)로 하루 변동과 기간 변동을 계산한다. 없는 값을 만들지 않는다."""
    today = now.astimezone(KST).date()
    out = {"status": "ok", "spec": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in spec.items()},
           "first_date": rows[0][0].isoformat(), "latest_date": rows[-1][0].isoformat(), "latest_rate": rows[-1][1],
           "records": len(rows), "day": None, "period": None, "notes": []}
    pts = [(r[0], r[1]) for r in rows]
    if len(pts) >= 2:
        out["day"] = change(pts[-2], pts[-1])
        src = rows[-1][2]
        if src is not None and abs(src - out["day"]["pct"]) >= 0.01:
            out["day"]["source_change_pct"] = src
    kind = spec["kind"]
    used: list[date] = []
    if kind in ("default", "records"):
        n = min(int(spec.get("n") or DEFAULT_RECORDS), MAX_RECORDS)
        out["period_n"] = n
        if len(pts) < n + 1:
            out["period_status"] = "short_history"
        else:
            out["period"] = change(pts[-n - 1], pts[-1])
            out["period_status"] = "ok"
            used = [d for d, _ in pts[-n - 1:]]
    elif kind == "range":
        start, end = spec["start"], min(spec["end"], today)
        out["req_start"], out["req_end"] = start.isoformat(), spec["end"].isoformat()
        inside = [p for p in pts if start <= p[0] <= end]
        before = [p for p in pts if p[0] < start]
        if start > today:
            out["period_status"] = "future"
        elif not inside:
            out["period_status"] = "no_data"
        elif not before:
            out["period_status"] = "short_history"
        else:
            out["period"] = change(before[-1], inside[-1])
            out["period_status"] = "ok"
            out["period_records"] = len(inside)
            used = [before[-1][0]] + [p[0] for p in inside]
            if inside[-1][0] < end:
                out["incomplete"] = True  # 요청 기간 끝(또는 오늘)까지 기록이 없다
    elif kind in ("today", "yesterday"):
        want = today if kind == "today" else today - timedelta(days=1)
        out["want_date"] = want.isoformat()
        hit = [i for i, p in enumerate(pts) if p[0] == want]
        if hit and hit[0] >= 1:
            i = hit[0]
            out["period"] = change(pts[i - 1], pts[i])
            out["period_status"] = "ok"
            used = [pts[i - 1][0], pts[i][0]]
        else:
            out["period_status"] = "not_stored"
    else:
        out["period_status"] = kind
    if out["day"]:
        used = used + [date.fromisoformat(out["day"]["from_date"]), date.fromisoformat(out["day"]["end_date"])]
    out["weekend_dates"] = sorted({d.isoformat() for d in used if d.weekday() >= 5})
    return out


def answer(question: str, res: dict, now: datetime) -> str:
    """코드 답. 하루 변동과 기간 변동은 늘 다른 줄·다른 이름으로 쓴다."""
    from app.answer_basis import fx_lag
    from app.disclaimer import DISCLAIMER

    latest = date.fromisoformat(res["latest_date"])
    lag = fx_lag(latest, now)
    head = f"{PAIR_NAME} 환율(USD/KRW) · 마지막 저장 기록 {_day(latest)} 종가 {_rate(res['latest_rate'])}"
    lines = ["## 환율 움직임(코드 계산)"]
    q = question or ""
    if _FORECAST_RE.search(q):
        lines.append("- 환율 전망은 제공하지 않습니다(환율 예측 모델이 없습니다). 아래는 저장된 과거 기록의 계산 결과입니다.")
    st, spec = res.get("period_status"), res["spec"]
    p = res.get("period")
    if st == "ok" and p:
        if spec["kind"] in ("default", "records"):
            label = f"최근 {res['period_n']}거래일(저장된 환율 기록 {res['period_n'] + 1}개, 구간 {res['period_n']}개)"
        elif spec["kind"] == "range":
            label = f"'{spec['text']}'({res['req_start']}~{res['req_end']}, 기간 직전 기록 → 기간 안 마지막 기록)"
        else:
            label = f"{spec['text']}({res['want_date']}) 하루(직전 기록 대비)"
        lines.append(f"- {label} 변동률: {_day(date.fromisoformat(p['from_date']))} {_rate(p['from_rate'])} → "
                     f"{_day(date.fromisoformat(p['end_date']))} {_rate(p['end_rate'])}, {p['diff']:+,.2f}원({_pct(p['pct'])}), {p['direction']}.")
        if res.get("incomplete"):
            lines.append(f"- 요청 기간 끝({res['req_end']})까지 환율 기록이 없어 {p['end_date']}까지만 계산했습니다. 기간 전체의 변동률이 아닙니다.")
    elif st == "short_history":
        if spec["kind"] in ("default", "records"):
            lines.append(f"- 최근 {res['period_n']}거래일 변동률: 저장된 환율 기록이 {res['records']}개라 계산할 수 없습니다.")
        else:
            lines.append(f"- '{spec['text']}'({res['req_start']}~{res['req_end']}) 변동률: 저장된 환율 기록이 {res['first_date']}부터라 "
                         "기간 시작 전 값이 없습니다. 일부 구간 값으로 대신하지 않습니다.")
    elif st == "no_data":
        lines.append(f"- '{spec['text']}'({res['req_start']}~{res['req_end']})에는 저장된 환율 기록이 없어 계산하지 않았습니다"
                     f"(마지막 기록 {res['latest_date']}). \"최근 5거래일\"처럼 물어보시면 저장된 마지막 기록 기준으로 계산합니다.")
    elif st == "not_stored":
        lines.append(f"- {spec['text']}({res['want_date']}) 환율은 저장돼 있지 않아 그날 변동을 계산하지 않았습니다"
                     f"(마지막 기록 {res['latest_date']}). 아래 하루 변동은 {res['latest_date']} 기록의 값입니다.")
    elif st == "future":
        lines.append(f"- '{spec['text']}'은 아직 오지 않은 기간이라 계산하지 않았습니다.")
    elif st == "unsupported":
        lines.append(f"- 질문의 기간 표현 '{spec['text']}'을 해석하지 못해 다른 기간으로 바꿔 계산하지 않았습니다. "
                     "\"최근 5거래일\", \"지난달\", \"한 달\"처럼 물어봐 주세요.")
    d = res.get("day")
    if d and not (spec["kind"] in ("today", "yesterday") and st == "ok" and p and p["end_date"] == d["end_date"]):
        lines.append(f"- 마지막 기록일 하루 변동률: {_day(date.fromisoformat(d['from_date']))} {_rate(d['from_rate'])} → "
                     f"{_day(date.fromisoformat(d['end_date']))} {_rate(d['end_rate'])}, {d['diff']:+,.2f}원({_pct(d['pct'])}). "
                     "하루 변동률이며 기간 변동률이 아닙니다.")
    if d and d.get("source_change_pct") is not None:
        lines.append(f"- 원천이 {d['end_date']} 행에 함께 준 등락률 {_pct(d['source_change_pct'])}는 수집 때 앞 행이 지금 저장값과 달라 쓰지 않았고, "
                     "저장된 두 기록으로 다시 계산했습니다.")
    if res.get("weekend_dates"):
        days = ", ".join(_day(date.fromisoformat(x)) for x in res["weekend_dates"])
        lines.append(f"- 계산에 쓴 기록 중 {days}는 원천이 주말 날짜로 준 값입니다. 원천 날짜를 그대로 썼고, 환율 기록일은 한국거래소 거래일과 "
                     "다를 수 있습니다.")
    if _CAUSE_RE.search(q):
        lines.append("- 환율이 움직인 원인을 설명하는 뉴스·지표는 수집하지 않아 원인을 말씀드릴 수 없습니다. 위 수치는 움직임의 크기와 방향일 뿐입니다.")
    if lag > 0:
        lines.append(f"- 마지막 환율 기록({res['latest_date']})은 {lag}영업일 전 값입니다. 최신·실시간 환율이 아니며 그 뒤의 움직임은 알 수 없습니다.")
    lines.append(f"- 자료: {SOURCE_NAME}(DB 저장값, {res['first_date']}~{res['latest_date']} 기록 {res['records']}개). "
                 "기간 변동률은 출발·끝 기록 두 값으로만 계산하고 빠진 날의 값은 만들지 않았습니다.")
    return f"{head}\n\n" + "\n".join(lines) + f"\n\n{DISCLAIMER}"


def load_rows(db) -> list[tuple]:
    from app.models import ExchangeRate

    rows = (db.query(ExchangeRate.price_date, ExchangeRate.close_price, ExchangeRate.change_pct)
            .filter(ExchangeRate.pair_code == PAIR_CODE).order_by(ExchangeRate.price_date.asc()).all())
    return [(d, float(c), float(p) if p is not None else None) for d, c, p in rows]


def no_data_answer() -> str:
    from app.disclaimer import DISCLAIMER

    return f"{PAIR_NAME} 환율 기록이 아직 저장돼 있지 않아 계산할 수 없습니다. 없는 값을 추정하지 않습니다.\n\n{DISCLAIMER}"


def facts_line(fx: dict) -> str | None:
    """LLM 경로 답에 붙이는 코드 계산 수치 한 줄(fx_tool 결과로). 하루·기간 변동을 이름을 붙여 따로 쓴다."""
    rates = fx.get("rates") or []
    if len(rates) < 2:
        return None
    a, b, z = rates[-2], rates[-1], rates[0]
    day = round((b["close_rate"] / a["close_rate"] - 1) * 100, 2)
    per = fx.get("period_change_pct")
    n = len(rates) - 1
    return (f"환율 수치(코드 계산, 저장 기록 기준): 마지막 기록일 하루 변동률 {a['price_date']} → {b['price_date']} {_pct(day)} / "
            f"기록 {len(rates)}개({n}개 구간) 변동률 {z['price_date']} → {b['price_date']} {_pct(per) if per is not None else '계산 불가'}.")
