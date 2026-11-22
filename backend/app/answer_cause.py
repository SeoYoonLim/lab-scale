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

from app import evidence_scope, krx_calendar
from app.briefing import KST
from app.korean import josa

CAUSE_DAYS = 5  # 움직임·원인 창(거래일). 전망 기간과 관계없이 최근 1주일
NEWS_LIMIT = 3
DISCLOSURE_LIMIT = 3
NEWS_FETCH = 300  # 창 안, 기준일 뒤 각각 읽는 기사 수 상한(대형주는 하루 수십 건)
REFERENCE_LIMIT = 3  # 2026-11-23: 업종·시장 참고 기사(원인 후보가 아님) 표시 상한
_UP_RE = re.compile(r"올랐|오른\s*(?:이유|원인|거)|상승했|상승한\s*(?:이유|원인)|상승\s*(?:이유|원인)|급등|뛰었|올라갔|강세")
_DOWN_RE = re.compile(r"내렸|내린\s*(?:이유|원인|거)|하락했|하락한\s*(?:이유|원인)|하락\s*(?:이유|원인)|떨어졌|떨어진|급락|빠졌|빠진|빠져|약세")


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


# 71차: 주가·거래 동향을 전한 기사("거래대금 …, 상승 마감 전 흐름", "카카오 주가 3만원대 이탈", "코스피 3.59% 하락…")는 움직임을
# 묘사할 뿐 원인이 아니다. 원인 후보에서 빼고 건수만 밝힌다. 숫자 없는 "현 주가 저평가" 같은 IR 기사는 걸리지 않게 좁게 잡는다.
PRICE_REPORT_RE = re.compile(
    r"거래대금|거래량\s*[\d,]|시가총액\s*[\d,]|상승\s*마감|하락\s*마감|보합\s*마감|마감\s*전\s*흐름|매수세|매도세|[↑↓▲▼]"
    r"|\d+(?:\.\d+)?\s*%\s*(?:상승|하락|올라|내려|급등|급락|오른|내린|↑|↓)|\d[\d,]*\s*원\s*(?:형성|기록|마감|돌파|이탈|선)"
    r"|\d+만\s*\d*\s*원(?:대|선)?\s*(?:이탈|돌파|회복|붕괴|형성)?|주가\s*[\d,]+|신고가|신저가|코스피|코스닥|특징주")


def is_price_report(title: str | None) -> bool:
    return bool(PRICE_REPORT_RE.search(title or ""))


def collect(db, company, base: date, start: date, after: datetime | None = None, news_limit: int = NEWS_LIMIT,
            disclosure_limit: int = DISCLOSURE_LIMIT) -> dict:
    """창(start~base, KST 날짜) 안의 이 종목 뉴스·공시와, base 뒤 관련 기사 수.

    after(71차, 기간 원인 질문): 창 시작을 날짜 0시 대신 이 시각(기간 직전 거래일의 장 마감)으로 둔다. 그 거래일 종가가 등락의
    출발점이라 그 전에 나온 기사는 기간 움직임의 원인 후보가 아니다. 공시는 접수 시각이 없어 그 날짜 접수분도 뺀다(다음 날부터)."""
    from sqlalchemy import func

    from app.models import Company, Disclosure, News
    from app.news_relevance import evidence_news
    from app.outlook import disclosure_date, filter_titles, key_news, kst_date

    lo = after if after is not None else datetime.combine(start, datetime.min.time(), KST)
    hi = datetime.combine(base + timedelta(days=1), datetime.min.time(), KST)
    cut = cutoff_at(base)  # 69차: 기준일 정규장 마감. 당일 기사는 이 시각 전 것만 후보다
    base_midnight = datetime.combine(base, datetime.min.time(), KST)  # 당일 KST 정각 자정 = 시각을 모르는 행일 수 있다
    others = ([n for (n,) in db.query(Company.name).filter(Company.asset_type == "etf", Company.id != company.id)]
              if getattr(company, "asset_type", None) == "etf" else [])

    etf = getattr(company, "asset_type", None) == "etf"
    scope_ctx = None if etf else evidence_scope.company_context(company.name)

    def relevant(rows):
        items = [{"company_name": company.name, "title": r.title, "source": r.source, "url": r.url,
                  "published_at": r.published_at.isoformat() if r.published_at else None, "date": kst_date(r.published_at)}
                 for r in rows]
        if scope_ctx is not None:
            # 2026-11-23: 제목·본문 요약으로 기업 직접 / 업종·산업 / 시장 / 관련성 미확인을 나눈다(app/evidence_scope). ETF는 나누지 않는다
            for item, r in zip(items, rows):
                item["scope"] = evidence_scope.classify(company.name, r.title, getattr(r, "content", None), *scope_ctx)
        return filter_titles(evidence_news(company.name, company.asset_type, items, other_etf_names=others))

    base_q = db.query(News).filter(News.company_id == company.id, News.is_relevant.isnot(False))
    # 창(start~base, KST) 안의 기사를 직접 읽는다. 최근 N건만 읽으면 시세가 늦을 때 창 안의 기사가 하나도 안 읽힌다(대형주는 하루 수십 건)
    raw = (base_q.filter(News.published_at >= lo, News.published_at < cut, News.published_at != base_midnight)
           .order_by(News.published_at.desc()).limit(NEWS_FETCH).all())
    titled = relevant(raw)
    in_window = [n for n in titled if not is_price_report(n.get("title"))]
    price_reports = len(titled) - len(in_window)
    # 69차: 기준일 당일 장 마감 뒤 기사·시각을 모르는 기사는 후보에서 빼고 건수만 센다(after_count는 68차대로 기준일 다음 날부터)
    after_close = (db.query(func.count(News.id)).filter(News.company_id == company.id, News.is_relevant.isnot(False),
                                                         News.published_at >= cut, News.published_at < hi).scalar())
    unknown_news = (db.query(func.count(News.id)).filter(News.company_id == company.id, News.is_relevant.isnot(False),
                                                          News.published_at == base_midnight).scalar())
    # 기준일 뒤 기사는 원인 후보가 아니므로 건수와 첫 날짜만 센다(이 종목에 수집된 기사 기준, 제목 판정 없이 SQL로 정확히)
    after_count, after_min = (db.query(func.count(News.id), func.min(News.published_at))
                              .filter(News.company_id == company.id, News.is_relevant.isnot(False), News.published_at >= hi).one())
    # 2026-11-23: 원인 후보(목록 앞)는 이 기업을 직접 다룬 기사만이다. 업종·시장 기사는 참고로 몇 건만 따로 붙이고(scope로 구분),
    # 이 기업을 직접 다루는지 확인되지 않은 기사는 건수만 센다
    direct = [n for n in in_window if not evidence_scope.is_reference(n.get("scope"))]
    context = [n for n in in_window if n.get("scope") in (evidence_scope.SECTOR, evidence_scope.MARKET)]
    unconfirmed = sum(1 for n in in_window if n.get("scope") == evidence_scope.UNCONFIRMED)
    key = key_news(direct)
    rest = [n for n in direct if n not in key][:max(0, news_limit - len(key))]
    news = sorted(key + rest, key=lambda n: n.get("published_at") or "", reverse=True)[:news_limit]
    references = sorted(context, key=lambda n: n.get("published_at") or "", reverse=True)[:REFERENCE_LIMIT]
    disc_from = (after.astimezone(KST).date() + timedelta(days=1)) if after is not None else start
    disclosures = []
    unknown_disc = 0
    if getattr(company, "asset_type", None) != "etf":
        # 공시 접수일은 KST 날짜를 UTC 자정 근처로 저장한 행이 있어(disclosure_date) 하루씩 넓게 읽고 날짜로 거른다
        dlo = datetime.combine(disc_from - timedelta(days=1), datetime.min.time(), KST)
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
            if day and disc_from.isoformat() <= day < base.isoformat():
                same = next((x for x in disclosures if x["title"] == d.title and x["date"] == day), None)
                if same is not None:  # 같은 날 같은 제목(정정 여러 건)은 한 줄로 묶고 건수만 센다
                    same["count"] = same.get("count", 1) + 1
                    continue
                disclosures.append({"company_name": company.name, "title": d.title, "disclosure_type": d.disclosure_type,
                                    "disclosed_at": d.disclosed_at.isoformat() if d.disclosed_at else None,
                                    "source_url": d.source_url, "date": day})
        disclosures = disclosures[:disclosure_limit]
    return {"news": news + references, "disclosures": disclosures, "after_count": int(after_count or 0),
            "after_first": kst_date(after_min),
            "after_close": int(after_close or 0), "unknown_news": int(unknown_news or 0), "unknown_disclosures": unknown_disc,
            "cutoff": cut.strftime("%H:%M"), "news_candidates": len(in_window), "news_fetch_capped": len(raw) >= NEWS_FETCH,
            "price_reports": price_reports, "direct_candidates": len(direct), "context_candidates": len(context),
            "unconfirmed_news": unconfirmed}


