"""원인 + 전망 복합 질문의 "최근 주가 움직임과 원인" 단락(68차, 2026-11-17). 문장은 코드 템플릿이고 LLM을 쓰지 않는다.

실사용 #43444("하이닉스가 왜 올랐어? 그리고 앞으로 7일간 오를 거 같아?")는 LLM 경로로 가서
(1) 전망을 빠뜨렸고 (2) 시세 기준일(11-12) 뒤에 나온 11-17 기사로 원인을 설명했고 (3) "올랐다"는 전제를 실제 등락과 맞춰 보지 않았다.
이 단락의 규칙:
- 움직임: 저장된 시세의 기준일 하루 등락과 최근 5거래일 등락(기준일 종가 기준). 질문의 전제(올랐다·내렸다)와 반대면 그렇다고 밝힌다.
- 원인 후보: 그 5거래일 창(비교 기준일 ~ 기준일, KST 날짜)에 발행된 이 종목 기사(제목에 종목 이름, 전망 경로와 같은 후처리)와 접수된 공시.
  기준일 뒤 기사는 그 시세에 반영되지 않았으므로 원인 후보에서 빼고 건수만 밝힌다.
- 후보가 없으면 원인을 특정할 수 없다고 쓴다. 후보가 있어도 직접 원인이라고 단정하지 않는다.

69차(2026-11-18): 기준 시점 = 기준일 정규장 마감(krx_calendar.close_time, 보통 15:30·특수일 16:30). 원인 단독 질문("왜 올랐어?")도 이 모듈을 쓴다.
- 기준일 당일 기사는 발행 시각을 마감과 비교한다. 마감 뒤 기사는 그날 종가에 반영되지 않았으므로 원인 후보가 아니다(건수만 밝힌다).
- 발행 시각을 알 수 없는 자료는 시간 순서를 추정하지 않는다: 공시는 접수일만 저장돼 있고(전부 KST 자정), 뉴스도 KST 정각 자정 행은
  날짜만 있는 행일 수 있다. 기준일 당일의 그런 자료는 원인 후보에서 빼고 건수만 밝힌다(기준일 전날까지의 자료는 날짜만으로 충분하다).
- LLM 경로(_answer의 도구 결과)에는 filter_records가 같은 규칙으로 기사·공시를 LLM에 넘기기 전에 거른다.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app import krx_calendar
from app.briefing import KST

CAUSE_DAYS = 5  # 움직임·원인 창(거래일). 전망 기간과 관계없이 최근 1주일
NEWS_LIMIT = 3
DISCLOSURE_LIMIT = 3
NEWS_FETCH = 300  # 창 안, 기준일 뒤 각각 읽는 기사 수 상한(대형주는 하루 수십 건)
_UP_RE = re.compile(r"올랐|오른\s*(?:이유|원인|거)|상승했|상승한\s*(?:이유|원인)|급등|뛰었|올라갔|강세")
_DOWN_RE = re.compile(r"내렸|내린\s*(?:이유|원인|거)|하락했|하락한\s*(?:이유|원인)|떨어졌|떨어진|급락|빠졌|빠진|약세")


def cutoff_at(base: date) -> datetime:
    """기준일 시세의 기준 시점: 그날 정규장 마감(KST). 이 시각 전에 발행된 자료만 그날 종가의 원인 후보가 될 수 있다."""
    return datetime.combine(base, krx_calendar.close_time(base), KST)


def _aware(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):  # date만 있으면 시각을 모른다
        return datetime.combine(value, datetime.min.time(), KST)
    return value if value.tzinfo else value.replace(tzinfo=KST)


def time_state(value, base: date, date_only: bool = False) -> str:
    """자료 발행(접수) 시각이 기준일 시세보다 앞인가: "before"(원인 후보 가능), "after"(반영 안 됨), "unknown"(기준일 당일인데 시각 모름).

    date_only=True(공시: 접수일만 있음)이거나 KST 정각 자정(날짜만 있는 행일 수 있음)이면 시각을 모르는 것으로 본다.
    값이 없으면 "unknown"이다."""
    at = _aware(value)
    if at is None:
        return "unknown"
    at = at.astimezone(KST)
    day = at.date()
    if day < base:
        return "before"
    if day > base:
        return "after"
    if date_only or at.time() == datetime.min.time():
        return "unknown"
    return "before" if at < cutoff_at(base) else "after"


def premise(question: str) -> str | None:
    """질문이 전제한 움직임: "up"(올랐어), "down"(떨어졌어), 둘 다 없거나 둘 다 있으면 None."""
    up, down = bool(_UP_RE.search(question or "")), bool(_DOWN_RE.search(question or ""))
    return "up" if up and not down else ("down" if down and not up else None)


def _won(v: float) -> str:
    return f"{v:,.0f}원"


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:+.2f}%"


def _kst_day(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(KST).date() if value.tzinfo else value.date()


def collect(db, company, base: date, start: date) -> dict:
    """창(start~base, KST 날짜) 안의 이 종목 뉴스·공시와, base 뒤 관련 기사 수."""
    from sqlalchemy import func

    from app.models import Company, Disclosure, News
    from app.news_relevance import evidence_news
    from app.outlook import disclosure_date, filter_titles, key_news, kst_date

    lo = datetime.combine(start, datetime.min.time(), KST)
    hi = datetime.combine(base + timedelta(days=1), datetime.min.time(), KST)
    cut = cutoff_at(base)  # 69차: 기준일 정규장 마감. 당일 기사는 이 시각 전 것만 후보다
    base_midnight = datetime.combine(base, datetime.min.time(), KST)  # 당일 KST 정각 자정 = 시각을 모르는 행일 수 있다
    others = ([n for (n,) in db.query(Company.name).filter(Company.asset_type == "etf", Company.id != company.id)]
              if getattr(company, "asset_type", None) == "etf" else [])

    def relevant(rows):
        items = [{"company_name": company.name, "title": r.title, "source": r.source, "url": r.url,
                  "published_at": r.published_at.isoformat() if r.published_at else None, "date": kst_date(r.published_at)}
                 for r in rows]
        return filter_titles(evidence_news(company.name, company.asset_type, items, other_etf_names=others))

    base_q = db.query(News).filter(News.company_id == company.id, News.is_relevant.isnot(False))
    # 창(start~base, KST) 안의 기사를 직접 읽는다. 최근 N건만 읽으면 시세가 늦을 때 창 안의 기사가 하나도 안 읽힌다(대형주는 하루 수십 건)
    in_window = relevant(base_q.filter(News.published_at >= lo, News.published_at < cut, News.published_at != base_midnight)
                         .order_by(News.published_at.desc()).limit(NEWS_FETCH).all())
    # 69차: 기준일 당일 장 마감 뒤 기사·시각을 모르는 기사는 후보에서 빼고 건수만 센다(after_count는 68차대로 기준일 다음 날부터)
    after_close = (db.query(func.count(News.id)).filter(News.company_id == company.id, News.is_relevant.isnot(False),
                                                         News.published_at >= cut, News.published_at < hi).scalar())
    unknown_news = (db.query(func.count(News.id)).filter(News.company_id == company.id, News.is_relevant.isnot(False),
                                                          News.published_at == base_midnight).scalar())
    # 기준일 뒤 기사는 원인 후보가 아니므로 건수와 첫 날짜만 센다(이 종목에 수집된 기사 기준, 제목 판정 없이 SQL로 정확히)
    after_count, after_min = (db.query(func.count(News.id), func.min(News.published_at))
                              .filter(News.company_id == company.id, News.is_relevant.isnot(False), News.published_at >= hi).one())
    key = key_news(in_window)
    rest = [n for n in in_window if n not in key][:max(0, NEWS_LIMIT - len(key))]
    news = sorted(key + rest, key=lambda n: n.get("published_at") or "", reverse=True)[:NEWS_LIMIT]
    disclosures = []
    unknown_disc = 0
    if getattr(company, "asset_type", None) != "etf":
        # 공시 접수일은 KST 날짜를 UTC 자정 근처로 저장한 행이 있어(disclosure_date) 하루씩 넓게 읽고 날짜로 거른다
        dlo = datetime.combine(start - timedelta(days=1), datetime.min.time(), KST)
        dhi = datetime.combine(base + timedelta(days=2), datetime.min.time(), KST)
        drows = (db.query(Disclosure).filter(Disclosure.company_id == company.id, Disclosure.disclosed_at >= dlo,
                                             Disclosure.disclosed_at < dhi)
                 .order_by(Disclosure.disclosed_at.desc()).all())
        for d in drows:
            day = disclosure_date(d.source_url, d.disclosed_at)
            if day == base.isoformat():
                # 69차: 공시는 접수일만 있어(시각 없음) 기준일 당일 접수분은 장 마감 전후를 알 수 없다 -> 후보에서 빼고 센다
                unknown_disc += 1
                continue
            if day and start.isoformat() <= day < base.isoformat():
                same = next((x for x in disclosures if x["title"] == d.title and x["date"] == day), None)
                if same is not None:  # 같은 날 같은 제목(정정 여러 건)은 한 줄로 묶고 건수만 센다
                    same["count"] = same.get("count", 1) + 1
                    continue
                disclosures.append({"company_name": company.name, "title": d.title, "disclosure_type": d.disclosure_type,
                                    "disclosed_at": d.disclosed_at.isoformat() if d.disclosed_at else None,
                                    "source_url": d.source_url, "date": day})
        disclosures = disclosures[:DISCLOSURE_LIMIT]
    return {"news": news, "disclosures": disclosures, "after_count": int(after_count or 0), "after_first": kst_date(after_min),
            "after_close": int(after_close or 0), "unknown_news": int(unknown_news or 0), "unknown_disclosures": unknown_disc,
            "cutoff": cut.strftime("%H:%M")}


def movement(ind: dict) -> dict:
    """기준일 하루 등락과 최근 CAUSE_DAYS거래일 등락(regime_inputs의 r5)."""
    ri = ind.get("regime_inputs") or {}
    frm = ri.get(f"r{CAUSE_DAYS}_from")
    return {"base": ind["base_date"], "close": ind["close"], "prev": ind.get("prev_date"), "day": ind.get("day_change_pct"),
            "r": ri.get(f"r{CAUSE_DAYS}"), "from_date": frm[0] if frm else None, "from_close": frm[1] if frm else None}


def premise_line(question: str, mv: dict) -> str | None:
    p, r, day = premise(question), mv["r"], mv["day"]
    if p is None or r is None:
        return None
    if p == "up" and r < 0:
        extra = f"(기준일 하루는 {_pct(day)} 올랐습니다)" if day is not None and day > 0 else ""
        return f"- 질문은 '올랐다'고 하셨지만, 저장된 시세로는 최근 {CAUSE_DAYS}거래일 {_pct(r)}로 내렸습니다{extra}."
    if p == "down" and r > 0:
        extra = f"(기준일 하루는 {_pct(day)} 내렸습니다)" if day is not None and day < 0 else ""
        return f"- 질문은 '내렸다'고 하셨지만, 저장된 시세로는 최근 {CAUSE_DAYS}거래일 {_pct(r)}로 올랐습니다{extra}."
    return None


def section(company, res: dict, question: str, found: dict, day_note: str | None = None, trimmed: bool = False) -> str:
    """"## 최근 주가 움직임과 원인" 단락. day_note는 질문의 '오늘·어제'와 기준일 관계(69차), trimmed=True면 시세를 그날까지로
    잘라 계산했으므로 최신성 문구를 붙이지 않는다."""
    ind, data = res["indicators"], res["data"]
    mv = movement(ind)
    base = mv["base"]
    span = f"{mv['from_date']}~{base}" if mv["from_date"] else base
    lines = [f"## 최근 주가 움직임과 원인({span} 종가 기준)"]
    move = f"- 실제 움직임: 기준일 {base} 종가 {_won(mv['close'])}, 전 거래일({mv['prev']}) 대비 {_pct(mv['day'])}"
    if mv["r"] is not None:
        move += f", 최근 {CAUSE_DAYS}거래일({mv['from_date']} 종가 {_won(mv['from_close'])} 대비) {_pct(mv['r'])}"
    lines.append(move + ".")
    if day_note:
        lines.append(f"- {day_note}")
    pl = premise_line(question, mv)
    if pl:
        lines.append(pl)
    rel = ind.get("relative")
    if rel and rel.get("period_days") == CAUSE_DAYS:
        word = {"in_line": "시장과 비슷했습니다", "outperform": "시장보다 좋았습니다", "underperform": "시장보다 부진했습니다"}
        lines.append(f"- 같은 기간 지수 {_pct(rel.get('market_change_pct'))}, 종목 {_pct(rel.get('stock_change_pct'))}로 "
                     f"{word.get(rel.get('relative'), '시장과 비교했습니다')}(차이 {rel.get('diff_pct_point', 0):+.2f}%p).")
    news, discs = found["news"], found["disclosures"]
    cutoff = found.get("cutoff") or cutoff_at(date.fromisoformat(base)).strftime("%H:%M")
    if news or discs:
        lines.append(f"- 같은 기간({span}, {base} 장 마감 {cutoff} 전까지) 이 종목 기사·공시입니다. "
                     "주가 변동의 직접 원인이라고 단정할 수는 없습니다.")
        for n in news:
            src = f"({n['source']}) " if n.get("source") else ""
            lines.append(f"  - 뉴스 {n.get('date') or ''} {src}\"{n['title']}\"")
        for d in discs:
            many = f"(같은 제목 {d['count']}건)" if d.get("count", 1) > 1 else ""
            lines.append(f"  - 공시 {d.get('date') or ''} 접수 \"{d['title']}\"{many}")
    else:
        what = "기사" if getattr(company, "asset_type", None) == "etf" else "기사·공시"
        lines.append(f"- 같은 기간({span}, {base} 장 마감 {cutoff} 전까지) 이 종목을 직접 다룬 {what}를 찾지 못해 움직임의 원인을 "
                     "특정할 수 없습니다.")
    if found["after_count"]:
        lines.append(f"- 기준일 뒤({found['after_first']}~)에 수집된 이 종목 기사 {found['after_count']:,}건은 {base} 시세에 반영되지 않아 "
                     "원인 후보에서 뺐습니다.")
    if found.get("after_close"):
        lines.append(f"- 기준일 {base} 장 마감({cutoff}) 뒤에 나온 이 종목 기사 {found['after_close']:,}건은 {base} 종가에 반영되지 않아 "
                     "원인 후보에서 뺐습니다.")
    unknown = []
    if found.get("unknown_news"):
        unknown.append(f"발행 시각이 없는 기사 {found['unknown_news']:,}건")
    if found.get("unknown_disclosures"):
        unknown.append(f"접수 시각이 없는 공시 {found['unknown_disclosures']:,}건")
    if unknown:
        lines.append(f"- 기준일 {base} 당일 {'·'.join(unknown)}은 장 마감 전후를 알 수 없어 원인 후보에서 뺐습니다.")
    fresh = data.get("freshness") or ""
    if fresh and fresh != "최신 시세" and not trimmed:
        lines.append(f"- 시세가 {fresh} 상태라 {base} 뒤의 움직임은 알 수 없습니다.")
    return "\n".join(lines)


# ---------- 69차(2026-11-18): 원인 단독 질문("삼성전자 오늘 왜 올랐어?") ----------
# 68차 원인+전망 경로만 기준일 필터를 썼고, 원인 단독 질문은 LLM 경로로 가서 모델이 부른 news_tool의 최근 기사(시세 기준일 뒤 포함)가
# 그대로 LLM 컨텍스트에 들어갔다. 이제 (1) 종목 하나·기간 없음(또는 오늘·어제·최근)인 원인 질문은 이 모듈의 코드 답(LLM 없음)으로,
# (2) 나머지 원인 질문(두 종목, 한 달·3개월 같은 기간 등)은 LLM 경로에서 filter_records가 도구 결과를 LLM에 넘기기 전에 거른다.
_CAUSE_ONLY_PERIODS = {"오늘", "어제"}
# "실적 발표 후 왜 떨어졌어?"처럼 사건 뒤 반응을 묻는 질문은 최근 5거래일 창이 아니라 사건 날짜가 기준이라 이 경로가 아니다
_EVENT_AFTER_RE = re.compile(r"(?:공시|뉴스|기사|보도|발표|실적|소식)\S*\s*(?:\S+\s*)?(?:후|뒤|이후|직후)(?![가-힣])")
_MARKET_SUBJECT_RE = re.compile(r"코스피|코스닥|증시|지수|시장|KOSPI|KOSDAQ", re.IGNORECASE)
_TODAY_RE = re.compile(r"오늘|금일")
_YESTERDAY_RE = re.compile(r"어제")


def is_cause_only_question(question: str) -> bool:
    """원인 단독 질문을 코드 답으로 받을 수 있는가: 등락 원인 질문 + 전망·주문·사건 반응 아님 + 기간이 없거나 오늘·어제."""
    from app.outlook import is_cause_forecast_question, is_order_request
    from app.period_parser import parse_period
    from app.routing import is_cause_question, needs_event_reaction_tool

    q = question or ""
    if not is_cause_question(q) or is_cause_forecast_question(q) or is_order_request(q) or needs_event_reaction_tool(q):
        return False
    if _EVENT_AFTER_RE.search(q):
        return False
    p = parse_period(q)
    return p is None or p.matched_text.strip() in _CAUSE_ONLY_PERIODS


def is_market_subject(question: str) -> bool:
    """종목 없이 시장(코스피·증시)을 묻는가. 그런 질문은 직전 종목으로 이어받지 않는다."""
    return bool(_MARKET_SUBJECT_RE.search(question or ""))


def requested_day(question: str, now: datetime) -> tuple[str, date] | None:
    """질문의 '오늘'·'어제'가 가리키는 거래일. '어제'는 오늘 전의 마지막 거래일(월요일의 어제 = 금요일)."""
    q = question or ""
    today = now.astimezone(KST).date()
    if _YESTERDAY_RE.search(q):
        try:
            return "어제", krx_calendar.last_trading_day(today - timedelta(days=1))
        except ValueError:
            return "어제", today - timedelta(days=1)
    if _TODAY_RE.search(q):
        return "오늘", today
    return None


def rows_until(rows: list[tuple], question: str, now: datetime) -> tuple[list[tuple], str | None, bool]:
    """질문의 '오늘·어제'에 맞춰 시세 행(price_date 오름차순)을 자른다. (행, 기준일 안내 한 줄, 잘랐는가).

    - '어제'이고 저장된 시세가 그 뒤까지 있으면 그날까지로 자른다(그날 종가가 기준, 그 뒤 기사는 원인 후보가 아니다).
    - 요청한 날의 시세가 아직 없으면 자르지 않고 마지막 저장 거래일 기준이라고 밝힌다(다른 날을 그날인 것처럼 답하지 않는다)."""
    req = requested_day(question, now)
    if req is None or not rows:
        return rows, None, False
    word, day = req
    last = rows[-1][0]
    if last == day:
        return rows, None, False
    if last > day:
        cut = [r for r in rows if r[0] <= day]
        if len(cut) >= 2 and cut[-1][0] == day:
            return cut, f"질문하신 '{word}'는 {day.isoformat()} 거래일로 보고 그날 종가까지의 움직임으로 답합니다.", True
        return rows, (f"질문하신 '{word}'({day.isoformat()}) 시세가 저장돼 있지 않아 마지막 저장 거래일 {last.isoformat()} "
                      "기준으로 답합니다."), False
    why = "장중이거나 미수집" if day >= now.astimezone(KST).date() else "미수집"
    return rows, (f"질문하신 '{word}'({day.isoformat()}) 시세는 아직 저장되지 않아({why}) 마지막 저장 거래일 "
                  f"{last.isoformat()} 기준으로 답합니다."), False


def cause_only_answer(company, res: dict, question: str, found: dict, day_note: str | None, trimmed: bool) -> str:
    """원인 단독 질문의 코드 답: 제목 한 줄 + 68차 원인 단락(기준 시점까지의 기사·공시만) + 면책."""
    from app.outlook import DISCLAIMER

    base = res["indicators"]["base_date"]
    head = f"{company.name}({company.ticker}) · 최근 주가 움직임과 원인 · 시세 기준일 {base}"
    body = section(company, res, question, found, day_note=day_note, trimmed=trimmed)
    return f"{head}\n\n{body}\n\n{DISCLAIMER}"


def _base_of(records: list[dict]) -> dict[str, date]:
    """도구 결과의 시세 기준일: 종목명 -> stock_tool 기간 끝 날짜. stock_tool이 없으면 market_tool 비교 끝 날짜."""
    bases: dict[str, date] = {}
    for tool in ("stock_tool", "market_tool"):
        for r in records:
            res = r.get("result")
            if r.get("tool_name") != tool or not isinstance(res, dict) or not res.get("found"):
                continue
            value = res.get("period_end_date") if tool == "stock_tool" else res.get("end_date")
            name = res.get("company_name") or (r.get("arguments") or {}).get("ticker")
            try:
                day = date.fromisoformat(str(value)[:10])
            except ValueError:
                continue
            if name and name not in bases:
                bases[name] = day
        if bases:
            return bases
    return bases


def _split(items: list[dict], base_for, when_key: str, date_only: bool, names_key: str) -> tuple[list[dict], int, int]:
    """(기준 시점 전 자료, 뒤 건수, 시각 모름 건수)."""
    kept, after, unknown = [], 0, 0
    for it in items:
        names = it.get(names_key) if names_key == "company_names" else [it.get(names_key)]
        state = time_state(it.get(when_key), base_for([n for n in (names or []) if n]), date_only=date_only)
        if state == "before":
            kept.append(it)
        elif state == "after":
            after += 1
        else:
            unknown += 1
    return kept, after, unknown


def _note(base: date, cut: str, after: int, unknown: int) -> str:
    parts = []
    if after:
        parts.append(f"{base.isoformat()} {cut}(정규장 마감) 뒤에 나온 자료 {after}건")
    if unknown:
        parts.append(f"{base.isoformat()} 당일인데 발행·접수 시각을 알 수 없는 자료 {unknown}건")
    return (f"이 질문은 시세 기준일 {base.isoformat()} 종가까지의 움직임 원인을 묻습니다. " + "·".join(parts) +
            "은 그 시세에 반영되지 않았거나 전후를 알 수 없어 뺐습니다. 남은 자료가 움직임을 직접 설명하지 않으면 원인을 특정할 수 없다고 "
            "답하고, 다른 이유를 지어내지 마세요.")


def filter_records(records: list[dict], question: str, refetch=None) -> dict | None:
    """원인 질문의 LLM 경로: 도구 결과(news_tool·disclosure_tool·rag_search_tool)에서 시세 기준 시점 뒤 자료와 기준일 당일 시각 모름
    자료를 LLM에 넘기기 전에 뺀다(records의 result를 고친다 = 출처 목록에도 남지 않는다). 원인 질문이 아니거나 기준일을 모르면 None.

    refetch(tool_name, args) -> dict가 있으면 뉴스·공시 목록에서 뺀 자리를 기준 시점 전 자료로 다시 채운다(최근 N건이 모두 기준일 뒤인 경우).
    돌려주는 값은 기록용 요약이다."""
    from app.routing import is_cause_question

    if not is_cause_question(question):
        return None
    bases = _base_of(records)
    if not bases:
        return None
    earliest = min(bases.values())

    def base_for(names):
        hits = [bases[n] for n in names if n in bases]
        return min(hits) if hits else earliest

    summary = {"bases": {k: v.isoformat() for k, v in bases.items()}, "excluded_after": 0, "excluded_unknown_time": 0,
               "refetched": []}
    for r in records:
        name, res = r.get("tool_name"), r.get("result")
        if not isinstance(res, dict) or name not in ("news_tool", "disclosure_tool", "rag_search_tool"):
            continue
        if name == "rag_search_tool":
            items = res.get("results") or []
            kn, a1, u1 = _split([x for x in items if x.get("type") == "news"], base_for, "published_at", False, "company_names")
            kd, a2, u2 = _split([x for x in items if x.get("type") != "news"], base_for, "disclosed_at", True, "company_names")
            kept_ids = {id(x) for x in kn + kd}
            kept, after, unknown = [x for x in items if id(x) in kept_ids], a1 + a2, u1 + u2
            key = "results"
            base = base_for([res["company_name"]] if res.get("company_name") else [])
        else:
            key = "news" if name == "news_tool" else "disclosures"
            when, date_only = ("published_at", False) if name == "news_tool" else ("disclosed_at", True)
            base = base_for([res.get("company_name")])
            kept, after, unknown = _split(res.get(key) or [], base_for, when, date_only, "company_name")
            if (after or unknown) and refetch is not None and res.get("company_name"):
                # 기준 시점 전 자료로 다시 읽는다. 공시는 접수 시각이 없어 기준일 0시 전(전날까지)만 읽는다
                before = cutoff_at(base) if name == "news_tool" else datetime.combine(base, datetime.min.time(), KST)
                try:
                    again = refetch(name, {"company_name": res["company_name"], "limit": res.get("limit") or 5,
                                           "before_kst": before.isoformat()})
                except Exception:  # noqa: BLE001 - 다시 읽기가 실패하면 거른 목록만 쓴다
                    again = None
                if isinstance(again, dict) and again.get("found"):
                    more, _, _ = _split(again.get(key) or [], base_for, when, date_only, "company_name")
                    seen = {x.get("url") or x.get("source_url") for x in kept}
                    kept += [x for x in more if (x.get("url") or x.get("source_url")) not in seen]
                    kept = kept[: res.get("limit") or 5]
                    summary["refetched"].append(name)
            if name == "news_tool" and res.get("indirect_news"):
                ind, ia, iu = _split(res["indirect_news"], lambda _n, b=base: b, "published_at", False, "company_name")
                res["indirect_news"] = ind
                after, unknown = after + ia, unknown + iu
        if not (after or unknown):
            continue
        res[key] = kept
        cut = cutoff_at(base).strftime("%H:%M")
        res["cause_cutoff"] = {"base_date": base.isoformat(), "cutoff": cut, "excluded_after": after,
                               "excluded_unknown_time": unknown, "note": _note(base, cut, after, unknown)}
        if not kept:
            res["found"] = False
            res["message"] = (f"시세 기준일 {base.isoformat()} 장 마감({cut}) 전까지 나온 관련 자료를 찾지 못했습니다. "
                              "원인을 특정할 수 없습니다.")
        summary["excluded_after"] += after
        summary["excluded_unknown_time"] += unknown
    return summary
