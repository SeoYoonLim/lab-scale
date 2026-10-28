"""브리핑 응답의 숫자를 DB에서 독립적으로 다시 계산해 대조한다(tests/test_briefing_devdb.py와 검증 스크립트가 같이 쓴다).

app/briefing.py의 함수를 쓰지 않는다. 원시 SQL로 읽고 Decimal로 정확히 계산한 값과, 응답에 소수 둘째 자리로 반올림돼 담긴 값의
차이가 반올림 오차(0.005) 이내인지 본다. 임계값도 여기서 따로 적어서(문서의 값) 코드 상수가 바뀌면 이 대조가 깨지게 한다.
"""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text

TOL = Decimal("0.005") + Decimal("1e-9")
DAY, WEEK, VOL, CONC = Decimal("3.0"), Decimal("7.0"), Decimal("2.0"), Decimal("40.0")
INITIAL = Decimal(10_000_000)


def _r2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pct(new, old):
    return (Decimal(new) / Decimal(old) - 1) * 100


class Recheck:
    def __init__(self):
        self.checked = 0
        self.problems: list[str] = []

    def num(self, label, actual, expected, tol=TOL):
        """actual(응답 값)이 expected(독립 계산 값)와 반올림 오차 이내이면 통과. 둘 다 None이어도 통과."""
        self.checked += 1
        if expected is None or actual is None:
            if not (expected is None and actual is None):
                self.problems.append(f"{label}: 응답 {actual!r} != 독립 계산 {expected!r}")
            return
        if abs(Decimal(str(actual)) - Decimal(expected)) > tol:
            self.problems.append(f"{label}: 응답 {actual!r} != 독립 계산 {Decimal(expected):.6f}")

    def eq(self, label, actual, expected):
        self.checked += 1
        if actual != expected:
            self.problems.append(f"{label}: 응답 {actual!r} != 독립 계산 {expected!r}")


def _open_session(now: datetime) -> bool:
    """독립 재작성: 평일 15:30(KST) 전이면 오늘 날짜의 행은 장중 값이라 응답에 쓰이지 않아야 한다."""
    return now.weekday() < 5 and (now.hour, now.minute) < (15, 30)


def _prices(db, ticker, now):
    rows = db.execute(
        text(
            "SELECT p.price_date, p.close_price, p.volume FROM stock_price p JOIN company c ON c.id = p.company_id "
            "WHERE c.ticker = :t ORDER BY p.price_date DESC LIMIT 25"
        ),
        {"t": ticker},
    ).all()
    return [r for r in rows if not (_open_session(now) and r.price_date == now.date())]