def _move_word(pct: float | None) -> str:
    if pct is None or pct == 0:
        return "주가 움직임의"
    return "하락" if pct < 0 else "상승"


def evidence_lines(name: str, what: str, rng: str, found: dict, pct: float | None, extra: str = "") -> list[str]:
    """원인 단락의 근거 줄(2026-11-23). 이 기업을 직접 다룬 기사·공시와 업종·시장 참고 기사를 나눠 쓴다.

    직접 근거가 없으면 업종·시장 기사가 있어도 이 기업의 개별 원인을 특정하지 않는다고 밝힌다. 기사 제목·날짜·언론사는 조회된 값 그대로다."""
    news = found.get("news") or []
    discs = found.get("disclosures") or []
    direct = [n for n in news if not evidence_scope.is_reference(n.get("scope"))]
    refs = [n for n in news if evidence_scope.is_reference(n.get("scope"))]
    lines = []
    if direct or discs:
        lines.append(f"- 같은 기간({rng}) {josa(name, '을/를')} 직접 다룬 {what}입니다{extra}. 주가 변동의 직접 원인이라고 단정할 수는 없습니다.")
        for n in direct:
            src = f"({n['source']}) " if n.get("source") else ""
            lines.append(f"  - 뉴스 {n.get('date') or ''} {src}\"{n['title']}\"")
        for d in discs:
            many = f"(같은 제목 {d['count']}건)" if d.get("count", 1) > 1 else ""
            lines.append(f"  - 공시 {d.get('date') or ''} 접수 \"{d['title']}\"{many}")
    elif refs:
        lines.append(f"- 같은 기간({rng}) {josa(name, '을/를')} 직접 다룬 {what}를 찾지 못했습니다. 현재 확인된 자료만으로는 "
                     f"{name}의 개별적인 {_move_word(pct)} 원인을 특정하기 어렵습니다.")
    else:
        lines.append(f"- 같은 기간({rng}) {josa(name, '을/를')} 직접 다룬 {what}를 찾지 못해 움직임의 원인을 특정할 수 없습니다.")
    if refs:
        lines.append(f"- 업종·시장 참고: 여러 기업이나 업종·시장 전반을 다룬 기사입니다. {name}의 개별 원인으로 보지 않습니다.")
        for n in refs:
            src = f"({n['source']}) " if n.get("source") else ""
            label = evidence_scope.LABEL_KO.get(n.get("scope"), "참고")
            lines.append(f"  - [{label}] 뉴스 {n.get('date') or ''} {src}\"{n['title']}\"")
    skipped = max(0, int(found.get("context_candidates") or 0) - len(refs))
    unconfirmed = int(found.get("unconfirmed_news") or 0)
    if skipped or unconfirmed:
        parts = ([f"업종·시장 기사 {skipped:,}건"] if skipped else []) + (
            [f"제목·본문에서 {josa(name, '을/를')} 직접 다루는지 확인되지 않은 기사 {unconfirmed:,}건"] if unconfirmed else [])
        lines.append(f"- 그 밖에 {'과 '.join(parts)}은 원인 근거로 쓰지 않았습니다.")
    return lines


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
    cutoff = found.get("cutoff") or cutoff_at(date.fromisoformat(base)).strftime("%H:%M")
    what = "기사" if getattr(company, "asset_type", None) == "etf" else "기사·공시"
    lines += evidence_lines(company.name, what, f"{span}, {base} 장 마감 {cutoff} 전까지", found,
                            mv["r"] if mv["r"] is not None else mv["day"])
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
    from app.period_parser import parse_period

    q = question or ""
    if not _cause_gate(q):
        return False
    p = parse_period(q)
    if p is None:
        return _special_period(q) is None  # 미래·0·해석 못 한 기간이면 최근 5거래일로 바꿔 답하지 않는다(71차 기간 경로가 받는다)
    return p.matched_text.strip() in _CAUSE_ONLY_PERIODS


# 71차: 직전 종목 이어받기는 주어 없는 질문("왜 떨어졌어?", "오늘 주가가 하락한 원인이 뭐야?", "최근 한 달 동안 왜 상승했어?")에만 한다.
# "삼송전자 지난주 왜 올랐어?"(오타)·"현대 지난주 왜 떨어졌어?"(그룹명)를 직전 종목으로 바꿔 답하면 엉뚱한 종목의 수치가 나간다.
_SUBJECTLESS_START_RE = re.compile(
    r"^\s*(?:그럼|그러면|그리고|근데|그런데|그래서|혹시|이\s*종목|이\s*회사|그\s*종목|이거|얘|여기|주가|최근|지난|이번|오늘|어제|요즘|"
    r"왜|무슨|어째서|한\s*달|두\s*달|석\s*달|\d)")


def is_subjectless(question: str) -> bool:
    """질문이 종목 이름 없이 시작하는가(직전 종목을 이어받아도 되는 모양)."""
    return bool(_SUBJECTLESS_START_RE.match(question or ""))


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


