"""개인화 일일 투자 브리핑의 계산·조립부(2026-11-06). DB·Ollama·네트워크 없이 평범한 값(Facts)만 받아 dict를 만든다.

기존 GET /api/briefing(app/briefing.py)은 요청할 때마다 다시 계산하는 "지금 화면" 응답이고 저장하지 않는다. 이 모듈은 그 계산
(build_briefing: 보유·관심 종목 시세·평가·flag·뉴스·공시, 보유와 겹치는 관심 종목 제거)을 그대로 재사용하고, 그 위에
① 데이터 신선도(app/outlook의 기대 거래일·영업일 지연·수집 상태 규칙) ② 시장 5거래일 흐름·시장 관련 기사 ③ 업종·기초지수 간접 뉴스
(app/related_news) ④ 포트폴리오 위험 점검(app/diagnosis의 compute_portfolio·compute_flags, app/exposure의 ETF 실질 노출)
⑤ 마지막 리서치 이후 새 뉴스·공시 ⑥ 오늘 확인할 사항(최대 3개)을 더해 5개 영역으로 조립한다. 저장은 app/daily_briefing_db.

숫자는 모두 DB 값이나 결정적 계산 결과이고, 문장은 app/daily_briefing_text의 언어별 템플릿이다(같은 Facts를 언어만 바꿔 다시 그린다).
LLM은 요약 한 단락만 쓸 수 있고(app/daily_briefing_db.llm_summary), 숫자 검증을 통과하지 못하면 코드 요약이 대신한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from app import briefing_i18n, diagnosis_i18n, exposure_i18n
from app import daily_briefing_text as tx
from app.briefing import KST, RECENT_DAYS, Bar
from app.data_status import business_days_behind
from app.diagnosis import HoldingInput, compute_flags, compute_portfolio
from app.i18n import disclaimer
from app.news_relevance import is_duplicate_title, title_names_subject
from app.outlook import MAX_LAG_BUSINESS_DAYS, PRICE_STATUS_LATEST, expected_trading_day
from app.outlook_i18n import _PRICE_STATUS

MAX_CHECKS = 3
NEWS_SHOWN = 3  # 종목당 보이는 뉴스 묶음 수
MARKET_NEWS_SHOWN = 3
RETURN_LONG_DAYS = 20
EARNINGS_RE = re.compile(r"실적|매출액\s*또는\s*손익구조|영업\s*\(\s*잠정\s*\)")

STATUS_LATEST, STATUS_DELAYED, STATUS_STALE, STATUS_NO_DATA = "latest", "delayed", "stale", "no_data"
SECTION_OK, SECTION_PARTIAL, SECTION_NO_DATA, SECTION_EMPTY = "ok", "partial", "no_data", "empty"
SECTIONS = ("market", "holdings", "watchlist", "risk", "checklist")


@dataclass
class Facts:
    """DB 계층(app/daily_briefing_db)이 채우는 값. brief는 app.briefing.build_briefing의 결과(한국어)다."""

    now: datetime
    timezone: str
    brief: dict
    bars: dict[str, list[Bar]] = field(default_factory=dict)  # 장중 행을 뺀 종목별 시세(20거래일 등락률용)
    index_series: dict[str, list[tuple[date, float]]] = field(default_factory=dict)  # kospi·kosdaq·usd_krw, 장중 행 제외
    price_status: dict | None = None  # outlook.price_status(기대 거래일 시세가 없을 때 이유)
    last_refresh: dict | None = None  # data_status.summarize_last_run
    news_collected_at: datetime | None = None
    disclosure_collected_at: datetime | None = None
    market_news: list[dict] = field(default_factory=list)
    indirect: dict[str, tuple[str | None, str | None, list[dict]]] = field(default_factory=dict)  # ticker -> related_news.indirect_news
    research: dict[str, dict] = field(default_factory=dict)  # ticker -> {"at": datetime, "news": int, "disclosures": int}
    markets: dict[str, str | None] = field(default_factory=dict)  # 보유 종목 ticker -> company.market
    exposure: dict | None = None  # exposure_db.get_exposure 결과(보유가 없으면 None)
    watch_dedup: int = 0  # 보유와 겹쳐 관심 영역에서 뺀 관심 종목 수
    intraday: bool = False
    known_names: list[str] = field(default_factory=list)  # DB의 모든 종목명(LLM 요약에 브리핑에 없는 종목이 나오는지 검사)


# ---------- 계산 ----------


def _closes(series) -> list[tuple[date, float]]:
    return sorted(((d, c) for d, c in series if c is not None and c > 0), reverse=True)


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old <= 0:
        return None
    return round((new / old - 1) * 100, 2)


def series_stats(series) -> dict | None:
    """(날짜, 값) 시계열의 최신값, 1일·5거래일 변화율. 없으면 None."""
    rows = _closes(series)
    if not rows:
        return None
    (d0, c0) = rows[0]
    return {"date": d0.isoformat(), "close": c0,
            "change_pct": _pct(c0, rows[1][1]) if len(rows) > 1 else None,
            "week_change_pct": _pct(c0, rows[5][1]) if len(rows) > 5 else None}


def return_long(bars: list[Bar], n: int = RETURN_LONG_DAYS) -> float | None:
    rows = sorted((b for b in bars if b.close is not None and b.close > 0), key=lambda b: b.date, reverse=True)
    return _pct(rows[0].close, rows[n].close) if len(rows) > n else None


def lag_of(d: str | date | None, now: datetime) -> int | None:
    """기대 거래일(outlook.expected_trading_day: 장중이면 직전 평일) 대비 늦은 영업일 수."""
    if d is None:
        return None
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return business_days_behind(d, expected_trading_day(now))


def freshness(f: Facts) -> dict:
    """데이터 신선도. 판정은 outlook과 같다: 늦음 0이면 latest, 1~MAX_LAG_BUSINESS_DAYS면 delayed, 그 초과면 stale, 시세가 없으면 no_data."""
    b = f.brief
    as_of = b.get("data_date")
    expected = expected_trading_day(f.now).isoformat()
    lag = lag_of(as_of, f.now)
    if as_of is None:
        status = STATUS_NO_DATA
    elif lag == 0:
        status = STATUS_LATEST
    elif lag <= MAX_LAG_BUSINESS_DAYS:
        status = STATUS_DELAYED
    else:
        status = STATUS_STALE
    st = f.price_status or {}
    sources = {}
    for key in ("kospi", "kosdaq", "usd_krw"):
        s = series_stats(f.index_series.get(key, []))
        sources[key] = {"date": s["date"] if s else None, "lag_business_days": lag_of(s["date"], f.now) if s else None}
    return {
        "status": status,
        "data_as_of": as_of,
        "expected_date": expected,
        "lag_business_days": lag,
        "price_status": st.get("status") if st.get("status") != PRICE_STATUS_LATEST else None,
        "price_status_detail": st.get("detail") or None,
        "sources": sources,
        "news_collected_at": f.news_collected_at.astimezone(KST).isoformat() if f.news_collected_at else None,
        "disclosure_collected_at": f.disclosure_collected_at.astimezone(KST).isoformat() if f.disclosure_collected_at else None,
        "last_refresh": f.last_refresh,
        "intraday": f.intraday,
    }


def group_news(items: list[dict], limit: int) -> list[dict]:
    """비슷한 제목(news_relevance.is_duplicate_title)을 하나로 묶는다. 앞의 기사(최신)를 대표로 두고 묶인 수를 similar_count에 센다."""
    groups: list[dict] = []
    for it in items:
        for g in groups:
            if is_duplicate_title(it.get("title"), g["title"]):
                g["similar_count"] += 1
                break
        else:
            groups.append({**it, "similar_count": 1})
    return groups[:limit]


def direct_news(it: dict) -> list[dict]:
    """직접 관련 뉴스: 제목이 그 종목을 가리키는 기사만(news_relevance.title_names_subject, 통칭 포함). 비슷한 제목은 묶는다.
    수집 때 종목에 연결됐어도 제목에 종목이 없는 기사(시황 마감 기사 등)는 직접 관련으로 보이지 않는다."""
    kept = [n for n in it["news"] if title_names_subject(it["name"], it["asset_type"], n["title"])]
    return group_news(kept, NEWS_SHOWN)


def _risk_inputs(f: Facts) -> list[HoldingInput]:
    out = []
    for h in f.brief.get("holdings") or []:
        out.append(HoldingInput(ticker=h["ticker"], company_name=h["name"], market=f.markets.get(h["ticker"]), quantity=int(h["quantity"]),
                                avg_price=float(h["avg_price"]), current_price=h["latest_close"],
                                return_20d_pct=return_long(f.bars.get(h["ticker"], [])), asset_type=h["asset_type"]))
    return out


# ---------- 그리기(언어별) ----------


def _brief_in(f: Facts, L: str) -> dict:
    return f.brief if L == "ko-KR" else briefing_i18n.localize(f.brief, L)


def _fresh_notices(fr: dict, L: str) -> tuple[str, list[str]]:
    """(맨 위 경고 또는 안내 한 문장, 추가 안내들)."""
    st = fr["price_status"]
    why = tx.t("fresh_why", L, status=st if L == "ko-KR" else _PRICE_STATUS.get(st, {}).get(L, st)) if st else ""
    if fr["status"] == STATUS_NO_DATA:
        head = tx.t("fresh_none", L)
    elif fr["status"] == STATUS_LATEST:
        head = tx.t("fresh_latest", L, base=fr["data_as_of"])
    else:
        key = "fresh_delayed" if fr["status"] == STATUS_DELAYED else "fresh_stale"
        head = tx.t(key, L, lag=fr["lag_business_days"], base=fr["data_as_of"], exp=fr["expected_date"], why=why)
    extra = []
    names = {"kospi": "src_index_kospi", "kosdaq": "src_index_kosdaq", "usd_krw": "src_fx"}
    for key, s in fr["sources"].items():
        if s["date"] is None:
            extra.append(tx.t("fresh_source_none", L, source=tx.t(names[key], L)))
        elif s["lag_business_days"]:
            extra.append(tx.t("fresh_source_lag", L, source=tx.t(names[key], L), date=s["date"], lag=s["lag_business_days"]))
    lr = fr["last_refresh"]
    if lr and not lr.get("ok") and lr.get("failed_steps"):
        extra.append(tx.t("fresh_refresh_failed", L, at=(lr.get("finished_at") or "")[:16].replace("T", " "), steps=", ".join(lr["failed_steps"])))
    if fr["intraday"]:
        extra.append(tx.t("fresh_intraday", L))
    return head, extra


def _market(f: Facts, L: str) -> dict:
    items = []
    for key in ("kospi", "kosdaq", "usd_krw"):
        s = series_stats(f.index_series.get(key, []))
        name = tx.t(f"market_{key}", L)
        if s is None:
            items.append({"code": key, "name": name, "date": None, "close": None, "change_pct": None, "week_change_pct": None,
                          "text": tx.t("mkt_none", L, name=name)})
            continue
        close = tx.won(s["close"], L) if key == "usd_krw" else tx.num(s["close"], L)
        items.append({"code": key, "name": name, **s, "lag_business_days": lag_of(s["date"], f.now),
                      "text": tx.t("mkt_line", L, name=name, date=s["date"], close=close, d1=tx.pct(s["change_pct"], L),
                                   d5=tx.pct(s["week_change_pct"], L))})
    news = group_news(f.market_news, MARKET_NEWS_SHOWN)
    have = sum(1 for i in items if i["date"])
    status = SECTION_OK if have == 3 else SECTION_PARTIAL if have else SECTION_NO_DATA
    return {"title": tx.t("title_market", L), "status": status, "indices": items,
            "news_label": tx.t("mkt_news_label", L), "news": news,
            "news_empty_text": None if news else tx.t("mkt_news_none", L, n=RECENT_DAYS)}


def _indirect(f: Facts, ticker: str, L: str) -> dict | None:
    section, topic, items = f.indirect.get(ticker) or (None, None, [])
    if not section or not items:
        return None
    kind = "index" if "기초지수" in section else "sector"
    return {"kind": kind, "label": tx.t(f"news_{kind}_label", L), "topic": tx.topic(topic, L), "items": group_news(items, NEWS_SHOWN)}


def _item_common(it: dict, f: Facts, L: str, base: str | None) -> dict:
    d20 = return_long(f.bars.get(it["ticker"], []))
    if it["data_available"]:
        close = tx.won(it["latest_close"], L)
        price_text = tx.t("item_price", L, date=it["latest_date"], close=close, d1=tx.pct(it["day_change_pct"], L),
                          d5=tx.pct(it["week_change_pct"], L), d20=tx.pct(d20, L))
    else:
        price_text = tx.t("item_no_price", L)
    notes = []
    if it["latest_date"] and base and it["latest_date"] < base:
        notes.append(tx.t("item_older_price", L, date=it["latest_date"], base=base))
    news = direct_news(it)
    is_etf = it["asset_type"] == "etf"
    return {
        "ticker": it["ticker"], "name": it["name"], "asset_type": it["asset_type"],
        "latest_date": it["latest_date"], "lag_business_days": lag_of(it["latest_date"], f.now),
        "latest_close": it["latest_close"], "day_change_pct": it["day_change_pct"], "week_change_pct": it["week_change_pct"],
        "return_20d_pct": d20, "volume": it["volume"], "avg_volume_20d": it["avg_volume_20d"], "volume_ratio": it["volume_ratio"],
        "price_text": price_text, "notes": notes,
        "news_label": tx.t("news_direct_label", L), "news": news,
        "news_empty_text": None if news else tx.t("item_no_news", L, n=RECENT_DAYS),
        "indirect_news": _indirect(f, it["ticker"], L),
        "disclosures": it["disclosures"], "disclosure_count": it["disclosure_count"],
        "disclosure_empty_text": None if it["disclosures"] else tx.t("item_etf_no_disclosure" if is_etf else "item_no_disclosure", L, n=RECENT_DAYS),
        "flags": it["flags"], "notice": it["notice"],
    }


def _holdings(f: Facts, L: str, b: dict, risk: dict) -> dict:
    base = f.brief.get("data_date")
    items = []
    big_loss = {fl["ticker"]: fl for fl in risk.get("_flags_raw", []) if fl["code"] == "BIG_LOSS"}
    for it in b.get("holdings") or []:
        x = _item_common(it, f, L, base)
        x.update(quantity=it["quantity"], avg_price=it["avg_price"], eval_amount=it["eval_amount"], weight_pct=it["weight_pct"],
                 profit_loss=it["profit_loss"], profit_loss_pct=it["profit_loss_pct"], day_profit_loss=it["day_profit_loss"])
        x["position_text"] = tx.t("item_position", L, qty=f"{it['quantity']:,}", eval=tx.won(it["eval_amount"], L),
                                  w=tx.pct(it["weight_pct"], L, signed=False), pl=tx.pct(it["profit_loss_pct"], L))
        factors = [fl["text"] for fl in it["flags"]]
        if it["ticker"] in big_loss:
            factors.append(risk["_flag_text"](big_loss[it["ticker"]]))
        if it["notice"]:
            factors.append(it["notice"])
        factors += x["notes"]
        x["risk_factors"] = factors
        items.append(x)
    if not items:
        msg = (b.get("portfolio") or {}).get("message") or tx.t("holdings_none", L)
        return {"title": tx.t("title_holdings", L), "status": SECTION_EMPTY, "items": [], "message": msg}
    status = SECTION_OK if all(i["latest_date"] for i in items) else SECTION_PARTIAL if any(i["latest_date"] for i in items) else SECTION_NO_DATA
    return {"title": tx.t("title_holdings", L), "status": status, "items": items, "message": (b.get("portfolio") or {}).get("message")}


def _since_research(f: Facts, ticker: str, L: str) -> dict | None:
    r = f.research.get(ticker)
    if not r:
        return None
    at = r["at"].astimezone(KST).strftime("%Y-%m-%d %H:%M")
    return {"last_research_at": r["at"].astimezone(KST).isoformat(), "news_after": r["news"], "disclosures_after": r["disclosures"],
            "text": tx.t("since_research", L, at=at, news=r["news"], disc=r["disclosures"])}


def _watchlist(f: Facts, L: str, b: dict) -> dict:
    base = f.brief.get("data_date")
    items = []
    for it in b.get("watchlist") or []:
        x = _item_common(it, f, L, base)
        x["since_research"] = _since_research(f, it["ticker"], L)
        x["changes"] = [fl["text"] for fl in it["flags"]]
        x["notable"] = bool(it["flags"])
        x["status_text"] = None if x["notable"] else tx.t("watch_no_update", L)
        items.append(x)
    # 주목할 변화가 있는 종목 먼저(flag 수 많은 순), 그다음 원래 순서(최근 등록 순)
    order = {id(x): i for i, x in enumerate(items)}
    items.sort(key=lambda x: (not x["notable"], -len(x["changes"]), order[id(x)]))
    dedup = tx.t("watch_dedup", L, n=f.watch_dedup) if f.watch_dedup else None
    if not items:
        msg = tx.t("watch_all_in_holdings", L) if f.watch_dedup else tx.t("watch_none", L)
        return {"title": tx.t("title_watchlist", L), "status": SECTION_EMPTY, "items": [], "notable_count": 0, "message": msg, "dedup_text": dedup}
    status = SECTION_OK if all(i["latest_date"] for i in items) else SECTION_PARTIAL if any(i["latest_date"] for i in items) else SECTION_NO_DATA
    return {"title": tx.t("title_watchlist", L), "status": status, "items": items,
            "notable_count": sum(1 for i in items if i["notable"]), "message": None, "dedup_text": dedup}


def _risk(f: Facts, L: str, fr: dict) -> dict:
    holdings = f.brief.get("holdings") or []
    if not holdings:
        return {"title": tx.t("title_risk", L), "status": SECTION_EMPTY, "metrics": None, "flags": [], "texts": [tx.t("risk_no_holdings", L)],
                "sector": None, "etf_exposure": None, "cautions": [], "_flags_raw": [], "_flag_text": lambda fl: fl["message"]}
    cash = (f.brief.get("portfolio") or {}).get("cash_balance") or 0.0
    items, metrics = compute_portfolio(cash, _risk_inputs(f))
    raw = compute_flags(items, metrics)

    def flag_text(fl):
        return fl["message"] if L == "ko-KR" else diagnosis_i18n.flag_message(fl, items, L)

    flags = [{"code": fl["code"], "text": flag_text(fl), "value": fl["value"], "threshold": fl["threshold"],
              "ticker": fl.get("ticker"), "company_name": fl.get("company_name")} for fl in raw]
    texts = [tx.t("risk_metrics", L, top1=tx.pct(metrics["top1_weight_pct"], L, False), top3=tx.pct(metrics["top3_weight_pct"], L, False),
                  cash=tx.pct(metrics["cash_weight_pct"], L, False), n=metrics["holding_count"])]
    unpriced = [i["company_name"] for i in items if i["price_unavailable"]]
    if unpriced:
        texts.append(tx.t("risk_unpriced", L, names=tx.join(unpriced, L)))
    if not flags:
        texts.append(tx.t("risk_no_flags", L))
    etf = _etf_exposure(f, L)
    cautions = []
    if fr["status"] in (STATUS_DELAYED, STATUS_STALE):
        cautions.append(tx.t("caution_stale", L, lag=fr["lag_business_days"], base=fr["data_as_of"]))
    for h in _brief_in(f, L).get("holdings") or []:
        cautions += [fl["text"] for fl in h["flags"] if fl["type"] in ("day_move", "week_move")]
    status = SECTION_PARTIAL if unpriced else SECTION_OK
    if metrics["priced_holding_count"] == 0:
        status = SECTION_NO_DATA
    return {"title": tx.t("title_risk", L), "status": status,
            "metrics": {k: metrics[k] for k in ("total_asset", "cash_weight_pct", "holding_count", "priced_holding_count", "top1_weight_pct",
                                                "top3_weight_pct", "herfindahl_index", "market_weights_pct")},
            "flags": flags, "texts": texts,
            "sector": {"status": "unavailable", "text": tx.t("risk_sector_unavailable", L)},
            "etf_exposure": etf, "cautions": cautions, "_flags_raw": raw, "_flag_text": flag_text}


def _etf_exposure(f: Facts, L: str) -> dict:
    """ETF 실질 노출: 구성종목으로 분해한 ETF가 있을 때만 computed. 계산하지 않은 ETF는 이유와 함께 not_computed로 밝힌다."""
    held_etf = [h for h in f.brief.get("holdings") or [] if h["asset_type"] == "etf"]
    if not held_etf:
        return {"status": "no_etf", "text": tx.t("risk_etf_none", L), "flags": [], "top_companies": [], "undecomposed": []}
    ex = f.exposure or {}
    if L != "ko-KR" and ex:
        ex = exposure_i18n.localize(ex, L)
    decomposed = ex.get("decomposed_etfs") or []
    und = [{"ticker": u["ticker"], "name": u["name"], "reason_code": u["reason_code"]} for u in ex.get("undecomposed_etfs") or []]
    out_text = []
    if decomposed:
        dates = sorted({d["holdings_as_of"] for d in decomposed if d.get("holdings_as_of")})
        out_text.append(tx.t("risk_etf_computed", L, dates=tx.join(dates, L) if dates else tx.t("na", L)))
    if und:
        out_text.append(tx.t("risk_etf_not_computed", L, names=tx.join([u["name"] for u in und], L)))
    if not decomposed and not und:  # 시세가 없어 노출 계산 자체가 안 된 경우
        und = [{"ticker": h["ticker"], "name": h["name"], "reason_code": "no_price"} for h in held_etf]
        out_text.append(tx.t("risk_etf_not_computed", L, names=tx.join([h["name"] for h in held_etf], L)))
    return {"status": "computed" if decomposed else "not_computed", "text": " ".join(out_text),
            "flags": [{"kind": x["kind"], "text": x["message"], "value_pct": x["value_pct"], "threshold_pct": x["threshold_pct"]}
                      for x in ex.get("flags") or []] if decomposed else [],
            "top_companies": [{"name": c["name"], "code": c.get("code"), "weight_pct": c.get("effective_weight_pct", c.get("weight_pct"))}
                              for c in (ex.get("companies") or [])[:5]] if decomposed else [],
            "undecomposed": und}


def _checklist(f: Facts, L: str, fr: dict, risk: dict) -> dict:
    checks: list[dict] = []
    if fr["status"] in (STATUS_DELAYED, STATUS_STALE):
        checks.append({"kind": "data_delay", "ticker": None, "text": tx.t("check_delay", L, lag=fr["lag_business_days"], exp=fr["expected_date"])})
    elif fr["status"] == STATUS_NO_DATA and (f.brief.get("holdings") or f.brief.get("watchlist")):
        checks.append({"kind": "data_missing", "ticker": None, "text": tx.t("check_no_data", L)})

    def disclosures(scope_items, scope):
        out = []
        for it in scope_items:
            for d in it["disclosures"]:
                earn = bool(EARNINGS_RE.search(d["title"] or ""))
                out.append((0 if earn else 1, d["disclosed_at"], it, d, earn, scope))
        out.sort(key=lambda x: (x[0], -datetime.fromisoformat(x[1]).timestamp()))
        return out

    for _, _, it, d, earn, scope in disclosures(f.brief.get("holdings") or [], "holding"):
        checks.append({"kind": "earnings_disclosure" if earn else "holding_disclosure", "ticker": it["ticker"], "scope": scope,
                       "text": tx.t("check_earnings" if earn else "check_disclosure", L, name=it["name"], ticker=it["ticker"],
                                    title=d["title"], date=d["disclosed_at"][:10]), "url": d.get("source_url")})
    for fl in risk.get("_flags_raw", []):
        if fl["code"] in ("CONCENTRATION_HIGH", "BIG_LOSS", "LEVERAGED_INVERSE_ETF"):
            checks.append({"kind": f"risk_{fl['code'].lower()}", "ticker": fl.get("ticker"), "text": risk["_flag_text"](fl)})
    for _, _, it, d, earn, scope in disclosures(f.brief.get("watchlist") or [], "watchlist"):
        checks.append({"kind": "earnings_disclosure" if earn else "watchlist_disclosure", "ticker": it["ticker"], "scope": scope,
                       "text": tx.t("check_earnings" if earn else "check_disclosure", L, name=it["name"], ticker=it["ticker"],
                                    title=d["title"], date=d["disclosed_at"][:10]), "url": d.get("source_url")})
    # 같은 종목 공시가 여러 건이면 한 건만(가장 앞 = 실적 공시 또는 최신)
    seen, kept = set(), []
    for c in checks:
        key = (c["kind"].endswith("disclosure"), c["ticker"]) if c["ticker"] else (c["kind"], None)
        if key in seen:
            continue
        seen.add(key)
        kept.append(c)
    kept = kept[:MAX_CHECKS]
    return {"title": tx.t("title_checklist", L), "status": SECTION_OK if kept else SECTION_EMPTY, "items": kept,
            "message": None if kept else tx.t("check_none", L)}


def _sources(f: Facts) -> list[dict]:
    out, seen = [], set()

    def add(kind, ticker, title, url, at):
        key = (kind, url or title)
        if key in seen:
            return
        seen.add(key)
        out.append({"kind": kind, "ticker": ticker, "title": title, "url": url, "date": at})

    for scope in ("holdings", "watchlist"):
        for it in f.brief.get(scope) or []:
            for n in direct_news(it):
                add("news", it["ticker"], n["title"], n.get("url"), n.get("published_at"))
            for d in it["disclosures"]:
                add("disclosure", it["ticker"], d["title"], d.get("source_url"), d.get("disclosed_at"))
            sec, _, items = f.indirect.get(it["ticker"]) or (None, None, [])
            for n in group_news(items, NEWS_SHOWN):
                add("indirect_news", it["ticker"], n["title"], n.get("url"), n.get("published_at"))
    for n in group_news(f.market_news, MARKET_NEWS_SHOWN):
        add("market_news", None, n["title"], n.get("url"), n.get("published_at"))
    return out


def tickers(f: Facts) -> list[dict]:
    return ([{"ticker": h["ticker"], "name": h["name"], "scope": "holding"} for h in f.brief.get("holdings") or []]
            + [{"ticker": w["ticker"], "name": w["name"], "scope": "watchlist"} for w in f.brief.get("watchlist") or []])


def code_summary(f: Facts, L: str, notable: int, checks: int) -> str:
    h, w = len(f.brief.get("holdings") or []), len(f.brief.get("watchlist") or [])
    base = f.brief.get("data_date")
    if base is None:
        return tx.t("summary_code_none", L, h=h, w=w)
    return tx.t("summary_code", L, base=base, h=h, w=w, k=notable, c=checks)


def render(f: Facts, L: str, summary_text: str | None = None) -> dict:
    """같은 Facts를 locale 언어로. summary_text(검증 통과한 LLM 요약, 그 언어)가 없으면 코드 요약."""
    b = _brief_in(f, L)
    fr = freshness(f)
    head, extra = _fresh_notices(fr, L)
    risk = _risk(f, L, fr)
    sections = {"market": _market(f, L), "holdings": _holdings(f, L, b, risk), "watchlist": _watchlist(f, L, b)}
    sections["risk"] = {k: v for k, v in risk.items() if not k.startswith("_")}
    sections["checklist"] = _checklist(f, L, fr, risk)
    notable = len(b.get("highlights") or [])
    code = code_summary(f, L, notable, len(sections["checklist"]["items"]))
    local_date = f.now.astimezone(_tz(f.timezone)).date().isoformat()
    return {
        "title": tx.t("title_briefing", L, date=local_date),
        "locale": L,
        "local_date": local_date,
        "timezone": f.timezone,
        "generated_at": f.now.astimezone(KST).isoformat(),
        "data_as_of": fr["data_as_of"],
        "market_data_status": fr["status"],
        "freshness": {**fr, "headline": head, "notices": extra,
                      "warning": fr["status"] != STATUS_LATEST},
        "summary": {"text": summary_text or code, "source": "llm" if summary_text else "code",
                    "note": tx.t("summary_llm_note" if summary_text else "summary_code_note", L)},
        "section_order": list(SECTIONS),  # 화면 순서(JSONB 저장본은 키 순서를 보존하지 않는다)
        "sections": sections,
        "section_status": {k: sections[k]["status"] for k in SECTIONS},
        "highlights": b.get("highlights") or [],
        "not_advice": tx.t("not_advice", L),
        "disclaimer": disclaimer(L),
    }


def _tz(name: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


def llm_payload(f: Facts, locale: str = "ko-KR") -> dict:
    """LLM 요약 입력: 그 언어로 그린 브리핑의 사실만(숫자는 언어와 관계없이 같다). 사용자 식별 정보는 넣지 않는다.
    숫자 허용 목록도 이 값에서 만든다."""
    ko = render(f, locale)
    s = ko["sections"]
    return {
        "data_as_of": ko["data_as_of"],
        "market_data_status": ko["market_data_status"],
        "freshness": ko["freshness"]["headline"],
        "market": [i["text"] for i in s["market"]["indices"]],
        "holdings": [{"name": i["name"], "ticker": i["ticker"], "price": i["price_text"], "position": i["position_text"],
                      "risk_factors": i["risk_factors"]} for i in s["holdings"]["items"]],
        "watchlist": [{"name": i["name"], "ticker": i["ticker"], "changes": i["changes"] or [i["status_text"]]} for i in s["watchlist"]["items"]],
        "risk": s["risk"]["texts"] + [fl["text"] for fl in s["risk"]["flags"]],
        "checklist": [c["text"] for c in s["checklist"]["items"]],
    }