def recheck_item(db, rc: Recheck, item: dict, now: datetime, news_limit: int):
    t = item["ticker"]
    rows = _prices(db, t, now)
    rows = [r for r in rows if r.close_price is not None and r.close_price > 0]
    p = f"[{t}]"
    if not rows:
        rc.eq(f"{p} data_available", item["data_available"], False)
        return None
    rc.eq(f"{p} data_available", item["data_available"], True)
    c0 = rows[0].close_price
    rc.num(f"{p} latest_close", item["latest_close"], c0, tol=Decimal("0.0001"))
    rc.eq(f"{p} latest_date", item["latest_date"], rows[0].price_date.isoformat())
    day = _pct(c0, rows[1].close_price) if len(rows) > 1 else None
    week = _pct(c0, rows[5].close_price) if len(rows) > 5 else None
    rc.num(f"{p} day_change_pct", item["day_change_pct"], day)
    rc.num(f"{p} week_change_pct", item["week_change_pct"], week)

    ratio = avg = None
    prior = rows[1:21]
    if len(prior) == 20 and all(r.volume is not None for r in prior):
        avg = Decimal(sum(r.volume for r in prior)) / 20
        if avg > 0 and rows[0].volume is not None:
            ratio = Decimal(rows[0].volume) / avg
    rc.eq(f"{p} volume", item["volume"], rows[0].volume)
    rc.num(f"{p} avg_volume_20d", item["avg_volume_20d"], avg, tol=Decimal("0.05") + Decimal("1e-9"))
    rc.num(f"{p} volume_ratio", item["volume_ratio"], ratio)

    # 뉴스: 모두 이 종목의 최근 7일 기사이고, 최신순, 제목 중복 없음, 개수는 limit 이하
    cutoff = now - timedelta(days=7)
    db_titles = {
        r.title
        for r in db.execute(
            text(
                "SELECT n.title FROM news n JOIN company c ON c.id = n.company_id "
                "WHERE c.ticker = :t AND n.published_at >= :cut"
            ),
            {"t": t, "cut": cutoff},
        )
    }
    news = item["news"]
    rc.eq(f"{p} news 개수 <= limit", len(news) <= news_limit, True)
    rc.eq(f"{p} news 제목이 DB 최근 7일 기사", all(n["title"] in db_titles for n in news), True)
    stamps = [n["published_at"] for n in news]
    rc.eq(f"{p} news 최신순", stamps == sorted(stamps, reverse=True), True)
    rc.eq(f"{p} news 제목 중복 없음", len({n["title"] for n in news}) == len(news), True)

    # 공시: 건수 = DB의 최근 7일 공시 수, 표시 항목은 최대 3건
    n_disc = db.execute(
        text(
            "SELECT count(*) FROM disclosure d JOIN company c ON c.id = d.company_id "
            "WHERE c.ticker = :t AND d.disclosed_at >= :cut"
        ),
        {"t": t, "cut": cutoff},
    ).scalar()
    rc.eq(f"{p} disclosure_count", item["disclosure_count"], n_disc)
    rc.eq(f"{p} disclosures 표시 <= 3", len(item["disclosures"]) == min(3, n_disc), True)

    return {"day": day, "week": week, "ratio": ratio, "disc": n_disc, "close": c0, "prev": rows[1].close_price if len(rows) > 1 else None,
            "date": rows[0].price_date}


def _expected_flag_types(m: dict, weight: Decimal | None) -> set[str]:
    out = set()
    if weight is not None and _r2(weight) >= CONC:
        out.add("concentration")
    if m["day"] is not None and abs(_r2(m["day"])) >= DAY:
        out.add("day_move")
    if m["week"] is not None and abs(_r2(m["week"])) >= WEEK:
        out.add("week_move")
    if m["ratio"] is not None and _r2(m["ratio"]) >= VOL:
        out.add("volume_spike")
    if m["disc"] >= 1:
        out.add("new_disclosure")
    return out