# ---------- 71차(2026-11-18): 기간을 붙인 원인 질문("셀트리온 지난주 급등 이유", "카카오 한 달 동안 왜 떨어졌어?") ----------
# 70차 실검증(docs/FINAL_INTEGRATION_AUDIT_70.md 5.2의 2b·10): 이 질문들은 LLM 경로라 (1) '급등' 전제를 실제 -0.71%와 대조하지 않았고
# (2) "시장 흐름과도 관련" 같은 근거 없는 인과를 썼고 (3) 코스피 -16.31%를 카카오 주어에 붙였다. 이제 코드가 기간 창을 정하고
# 종목·지수 수익률을 따로 계산해 답한다(LLM 없음). 기준 시점 규칙(cutoff_at·time_state·당일 공시 제외)은 69차 그대로다.
MAX_PERIOD_CAL_DAYS = 93     # 달력 기간 상한(3개월). 넘으면 긴 기간 규칙(72차)
SHARP_MOVE_PCT = 5.0  # '급등'·'급락' 전제를 크기로 확인하는 기준(기간 등락률 절댓값). 이보다 작으면 그렇게 부르기 어렵다고 밝힌다
MAX_PERIOD_TRADING_DAYS = 63  # "N거래일" 상한(3개월 안팎)
PERIOD_NEWS_LIMIT = 5
PERIOD_DISCLOSURE_LIMIT = 5


def _cause_gate(q: str) -> bool:
    """원인 질문 공통 조건(69차와 같다): 등락 원인 질문이고 원인+전망·주문·사건 뒤 반응이 아니다."""
    from app.outlook import is_cause_forecast_question, is_order_request
    from app.routing import is_cause_question, needs_event_reaction_tool

    if not is_cause_question(q) or is_cause_forecast_question(q) or is_order_request(q) or needs_event_reaction_tool(q):
        return False
    return not _EVENT_AFTER_RE.search(q)


# 미래 기간("다음 주 왜 올랐어?"), 0 기간("0거래일"), 해석하지 못한 연·월("2025년에", "13월에")을 다른 기간으로 바꿔 답하지 않는다.
# period_parser가 None을 주면 69차 원인 단독(최근 5거래일)으로 가서 질문과 다른 기간을 답하던 것을 막는다.
_FUTURE_PERIOD_RE = re.compile(r"다음\s*주|다음\s*달|내주|내달|내일|모레|다음\s*거래일")
_ZERO_PERIOD_RE = re.compile(r"(?<![\d.])0+\s*(?:거래일|영업일|일|주|주일|개월|달|년)")
_UNPARSED_DATE_RE = re.compile(r"(?<!\d)\d{4}\s*년|(?<!\d)\d{1,2}\s*월(?!\s*요일)|(?<!\d)\d{1,2}\s*일\s*(?:부터|까지|에)")


def _special_period(q: str) -> dict | None:
    m = _ZERO_PERIOD_RE.search(q)
    if m:
        return {"kind": "invalid", "text": m.group(0).strip()}
    m = _FUTURE_PERIOD_RE.search(q)
    if m:
        return {"kind": "future", "text": m.group(0).strip()}
    m = _UNPARSED_DATE_RE.search(q)
    if m:
        return {"kind": "unsupported", "text": m.group(0).strip()}
    return None


def period_spec(question: str, now: datetime | None = None) -> dict | None:
    """질문의 기간 표현(오늘·어제 제외). {"text", "start", "end", "trading", "n", "too_long"} 또는 None.

    - "N거래일"이면 trading=True, n=N: 시세 기준일부터 KRX 거래일로 N일을 거슬러 간다(68·69차 '최근 5거래일'과 같은 정의).
    - 그 밖(지난주·이번 주·한 달·지난달·N주·N일·N개월)은 질문이 가리키는 달력 범위 start~end(period_parser)다.
    - 3개월(달력 93일, 63거래일)을 넘으면 too_long=True. 72차부터는 이것도 코드 답이다: 등락·지수는 계산하고(기간 전체 시세가 있을 때만),
      기사·공시는 원인 후보로 고르지 않고 건수만 밝힌다(long_evidence_lines)."""
    from app.period_parser import parse_period

    q = question or ""
    p = parse_period(q, now)
    if p is None:
        # 원인 질문은 과거 움직임을 묻는다. 해석하지 못한 기간 중 미래·0·연월 표현이면 그 사실을 밝히는 코드 답으로 받는다
        special = _special_period(q)
        return {**special, "start": None, "end": None, "trading": False, "n": None, "too_long": False} if special else None
    text = p.matched_text.strip()
    if text in _CAUSE_ONLY_PERIODS:
        return None
    trading = getattr(p, "trading_days", False) or "거래일" in text
    too_long = (p.period_days > MAX_PERIOD_TRADING_DAYS) if trading else ((p.end_date - p.start_date).days > MAX_PERIOD_CAL_DAYS)
    return {"kind": "trading" if trading else "range", "text": text, "start": p.start_date, "end": p.end_date,
            "trading": trading, "n": p.period_days if trading else None, "too_long": too_long}


def is_period_cause_question(question: str, now: datetime | None = None) -> bool:
    """기간을 붙인 원인 질문을 코드 답으로 받을 수 있는가(종목 수·시장 질문 여부는 호출측이 본다)."""
    q = question or ""
    if not _cause_gate(q):
        return False
    # 72차: 3개월 초과도 코드 답이다(전에는 LLM 경로라 전제 확인 없이 답했다, 71차 보고서 7절 "삼성전자 100거래일 왜 올랐어?")
    return period_spec(q, now) is not None


