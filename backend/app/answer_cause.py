"""원인 + 전망 복합 질문의 "최근 주가 움직임과 원인" 단락(68차, 2026-11-17). 문장은 코드 템플릿이고 LLM을 쓰지 않는다.

실사용 #43444("하이닉스가 왜 올랐어? 그리고 앞으로 7일간 오를 거 같아?")는 LLM 경로로 가서
(1) 전망을 빠뜨렸고 (2) 시세 기준일(11-12) 뒤에 나온 11-17 기사로 원인을 설명했고 (3) "올랐다"는 전제를 실제 등락과 맞춰 보지 않았다.
이 단락의 규칙:
- 움직임: 저장된 시세의 기준일 하루 등락과 최근 5거래일 등락(기준일 종가 기준). 질문의 전제(올랐다·내렸다)와 반대면 그렇다고 밝힌다.
- 원인 후보: 그 5거래일 창(비교 기준일 ~ 기준일, KST 날짜)에 발행된 이 종목 기사(제목에 종목 이름, 전망 경로와 같은 후처리)와 접수된 공시.
  기준일 뒤 기사는 그 시세에 반영되지 않았으므로 원인 후보에서 빼고 건수만 밝힌다.
- 후보가 없으면 원인을 특정할 수 없다고 쓴다. 후보가 있어도 직접 원인이라고 단정하지 않는다.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.briefing import KST

CAUSE_DAYS = 5  # 움직임·원인 창(거래일). 전망 기간과 관계없이 최근 1주일
NEWS_LIMIT = 3
DISCLOSURE_LIMIT = 3
NEWS_FETCH = 300  # 창 안, 기준일 뒤 각각 읽는 기사 수 상한(대형주는 하루 수십 건)
_UP_RE = re.compile(r"올랐|오른\s*(?:이유|원인|거)|상승했|상승한\s*(?:이유|원인)|급등|뛰었|올라갔|강세")
_DOWN_RE = re.compile(r"내렸|내린\s*(?:이유|원인|거)|하락했|하락한\s*(?:이유|원인)|떨어졌|떨어진|급락|빠졌|빠진|약세")


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
    others = ([n for (n,) in db.query(Company.name).filter(Company.asset_type == "etf", Company.id != company.id)]
              if getattr(company, "asset_type", None) == "etf" else [])

    def relevant(rows):
        items = [{"company_name": company.name, "title": r.title, "source": r.source, "url": r.url,
                  "published_at": r.published_at.isoformat() if r.published_at else None, "date": kst_date(r.published_at)}
                 for r in rows]
        return filter_titles(evidence_news(company.name, company.asset_type, items, other_etf_names=others))

    base_q = db.query(News).filter(News.company_id == company.id, News.is_relevant.isnot(False))
    # 창(start~base, KST) 안의 기사를 직접 읽는다. 최근 N건만 읽으면 시세가 늦을 때 창 안의 기사가 하나도 안 읽힌다(대형주는 하루 수십 건)
    in_window = relevant(base_q.filter(News.published_at >= lo, News.published_at < hi)
                         .order_by(News.published_at.desc()).limit(NEWS_FETCH).all())
    # 기준일 뒤 기사는 원인 후보가 아니므로 건수와 첫 날짜만 센다(이 종목에 수집된 기사 기준, 제목 판정 없이 SQL로 정확히)
    after_count, after_min = (db.query(func.count(News.id), func.min(News.published_at))
                              .filter(News.company_id == company.id, News.is_relevant.isnot(False), News.published_at >= hi).one())
    key = key_news(in_window)
    rest = [n for n in in_window if n not in key][:max(0, NEWS_LIMIT - len(key))]
    news = sorted(key + rest, key=lambda n: n.get("published_at") or "", reverse=True)[:NEWS_LIMIT]
    disclosures = []
    if getattr(company, "asset_type", None) != "etf":
        # 공시 접수일은 KST 날짜를 UTC 자정 근처로 저장한 행이 있어(disclosure_date) 하루씩 넓게 읽고 날짜로 거른다
        dlo = datetime.combine(start - timedelta(days=1), datetime.min.time(), KST)
        dhi = datetime.combine(base + timedelta(days=2), datetime.min.time(), KST)
        drows = (db.query(Disclosure).filter(Disclosure.company_id == company.id, Disclosure.disclosed_at >= dlo,
                                             Disclosure.disclosed_at < dhi)
                 .order_by(Disclosure.disclosed_at.desc()).all())
        for d in drows:
            day = disclosure_date(d.source_url, d.disclosed_at)
            if day and start.isoformat() <= day <= base.isoformat():
                same = next((x for x in disclosures if x["title"] == d.title and x["date"] == day), None)
                if same is not None:  # 같은 날 같은 제목(정정 여러 건)은 한 줄로 묶고 건수만 센다
                    same["count"] = same.get("count", 1) + 1
                    continue
                disclosures.append({"company_name": company.name, "title": d.title, "disclosure_type": d.disclosure_type,
                                    "disclosed_at": d.disclosed_at.isoformat() if d.disclosed_at else None,
                                    "source_url": d.source_url, "date": day})
        disclosures = disclosures[:DISCLOSURE_LIMIT]
    return {"news": news, "disclosures": disclosures, "after_count": int(after_count or 0), "after_first": kst_date(after_min)}


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


def section(company, res: dict, question: str, found: dict) -> str:
    """"## 최근 주가 움직임과 원인" 단락."""
    ind, data = res["indicators"], res["data"]
    mv = movement(ind)
    base = mv["base"]
    span = f"{mv['from_date']}~{base}" if mv["from_date"] else base
    lines = [f"## 최근 주가 움직임과 원인({span} 종가 기준)"]
    move = f"- 실제 움직임: 기준일 {base} 종가 {_won(mv['close'])}, 전 거래일({mv['prev']}) 대비 {_pct(mv['day'])}"
    if mv["r"] is not None:
        move += f", 최근 {CAUSE_DAYS}거래일({mv['from_date']} 종가 {_won(mv['from_close'])} 대비) {_pct(mv['r'])}"
    lines.append(move + ".")
    pl = premise_line(question, mv)
    if pl:
        lines.append(pl)
    rel = ind.get("relative")
    if rel and rel.get("period_days") == CAUSE_DAYS:
        word = {"in_line": "시장과 비슷했습니다", "outperform": "시장보다 좋았습니다", "underperform": "시장보다 부진했습니다"}
        lines.append(f"- 같은 기간 지수 {_pct(rel.get('market_change_pct'))}, 종목 {_pct(rel.get('stock_change_pct'))}로 "
                     f"{word.get(rel.get('relative'), '시장과 비교했습니다')}(차이 {rel.get('diff_pct_point', 0):+.2f}%p).")
    news, discs = found["news"], found["disclosures"]
    if news or discs:
        lines.append(f"- 같은 기간({span}) 이 종목 기사·공시입니다. 주가 변동의 직접 원인이라고 단정할 수는 없습니다.")
        for n in news:
            src = f"({n['source']}) " if n.get("source") else ""
            lines.append(f"  - 뉴스 {n.get('date') or ''} {src}\"{n['title']}\"")
        for d in discs:
            many = f"(같은 제목 {d['count']}건)" if d.get("count", 1) > 1 else ""
            lines.append(f"  - 공시 {d.get('date') or ''} 접수 \"{d['title']}\"{many}")
    else:
        what = "기사" if getattr(company, "asset_type", None) == "etf" else "기사·공시"
        lines.append(f"- 같은 기간({span}) 이 종목을 직접 다룬 {what}를 찾지 못해 움직임의 원인을 특정할 수 없습니다.")
    if found["after_count"]:
        lines.append(f"- 기준일 뒤({found['after_first']}~)에 수집된 이 종목 기사 {found['after_count']:,}건은 {base} 시세에 반영되지 않아 "
                     "원인 후보에서 뺐습니다.")
    fresh = data.get("freshness") or ""
    if fresh and fresh != "최신 시세":
        lines.append(f"- 시세가 {fresh} 상태라 {base} 뒤의 움직임은 알 수 없습니다.")
    return "\n".join(lines)