def recheck_briefing(db, owner_key: str, resp: dict, news_limit: int = 3) -> Recheck:
    """응답 전체를 DB 값으로 다시 계산해 대조한다. rc.problems가 비어 있으면 일치."""
    rc = Recheck()
    now = datetime.fromisoformat(resp["generated_at"])

    metrics = {}
    for item in resp["holdings"] + resp["watchlist"]:
        metrics[item["ticker"]] = recheck_item(db, rc, item, now, news_limit)

    # 포트폴리오
    port = resp["portfolio"]
    acct = db.execute(text("SELECT cash_balance FROM virtual_account WHERE device_id = :o"), {"o": owner_key}).first()
    rc.eq("portfolio.has_account", port["has_account"], acct is not None)
    weights = {}
    if acct is not None:
        cash = acct.cash_balance
        held = db.execute(
            text(
                "SELECT c.ticker, h.quantity, h.avg_price FROM holding h JOIN company c ON c.id = h.company_id "
                "WHERE h.device_id = :o"
            ),
            {"o": owner_key},
        ).all()
        rc.eq("holdings 종목 집합", {h["ticker"] for h in resp["holdings"]}, {h.ticker for h in held})
        value = Decimal(0)
        day_total, day_any = Decimal(0), False
        for h in held:
            m = metrics.get(h.ticker)
            if m:
                value += m["close"] * h.quantity
                if m["prev"] is not None:
                    day_total += (m["close"] - m["prev"]) * h.quantity
                    day_any = True
        total = cash + value
        rc.num("portfolio.cash_balance", port["cash_balance"], cash)
        rc.num("portfolio.holdings_value", port["holdings_value"], value)
        rc.num("portfolio.total_asset", port["total_asset"], total)
        rc.num("portfolio.profit_loss", port["profit_loss"], total - INITIAL)
        rc.num("portfolio.profit_loss_pct", port["profit_loss_pct"], _pct(total, INITIAL))
        rc.num("portfolio.day_profit_loss", port["day_profit_loss"], day_total if day_any else None)
        by_ticker = {h["ticker"]: h for h in resp["holdings"]}
        for h in held:
            item = by_ticker[h.ticker]
            m = metrics.get(h.ticker)
            rc.eq(f"[{h.ticker}] quantity", item["quantity"], h.quantity)
            rc.num(f"[{h.ticker}] avg_price", item["avg_price"], h.avg_price, tol=Decimal("0.0001"))
            if m:
                ev = m["close"] * h.quantity
                w = ev / total * 100 if total > 0 else None
                weights[h.ticker] = w
                rc.num(f"[{h.ticker}] eval_amount", item["eval_amount"], ev)
                rc.num(f"[{h.ticker}] weight_pct", item["weight_pct"], w)
                rc.num(f"[{h.ticker}] profit_loss", item["profit_loss"], (m["close"] - h.avg_price) * h.quantity)
                rc.num(f"[{h.ticker}] profit_loss_pct", item["profit_loss_pct"], _pct(m["close"], h.avg_price))
                rc.num(
                    f"[{h.ticker}] day_profit_loss", item["day_profit_loss"],
                    (m["close"] - m["prev"]) * h.quantity if m["prev"] is not None else None,
                )
        order = [x["ticker"] for x in port["weights"]]
        rc.eq("portfolio.weights 비중 큰 순", [weights[t] for t in order] == sorted(weights.values(), reverse=True), True)
        if port["top_weight"]:
            rc.eq("portfolio.top_weight", port["top_weight"]["ticker"], max(weights, key=weights.get) if weights else None)
    else:
        rc.eq("계좌 없음 -> 보유종목 없음", resp["holdings"], [])

    # flag 종류: 독립 계산한 수치와 문서의 임계값으로 기대 집합을 만들어 비교
    for item in resp["holdings"] + resp["watchlist"]:
        m = metrics.get(item["ticker"])
        if m is None:
            rc.eq(f"[{item['ticker']}] 시세 없는 종목의 flags", item["flags"], [])
            continue
        expected = _expected_flag_types(m, weights.get(item["ticker"]))
        rc.eq(f"[{item['ticker']}] flag 종류", {f["type"] for f in item["flags"]}, expected)
        for f in item["flags"]:
            rc.eq(f"[{item['ticker']}] flag.scope", f["scope"], "holding" if item in resp["holdings"] else "watchlist")

    # 시장
    for key, table, col, code in (("kospi", "market_index", "index_code", "KS11"), ("kosdaq", "market_index", "index_code", "KQ11"),
                                  ("usd_krw", "exchange_rate", "pair_code", "USD/KRW")):
        rows = db.execute(
            text(f"SELECT price_date, close_price FROM {table} WHERE {col} = :c ORDER BY price_date DESC LIMIT 3"), {"c": code}
        ).all()
        rows = [r for r in rows if not (_open_session(now) and r.price_date == now.date())][:2]
        q = resp["market"][key]
        if not rows:
            rc.eq(f"market.{key}", q, None)
            continue
        rc.eq(f"market.{key}.date", q["date"], rows[0].price_date.isoformat())
        rc.num(f"market.{key}.close", q["close"], rows[0].close_price, tol=Decimal("0.0001"))
        rc.num(f"market.{key}.change_pct", q["change_pct"], _pct(rows[0].close_price, rows[1].close_price) if len(rows) > 1 else None)

    # 기준일, highlights
    dates = [m["date"] for m in metrics.values() if m]
    rc.eq("data_date", resp["data_date"], max(dates).isoformat() if dates else resp["data_date"])
    rc.eq("highlights <= 5", len(resp["highlights"]) <= 5, True)
    all_flags = [f for it in resp["holdings"] + resp["watchlist"] for f in it["flags"]]
    rc.eq("highlights는 항목 flag의 부분집합", all(f in all_flags for f in resp["highlights"]), True)
    if all_flags:
        rc.eq("highlights 개수", len(resp["highlights"]), min(5, len(all_flags)))
    return rc