def period_window(spec: dict, rows: list[tuple], today: date) -> dict:
    """저장 시세 rows([(date, close, volume)] 오름차순)로 기간 창을 정한다. 없는 날의 값을 만들지 않는다.

    반환: status("ok"|"no_data"|"no_start"), 요청 범위(req_start·req_end), 창 거래일(KRX 달력), 출발 종가(from_*·기간 직전 거래일),
    끝 종가(end_*·기간 안 마지막 저장 거래일), missing_mid(창 안인데 저장 안 된 거래일), missing_tail(끝 뒤 요청 범위 안 미저장 거래일)."""
    stored = {r[0]: float(r[1]) for r in rows}
    days = sorted(stored)
    last = days[-1]
    if spec["trading"]:
        cal = [d for d in krx_calendar.trading_days(last - timedelta(days=spec["n"] * 2 + 30), last) if d <= last]
        window = cal[-spec["n"]:]
        req_start, req_end = (window[0] if window else last), last
    else:
        req_start, req_end = spec["start"], spec["end"]
        window = krx_calendar.trading_days(req_start, min(req_end, today))
    out = {"req_start": req_start.isoformat(), "req_end": req_end.isoformat(), "last_stored": last.isoformat(),
           "window_days": [d.isoformat() for d in window], "missing_mid": [], "missing_tail": []}
    if not spec["trading"] and req_start > today:
        out["status"] = "future"
        return out
    if not window:
        out["status"] = "no_data"
        return out
    in_stored = [d for d in window if d in stored]
    if not in_stored:
        out["status"] = "no_data"  # 요청 기간의 시세가 하나도 저장되지 않았다(예: 이번 주인데 시세는 지난주까지)
        out["missing_tail"] = [d.isoformat() for d in window]
        return out
    end_day = in_stored[-1]
    prev = krx_calendar.previous_trading_day(window[0])
    before = [d for d in days if d < window[0]]
    start_partial = False
    if spec.get("too_long") and not before:
        # 72차: 3개월 초과 기간은 시작 전 종가가 없으면(저장 시세가 기간 중간부터) 계산하지 않는다. 짧은 기간(71차)처럼 일부 구간을 계산해
        # 보여주면 긴 기간 전체의 등락처럼 읽힌다
        out.update(status="short_history", first_stored=days[0].isoformat(), expected_from=prev.isoformat())
        return out
    if before:
        from_day = before[-1]
    elif len(in_stored) >= 2:
        # 기간 직전 종가가 없다(상장·시세 수집이 기간 중간에 시작). 저장 시세가 시작하는 날 종가부터 계산하고 그 사실을 밝힌다
        from_day, start_partial = in_stored[0], True
    else:
        out["status"] = "no_start"
        return out
    out.update(status="ok", from_date=from_day.isoformat(), from_close=stored[from_day], end_date=end_day.isoformat(),
               end_close=stored[end_day], pct=round((stored[end_day] / stored[from_day] - 1) * 100, 2),
               trading_days=len([d for d in window if from_day < d <= end_day]), from_is_prev_trading_day=(from_day == prev),
               start_partial=start_partial,
               expected_from=prev.isoformat(),
               missing_mid=[d.isoformat() for d in window if from_day < d <= end_day and d not in stored],
               missing_tail=[d.isoformat() for d in window if d > end_day and d not in stored])
    return out


def index_change(db, company, win: dict) -> dict:
    """종목 상장 시장 지수(코스피 KS11·코스닥 KQ11)의 같은 두 날짜(출발·끝) 종가 수익률. 같은 날짜가 없으면 비교하지 않는다."""
    from app.models import MarketIndex
    from app.models.market_index import MARKET_INDEXES
    from app.tools.market_tool import index_code_for_market

    code = index_code_for_market(getattr(company, "market", None))
    if not code:
        return {"status": "no_index"}
    days = [date.fromisoformat(win["from_date"]), date.fromisoformat(win["end_date"])]
    got = dict(db.query(MarketIndex.price_date, MarketIndex.close_price)
               .filter(MarketIndex.index_code == code, MarketIndex.price_date.in_(days)).all())
    name = MARKET_INDEXES.get(code, code)
    missing = [d.isoformat() for d in days if d not in got]
    if missing:
        return {"status": "missing", "code": code, "name": name, "missing": missing}
    a, b = float(got[days[0]]), float(got[days[1]])
    return {"status": "ok", "code": code, "name": name, "from_date": win["from_date"], "end_date": win["end_date"],
            "from_close": a, "end_close": b, "pct": round((b / a - 1) * 100, 2)}


def _quote_topic(word: str) -> str:
    """'0거래일'은 / '다음 주'는 (따옴표 뒤 주제 조사)."""
    return f"'{word}'" + ("은" if josa(word, "은/는").endswith("은") else "는")


def _quote_obj(word: str) -> str:
    """'한 달'을 / '지난주'를 (따옴표 뒤 목적격 조사)."""
    return f"'{word}'" + ("을" if josa(word, "을/를").endswith("을") else "를")


def _rago(word: str) -> str:
    """'급등'이라고 / '올랐다'라고 (따옴표 뒤 인용 조사)."""
    return f"'{word}'" + ("이라고" if josa(word, "이/가").endswith("이") else "라고")


def _days(a: str, b: str) -> str:
    return a if a == b else f"{a}~{b}"


def _num(v: float) -> str:
    return f"{v:,.2f}"


def _period_premise(question: str, pct: float, name: str) -> str | None:
    p = premise(question)
    if p is None:
        return None
    word = "급등" if "급등" in question else ("급락" if "급락" in question else ("올랐다" if p == "up" else "내렸다"))
    if p == "up" and pct < 0:
        return f"- 질문의 전제와 다릅니다: 질문은 {_rago(word)} 하셨지만, {name} 주가는 이 기간 {_pct(pct)}로 내렸습니다."
    if p == "down" and pct > 0:
        return f"- 질문의 전제와 다릅니다: 질문은 {_rago(word)} 하셨지만, {name} 주가는 이 기간 {_pct(pct)}로 올랐습니다."
    if pct == 0:
        return f"- 질문은 {_rago(word)} 하셨지만, {name} 주가는 이 기간 변동이 없었습니다(0.00%)."
    if word in ("급등", "급락") and abs(pct) < SHARP_MOVE_PCT:
        # 최종 점검(2026-11-18): "셀트리온 지난주 급등"(+1.42%, 코스피 +4.56%)을 "같은 방향입니다"라고만 해 급등을 인정하는 것처럼 읽혔다
        return (f"- 질문의 전제('{word}')와 방향은 같지만, {name} 주가는 이 기간 {_pct(pct)}로 '{word}'이라고 하기는 어렵습니다"
                f"(기준: {SHARP_MOVE_PCT:.0f}% 이상).")
    return f"- 질문의 전제('{word}')와 같은 방향입니다({name} {_pct(pct)})."


def period_cause_answer(company, spec: dict, win: dict, idx: dict | None, found: dict | None, data: dict | None,
                        question: str) -> str:
    """기간 원인 질문의 코드 답. 종목 움직임과 지수 움직임을 주어를 붙여 따로 쓰고, 근거는 기간 안·기준 시점 전 자료만 쓴다."""
    from app.disclaimer import DISCLAIMER

    name = company.name
    what = "기사" if getattr(company, "asset_type", None) == "etf" else "기사·공시"
    kind = "ETF" if getattr(company, "asset_type", None) == "etf" else "종목"
    label = f"최근 {spec['n']}거래일" if spec["trading"] else f"'{spec['text']}'"
    if win["status"] in ("future", "invalid", "unsupported"):
        head = f"{name}({company.ticker}) · 주가 움직임과 원인 · 시세 기준일 {win['last_stored']}"
        why = {"future": f"질문하신 {_quote_topic(spec['text'])} 아직 오지 않은 기간입니다. 원인 분석은 이미 일어난 움직임에 대해서만 할 수 있고, "
                         "앞으로의 움직임을 원인처럼 설명하지 않습니다. 앞으로의 방향은 \"" + name + " 다음 주 전망 알려줘\"처럼 물어봐 주세요.",
               "invalid": f"질문의 기간 {_quote_topic(spec['text'])} 계산할 수 있는 기간이 아닙니다. \"최근 5거래일\", \"지난주\", \"한 달\"처럼 "
                          "다시 물어봐 주세요.",
               "unsupported": f"질문의 기간 표현 {_quote_topic(spec['text'])} 원인 분석에서 해석하지 못했습니다. 다른 기간으로 바꿔 답하지 않습니다. "
                              "\"지난주\", \"지난달\", \"최근 20거래일\", \"3개월\" 같은 표현으로 다시 물어봐 주세요."}[win["status"]]
        return f"{head}\n\n## 기간 주가 움직임과 원인\n- {why}\n\n{DISCLAIMER}"
    req = _days(win["req_start"], win["req_end"])
    if win["status"] != "ok":
        head = f"{name}({company.ticker}) · {label} 주가 움직임과 원인 · 시세 기준일 {win['last_stored']}"
        if win["status"] == "short_history":
            why = (f"질문하신 {label}({req})을 계산하려면 {win['expected_from']} 종가가 필요하지만, {name}의 저장 시세는 "
                   f"{win['first_stored']}부터 {win['last_stored']}까지입니다. 기간 전체의 등락을 계산할 수 없어 일부 구간 수치로 대신하지 않고, "
                   f"원인도 말씀드리지 않습니다. {win['first_stored']} 이후 기간(예: \"최근 1년\", \"최근 3개월\")으로 다시 물어봐 주세요.")
            return f"{head}\n\n## 기간 주가 움직임과 원인\n- {why}\n\n{DISCLAIMER}"
        if win["status"] == "no_data":
            why = (f"질문하신 {label}({req})의 시세가 아직 저장되지 않았습니다(마지막 저장 거래일 {win['last_stored']}). "
                   "이 기간의 주가 움직임을 계산할 수 없어 원인도 말씀드릴 수 없습니다. 없는 시세를 추정해 채우지 않습니다.")
        else:
            why = f"질문하신 {label}({req}) 직전 거래일의 저장 시세가 없어 기간 등락을 계산할 수 없습니다."
        return f"{head}\n\n## 기간 주가 움직임과 원인\n- {why}\n\n{DISCLAIMER}"
    span = f"{win['from_date']} 종가 → {win['end_date']} 종가"
    head = (f"{name}({company.ticker}) · {label} 주가 움직임과 원인 · 분석 {span}({win['trading_days']}거래일) · "
            f"시세 기준일 {win['last_stored']}")
    lines = [f"## 기간 주가 움직임과 원인({win['from_date']}~{win['end_date']})"]
    if spec["trading"]:
        lines.append(f"- 분석 기간: 시세 기준일 {win['end_date']}까지 최근 {spec['n']}거래일(KRX 거래일 기준, "
                     f"{win['window_days'][0]}~{win['end_date']})로 보고, 그 직전 거래일 {win['from_date']} 종가와 비교했습니다.")
    elif win.get("start_partial"):
        lines.append(f"- 분석 기간: 질문의 {_quote_obj(spec['text'])} {req}(달력 기준)로 보고, 저장 시세가 있는 {win['from_date']} 종가부터 "
                     f"{win['end_date']} 종가까지({win['trading_days']}거래일)를 계산했습니다.")
    else:
        lines.append(f"- 분석 기간: 질문의 {_quote_obj(spec['text'])} {req}(달력 기준)로 보고, 그 안의 KRX 거래일 "
                     f"{win['trading_days']}일을 기간 직전 거래일 {win['from_date']} 종가와 비교했습니다.")
    if win.get("start_partial"):
        lines.append(f"- {name}의 저장 시세가 {win['from_date']}부터라(상장 또는 수집 시작) 요청 기간 시작 전 종가가 없습니다. "
                     f"{win['from_date']} 종가부터 계산했고, 요청 기간 전체의 등락이 아닙니다.")
    elif not win.get("from_is_prev_trading_day"):
        lines.append(f"- 기간 직전 거래일 {win['expected_from']}의 시세가 저장돼 있지 않아 그 전 저장 거래일 {win['from_date']} 종가를 "
                     "출발점으로 썼습니다.")
    if win["missing_tail"]:
        tail = win["missing_tail"]
        lines.append(f"- 요청 기간 안 {_days(tail[0], tail[-1])}({len(tail)}거래일) 시세가 아직 저장되지 않아 {win['end_date']}까지만 "
                     "계산했습니다. 끝나지 않은 기간이라 이 수치는 기간 전체 등락이 아닙니다.")
    if win["missing_mid"]:
        mid = win["missing_mid"]
        lines.append(f"- 기간 중간에 시세가 저장되지 않은 거래일 {len(mid)}일({', '.join(mid[:5])}{' 등' if len(mid) > 5 else ''})이 "
                     "있습니다. 출발·끝 종가로만 계산했고 빠진 날의 값은 만들지 않았습니다.")
    lines.append(f"- {name}({kind}) 주가: {span}, {_won(win['from_close'])} → {_won(win['end_close'])}, {_pct(win['pct'])}.")
    pl = _period_premise(question, win["pct"], name)
    if pl:
        lines.append(pl)
    if idx and idx.get("status") == "ok":
        diff = round(win["pct"] - idx["pct"], 2)
        rel = "높았습니다" if diff > 0 else ("낮았습니다" if diff < 0 else "같았습니다")
        lines.append(f"- 같은 기간 {idx['name']} 지수: {idx['from_date']} {_num(idx['from_close'])} → {idx['end_date']} "
                     f"{_num(idx['end_close'])}, {_pct(idx['pct'])}. {name}({kind}) 수익률은 {idx['name']} 지수보다 "
                     f"{abs(diff):.2f}%p {rel}.")
        lines.append(f"- 지수 비교는 참고입니다. {name} 주가가 지수와 같은 방향(또는 반대 방향)으로 움직였다는 사실만으로 원인을 "
                     "판단하지 않습니다.")
    elif idx and idx.get("status") == "missing":
        lines.append(f"- 같은 날짜({', '.join(idx['missing'])})의 {idx['name']} 지수 종가가 없어 지수와 비교하지 않았습니다.")
    elif idx and idx.get("status") == "no_index":
        kind = "ETF라 상장 시장 지수 정보가 없어" if getattr(company, "asset_type", None) == "etf" else "상장 시장 정보가 없어"
        lines.append(f"- {josa(name, '은/는')} {kind} 시장 지수와 비교하지 않았습니다. 이 수치는 {name} 자체의 등락이며 구성 종목이나 "
                     "지수의 등락이 아닙니다.")
    found = found or {"news": [], "disclosures": []}
    if found.get("long"):
        lines += long_evidence_lines(company, win, found)
        fresh = (data or {}).get("freshness") or ""
        if fresh and fresh != "최신 시세":
            lines.append(f"- 시세가 {fresh} 상태입니다(마지막 저장 거래일 {win['last_stored']}). 그 뒤의 움직임은 알 수 없습니다.")
        return f"{head}\n\n" + "\n".join(lines) + f"\n\n{DISCLAIMER}"
    cut = found.get("cutoff") or cutoff_at(date.fromisoformat(win["end_date"])).strftime("%H:%M")
    rng = f"{win['from_date']} 장 마감 뒤~{win['end_date']} 장 마감 {cut} 전"
    direct_n = sum(1 for n in found.get("news") or [] if not evidence_scope.is_reference(n.get("scope")))
    more = found.get("direct_candidates", found.get("news_candidates", 0))
    extra = f"(직접 다룬 기사 {more:,}건 중 {direct_n}건)" if more > direct_n and direct_n else ""
    lines += evidence_lines(name, what, rng, found, win["pct"], extra)
    if found.get("price_reports"):
        lines.append(f"- 같은 기간 주가·거래 동향을 전한 기사 {found['price_reports']:,}건은 움직임을 묘사한 기사라 원인 후보로 쓰지 않았습니다.")
    if found.get("after_count"):
        lines.append(f"- 시세 기준일 뒤({found['after_first']}~)에 수집된 {name} 기사 {found['after_count']:,}건은 {win['end_date']} "
                     "시세에 반영되지 않아 원인 후보에서 뺐습니다.")
    if found.get("after_close"):
        lines.append(f"- {win['end_date']} 장 마감({cut}) 뒤에 나온 기사 {found['after_close']:,}건도 그날 종가에 반영되지 않아 뺐습니다.")
    unknown = []
    if found.get("unknown_news"):
        unknown.append(f"발행 시각이 없는 기사 {found['unknown_news']:,}건")
    if found.get("unknown_disclosures"):
        unknown.append(f"접수 시각이 없는 공시 {found['unknown_disclosures']:,}건")
    if unknown:
        lines.append(f"- {win['end_date']} 당일 {'·'.join(unknown)}은 장 마감 전후를 알 수 없어 원인 후보에서 뺐습니다.")
    fresh = (data or {}).get("freshness") or ""
    if fresh and fresh != "최신 시세":
        lines.append(f"- 시세가 {fresh} 상태입니다(마지막 저장 거래일 {win['last_stored']}). 그 뒤의 움직임은 알 수 없습니다.")
    return f"{head}\n\n" + "\n".join(lines) + f"\n\n{DISCLAIMER}"


# ---------- 72차(2026-11-18): 3개월 초과 기간·복수 종목 원인 질문 ----------
# 71차는 3개월(93일·63거래일)을 넘는 원인 질문과 종목 둘 이상의 원인 질문을 LLM 경로에 남겼다. LLM은 전제('올랐어')를 실제 등락과 대조하지
# 않았고(삼성전자 100거래일 -39.97%) 종목마다 수치·기사를 나눠 쓰는지 코드가 확인하지 않았다. 이제 둘 다 코드 답이다.
# - 긴 기간: 등락·지수는 71차와 같은 창 규칙으로 계산한다(기간 시작 전 종가가 없으면 계산하지 않는다 = short_history). 기사·공시는 기간이 길어
#   몇 건을 골라 원인으로 연결할 수 없으므로 목록을 붙이지 않고 건수·저장 범위만 밝힌다(출처 sources는 비고 source_status=long_period).
# - 복수 종목: 종목마다 단일 종목 경로(69차 원인 단독·71차 기간)를 그대로 따로 돌리고, 결과를 종목 단락으로 잇는다. 공통 문장만 코드가 붙인다.
MAX_CAUSE_COMPANIES = 3


def long_price_rows(db, company, now: datetime) -> list[tuple]:
    """저장 시세 전체 [(date, close, volume)] 오름차순(장중 행 제외). 긴 기간 창은 outlook.PRICE_ROWS(260행)를 넘는다."""
    from app.briefing import drop_open_session
    from app.models import StockPrice

    rows = (db.query(StockPrice.price_date, StockPrice.close_price, StockPrice.volume)
            .filter(StockPrice.company_id == company.id).order_by(StockPrice.price_date.asc()).all())
    return drop_open_session(list(rows), now, lambda r: r[0])


def count_evidence(db, company, frm: date, end: date) -> dict:
    """긴 기간 창(frm 장 마감 뒤 ~ end 장 마감 전)의 이 종목 뉴스·공시 건수와 저장 범위. 목록은 만들지 않는다(원인 후보로 고르지 않는다).

    건수는 SQL로 센다(제목 판정 없음): 뉴스는 company_id 연결 + is_relevant가 False가 아닌 행, 공시는 접수일 frm 다음 날 ~ end 전날.
    기준일 뒤·장 마감 뒤 건수는 collect()와 같은 규칙이다."""
    from sqlalchemy import func

    from app.models import Disclosure, News
    from app.outlook import kst_date

    lo, cut = cutoff_at(frm), cutoff_at(end)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), KST)
    base_midnight = datetime.combine(end, datetime.min.time(), KST)
    rel = [News.company_id == company.id, News.is_relevant.isnot(False)]
    news_count = (db.query(func.count(News.id)).filter(*rel, News.published_at >= lo, News.published_at < cut,
                                                         News.published_at != base_midnight).scalar())
    news_first = db.query(func.min(News.published_at)).filter(*rel).scalar()
    after_close = db.query(func.count(News.id)).filter(*rel, News.published_at >= cut, News.published_at < hi).scalar()
    unknown_news = db.query(func.count(News.id)).filter(*rel, News.published_at == base_midnight).scalar()
    after_count, after_min = db.query(func.count(News.id), func.min(News.published_at)).filter(*rel, News.published_at >= hi).one()
    out = {"long": True, "news": [], "disclosures": [], "news_count": int(news_count or 0), "news_first": kst_date(news_first),
           "after_close": int(after_close or 0), "unknown_news": int(unknown_news or 0), "after_count": int(after_count or 0),
           "after_first": kst_date(after_min), "unknown_disclosures": 0, "cutoff": cut.strftime("%H:%M"),
           "disclosure_count": None, "disclosure_first": None, "disclosure_collection_first": None}
    if getattr(company, "asset_type", None) != "etf":
        dlo = datetime.combine(frm + timedelta(days=1), datetime.min.time(), KST)
        dhi = datetime.combine(end, datetime.min.time(), KST)
        out["disclosure_count"] = int(db.query(func.count(Disclosure.id)).filter(
            Disclosure.company_id == company.id, Disclosure.disclosed_at >= dlo, Disclosure.disclosed_at < dhi).scalar() or 0)
        out["disclosure_first"] = kst_date(db.query(func.min(Disclosure.disclosed_at))
                                           .filter(Disclosure.company_id == company.id).scalar())
        out["disclosure_collection_first"] = kst_date(db.query(func.min(Disclosure.disclosed_at)).scalar())
    return out


def long_evidence_lines(company, win: dict, found: dict) -> list[str]:
    """긴 기간 원인 질문의 근거 문단: 원인을 특정하지 않고 건수·저장 범위·짧은 기간으로 나눠 묻는 방법만 밝힌다."""
    name = company.name
    etf = getattr(company, "asset_type", None) == "etf"
    rng = f"{win['from_date']} 장 마감 뒤~{win['end_date']} 장 마감 {found.get('cutoff') or '15:30'} 전"
    counts = f"뉴스 {found.get('news_count', 0):,}건" + ("" if etf else f"·공시 {found.get('disclosure_count') or 0:,}건")
    lines = [f"- 기간이 3개월보다 길어, 같은 기간({rng}) 저장된 {name} {counts} 중 몇 건을 골라 주가 움직임의 원인으로 연결할 수 없습니다. "
             "원인을 특정하지 않고, 기사·공시 목록도 원인 후보로 붙이지 않았습니다."]
    nf = found.get("news_first")
    if nf and nf > win["from_date"]:
        lines.append(f"- {name} 기사는 {nf}부터 저장돼 있어 기간 앞부분({win['from_date']}~{nf})의 기사는 없습니다.")
    if not etf:
        cf = found.get("disclosure_collection_first")
        if cf and cf > win["from_date"]:
            lines.append(f"- 공시는 {cf} 접수분부터 수집돼 있어 그 전 공시는 세지 못했습니다.")
    lines.append("- 원인을 보시려면 \"최근 1개월\", \"지난달\", \"최근 20거래일\"처럼 3개월 이내 기간으로 나눠 물어봐 주세요. "
                 "그 기간 안·시세 기준 시점 전에 나온 기사·공시만 근거로 보여 드립니다.")
    if found.get("after_count"):
        lines.append(f"- 시세 기준일 뒤({found['after_first']}~)에 수집된 {name} 기사 {found['after_count']:,}건은 {win['end_date']} "
                     "시세에 반영되지 않아 세지 않았습니다.")
    return lines


def cause_cutoff_info(base, found: dict | None, policy: str = "cause_window") -> dict:
    """코드 원인 경로가 news_tool·disclosure_tool 기록에 남기는 기준 시점 정보(app.sources.source_status가 읽는다).

    excluded_after는 DB에 저장된 그 종목 자료 중 기준일 장 마감 뒤라 원인 후보에서 뺀 건수(기준일 뒤 + 당일 마감 뒤)다."""
    f = found or {}
    day = date.fromisoformat(str(base)[:10])
    return {"base_date": day.isoformat(), "cutoff": f.get("cutoff") or cutoff_at(day).strftime("%H:%M"),
            "evidence_cutoff": cutoff_at(day).isoformat(), "policy": policy,
            "excluded_after": int(f.get("after_count") or 0) + int(f.get("after_close") or 0),
            "excluded_unknown_time": int(f.get("unknown_news") or 0) + int(f.get("unknown_disclosures") or 0)}


def _strip_disclaimer(text: str) -> str:
    from app.disclaimer import DISCLAIMER

    t = (text or "").rstrip()
    return t[: -len(DISCLAIMER)].rstrip() if t.endswith(DISCLAIMER) else t


def _move_of(records: list[dict]) -> dict | None:
    """단일 종목 경로 기록에서 (출발일, 끝일, 수익률). 기간 경로는 stock_tool의 period_*, 원인 단독은 최근 5거래일(regime_inputs)."""
    for r in records:
        res = r.get("result")
        if r.get("tool_name") != "stock_tool" or not isinstance(res, dict):
            continue
        if res.get("period_change_pct") is not None:
            return {"from": res["period_start_date"], "end": res["period_end_date"], "pct": res["period_change_pct"]}
        ind = res.get("indicators") or {}
        if ind.get("base_date"):
            mv = movement(ind)
            if mv["from_date"] and mv["r"] is not None:
                return {"from": mv["from_date"], "end": mv["base"], "pct": mv["r"]}
    return None


def multi_too_many_answer(companies: list) -> str:
    from app.disclaimer import DISCLAIMER

    names = ", ".join(c.name for c in companies)
    return (f"질문에서 종목 {len(companies)}개({names})를 찾았습니다. 원인 분석은 종목마다 시세·지수·기사·공시를 따로 계산하므로 한 번에 "
            f"{MAX_CAUSE_COMPANIES}종목까지만 답합니다. 종목 수를 줄이거나 종목별로 나눠 물어봐 주세요.\n\n{DISCLAIMER}")


def multi_cause_answer(parts: list[tuple], question: str) -> str:
    """복수 종목 원인 질문의 코드 답. parts = [(company, 단일 종목 답, 그 종목 기록)]. 수치·기사는 각 종목 단락의 것을 그대로 쓴다."""
    from app.disclaimer import DISCLAIMER

    names = "·".join(c.name for c, _, _ in parts)
    lines = [f"종목별 주가 움직임과 원인 · {names}",
             "",
             f"- {len(parts)}종목의 시세·수익률·시장 지수·기사·공시를 종목마다 따로 조회하고 계산했습니다. 한 종목의 수치나 기사를 다른 종목에 "
             "쓰지 않았습니다. 각 종목의 기사·공시는 그 종목에 연결되고 제목에 그 종목 이름이 있는 자료만입니다."]
    moves = [(c, _move_of(recs)) for c, _, recs in parts]
    for c, mv in moves:
        if mv:
            lines.append(f"- {c.name}({c.ticker}): {mv['from']} 종가 → {mv['end']} 종가, {_pct(mv['pct'])}")
        else:
            lines.append(f"- {c.name}({c.ticker}): 이 기간 등락을 계산하지 못했습니다(아래 {c.name} 단락의 사유).")
    got = [(c, mv) for c, mv in moves if mv]
    if len(got) >= 2:
        spans = {(mv["from"], mv["end"]) for _, mv in got}
        if len(spans) == 1:
            hi = max(got, key=lambda x: x[1]["pct"])
            lo = min(got, key=lambda x: x[1]["pct"])
            if hi[1]["pct"] != lo[1]["pct"]:
                lines.append(f"- 같은 두 날짜의 종가로 계산해 나란히 볼 수 있습니다: {josa(hi[0].name, '이/가')} {lo[0].name}보다 "
                             f"{hi[1]['pct'] - lo[1]['pct']:.2f}%p 높았습니다. 수익률 차이가 원인을 뜻하지는 않습니다.")
        else:
            lines.append("- 종목마다 계산한 날짜가 달라(시세 기준일 차이) 수익률을 서로 직접 비교하지 않습니다.")
    body = [f"### {i}. {c.name}\n\n{_strip_disclaimer(text)}" for i, (c, text, _) in enumerate(parts, 1)]
    return "\n".join(lines) + "\n\n" + "\n\n".join(body) + f"\n\n{DISCLAIMER}"


# 72차: "카카오랑 삼송전자 왜 떨어졌어?"처럼 종목 이름 옆에 접속 조사로 붙은 다른 이름을 찾지 못했으면(오타·미등록) 그 사실을 밝힌다.
# 찾은 종목만 조용히 답하면 질문의 두 종목 중 하나가 빠진 것을 모른다. 일반어(시장·관련·주가 등)로 시작하는 말은 이름으로 보지 않는다.
_CONJ = r"(?:이랑|랑|와|과|하고|및|,|·)"
_PARTICLE_RE = re.compile(r"(?:이랑|랑|와|과|하고|은|는|이|가|의|도|을|를|만)$")
_NOT_NAME_RE = re.compile(r"^(?:시장|코스피|코스닥|지수|증시|업종|관련|같은|함께|비교|경쟁|최근|지난|이번|오늘|어제|요즘|주가|주식|종목|"
                          r"뉴스|공시|실적|환율|달러|반도체|섹터|그룹|계열|동종|다른|우리|내|제|그|이|저|왜|어떻|얼마|기간|한|두|세|\d)")
ASK_CAUSE_SUBJECT = ("질문에서 종목을 찾지 못했습니다. 어떤 종목의 움직임 원인을 물으시는지 종목명 또는 종목코드로 알려주세요. "
                     "예: \"삼성전자 지난달 왜 떨어졌어?\"")


def unmatched_names(question: str, companies: list) -> list[str]:
    """질문에서 찾은 종목 이름(별칭·코드 포함) 바로 옆에 접속 조사로 붙었는데 종목으로 찾지 못한 이름들(나온 순서)."""
    from app.tools.company_resolver import _ALIASES_BY_NAME

    q = question or ""
    known = set()
    for c in companies or []:
        known |= {c.name.casefold(), str(c.ticker).casefold(), *(a.casefold() for a in _ALIASES_BY_NAME.get(c.name, ()))}
    out = []
    for c in companies or []:
        for token in sorted({c.name, *(_ALIASES_BY_NAME.get(c.name, ()))}, key=len, reverse=True):
            t = re.escape(token)
            pats = [re.compile(r"(?:^|\s)([0-9A-Za-z가-힣]{2,20}?)" + _CONJ + r"\s*" + t, re.IGNORECASE),
                    re.compile(t + r"\s*" + _CONJ + r"\s*([0-9A-Za-z가-힣]{2,20})", re.IGNORECASE)]
            for pat in pats:
                for m in pat.finditer(q):
                    word = _PARTICLE_RE.sub("", m.group(1)) if pat is pats[1] else m.group(1)
                    w = word.casefold()
                    if len(word) < 2 or _NOT_NAME_RE.match(word) or any(w == k or w.startswith(k) or k.startswith(w) for k in known):
                        continue
                    if word not in out:
                        out.append(word)
    return out


def unmatched_note(names: list[str]) -> str:
    quoted = ", ".join(f"'{n}'" for n in names)
    return (f"- 질문의 {quoted}{'은' if josa(names[-1], '은/는').endswith('은') else '는'} 등록된 종목에서 찾지 못해 이 답에 넣지 않았습니다. "
            "오타라면 정확한 종목명이나 종목코드로 다시 물어봐 주세요. 찾지 못한 종목의 수치·기사는 만들지 않습니다.")


# 72차: 종목 없이 시장(코스피·코스닥·증시)의 등락 원인을 묻는 질문. 시장 전체를 다룬 뉴스는 수집 대상이 아니라(뉴스는 종목에 연결) 원인 근거가
# 없다. 전에는 LLM 경로라 근거 없이 원인을 쓸 수 있었다. 지수 등락만 종목 기간 창과 같은 규칙으로 계산하고 원인은 특정하지 않는다.
_KOSPI_RE = re.compile(r"코스피|KOSPI", re.IGNORECASE)
_KOSDAQ_RE = re.compile(r"코스닥|KOSDAQ", re.IGNORECASE)


def is_market_cause_question(question: str) -> bool:
    q = question or ""
    return _cause_gate(q) and is_market_subject(q)


def market_codes(question: str) -> list[str]:
    q = question or ""
    codes = (["KS11"] if _KOSPI_RE.search(q) else []) + (["KQ11"] if _KOSDAQ_RE.search(q) else [])
    return codes or ["KS11", "KQ11"]


def market_spec(question: str, now: datetime) -> dict:
    """시장 원인 질문의 기간: 기간 표현이 있으면 period_spec, 오늘·어제는 1거래일(요청일 확인), 없으면 최근 5거래일."""
    spec = period_spec(question, now)
    if spec is not None:
        return spec
    day = requested_day(question, now)
    if day is not None:
        return {"kind": "trading", "text": day[0], "start": None, "end": None, "trading": True, "n": 1, "too_long": False,
                "day": day[1].isoformat()}
    return {"kind": "trading", "text": f"최근 {CAUSE_DAYS}거래일", "start": None, "end": None, "trading": True, "n": CAUSE_DAYS,
            "too_long": False}


def market_cause_answer(spec: dict, wins: list[tuple[str, dict]], question: str) -> str:
    """wins = [(지수 이름, period_window 결과)]. 지수 등락은 코드 계산, 원인은 특정하지 않는다."""
    from app.disclaimer import DISCLAIMER

    head = "시장 지수 움직임과 원인 · " + "·".join(n for n, _ in wins)
    if spec["kind"] in ("future", "invalid", "unsupported"):
        why = {"future": f"질문하신 {_quote_topic(spec['text'])} 아직 오지 않은 기간이라 계산하지 않았습니다.",
               "invalid": f"질문의 기간 {_quote_topic(spec['text'])} 계산할 수 있는 기간이 아닙니다.",
               "unsupported": f"질문의 기간 표현 {_quote_topic(spec['text'])} 해석하지 못해 다른 기간으로 바꿔 계산하지 않았습니다."}[spec["kind"]]
        return f"{head}\n\n## 지수 움직임과 원인\n- {why}\n\n{DISCLAIMER}"
    lines = ["## 지수 움직임과 원인(저장된 지수 종가로 코드 계산)"]
    label = spec["text"] if spec["trading"] else f"'{spec['text']}'"
    if spec.get("day") and wins and (wins[0][1].get("last_stored") or "") < spec["day"]:
        lines.append(f"- 질문하신 '{spec['text']}'({spec['day']}) 지수 종가는 아직 저장되지 않아 마지막 저장 거래일 기준으로 답합니다.")
        label = "마지막 저장 거래일 하루"  # 요청한 날이 아닌 값을 '오늘'이라고 부르지 않는다
    for name, w in wins:
        if w.get("status") != "ok":
            lines.append(f"- {name}: {label} 기간의 저장된 지수 종가가 없어 계산하지 않았습니다(마지막 저장 거래일 {w.get('last_stored')}).")
            continue
        extra = " 요청 기간 끝까지 저장되지 않아 기간 전체 등락이 아닙니다." if w.get("missing_tail") else ""
        lines.append(f"- {name} 지수({label}): {w['from_date']} {_num(w['from_close'])} → {w['end_date']} {_num(w['end_close'])}, "
                     f"{_pct(w['pct'])}({w['trading_days']}거래일).{extra}")
        pl = _period_premise(question, w["pct"], f"{name} 지수")
        if pl:
            lines.append(pl.replace(f"{name} 지수 주가는", f"{name} 지수는"))
    lines.append("- 시장 전체의 움직임을 설명하는 뉴스·지표(금리·환율·수급 등)는 이 서비스가 원인 근거로 수집하지 않아 원인을 특정할 수 없습니다. "
                 "위 수치는 지수 움직임의 크기와 방향일 뿐입니다.")
    lines.append("- 종목의 움직임 원인을 보시려면 \"삼성전자 지난달 왜 떨어졌어?\"처럼 종목을 넣어 물어봐 주세요.")
    return f"{head}\n\n" + "\n".join(lines) + f"\n\n{DISCLAIMER}"
