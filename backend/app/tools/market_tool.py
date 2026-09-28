from datetime import date

from sqlalchemy import func

from app.db.session import SessionLocal
from app.models import MarketIndex, StockPrice
from app.models.market_index import MARKET_INDEXES
from app.tools.company_resolver import not_found_response, resolve_company, to_int

# 종목-시장 등락률 차이가 이 값(%p) 미만이면 "비슷한 흐름"으로 본다.
IN_LINE_PP = 0.5


def index_code_for_market(market: str | None) -> str | None:
    """company.market 값을 지수 코드로 바꾼다. KOSDAQ GLOBAL도 코스닥 지수(KQ11)를 쓴다."""
    m = (market or "").upper()
    if m.startswith("KOSPI"):
        return "KS11"
    if m.startswith("KOSDAQ"):
        return "KQ11"
    return None


def compare_returns(
    stock_closes: list[tuple[date, float]], index_closes: list[tuple[date, float]], period_days: int
) -> dict | None:
    """종목과 지수의 최근 period_days 거래일 누적 등락률(%)을 같은 날짜 구간으로 비교한다.

    두 쪽 모두 종가가 있는 날짜만 쓴다(날짜 오름차순 입력). 시작 종가는 구간 직전 거래일 종가라서
    period_days=1이면 하루 등락률과 같다. 겹치는 날짜가 2개 미만이면 None을 돌려준다.
    실제로 쓴 거래일 수가 요청보다 적을 수 있어 period_days는 실제 값을 담는다."""
    index_by_date = dict(index_closes)
    common = [(d, c, index_by_date[d]) for d, c in stock_closes if d in index_by_date]
    window = common[-(period_days + 1):]
    if len(window) < 2:
        return None

    (start, s0, i0), (end, s1, i1) = window[0], window[-1]
    stock_pct = round((s1 / s0 - 1) * 100, 2)
    market_pct = round((i1 / i0 - 1) * 100, 2)
    diff = round(stock_pct - market_pct, 2)
    if abs(diff) < IN_LINE_PP:
        relative = "in_line"
    else:
        relative = "outperform" if diff > 0 else "underperform"
    return {
        "period_days": len(window) - 1,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "stock_change_pct": stock_pct,
        "market_change_pct": market_pct,
        "diff_pct_point": diff,
        "relative": relative,
    }


def _summary(company_name: str, index_name: str, cmp: dict) -> str:
    stock, market, diff = cmp["stock_change_pct"], cmp["market_change_pct"], cmp["diff_pct_point"]
    head = f"{cmp['start_date']} 종가 대비 {cmp['end_date']}까지 {cmp['period_days']}거래일간 {company_name} {stock:+.2f}%, {index_name} {market:+.2f}%."
    if cmp["relative"] == "in_line":
        return head + f" 시장과 비슷한 흐름입니다(차이 {diff:+.2f}%p)."
    # 둘 다 하락한 경우에도 뜻이 맞도록 '올랐다/내렸다'가 아니라 시장 대비 우열로 표현한다.
    word = "더 좋은" if cmp["relative"] == "outperform" else "더 부진한"
    return head + f" 시장 대비 {abs(diff):.2f}%p {word} 흐름입니다."


def market_tool(ticker: str, period_days: int = 5) -> dict:
    """종목의 최근 N거래일 등락률을 그 종목이 상장된 시장(코스피/코스닥) 지수의 같은 기간 등락률과 비교한다."""
    period_days = to_int(period_days, default=5, hi=60)
    db = SessionLocal()
    try:
        res = resolve_company(db, ticker)
        if res.company is None:
            return not_found_response({"ticker": ticker, "period_days": period_days}, res)
        company = res.company
        base = {"ticker": company.ticker, "company_name": company.name, "period_days": period_days}
        if res.corrected_from:
            base["corrected_from"] = res.corrected_from

        index_code = index_code_for_market(company.market)
        if index_code is None:
            return {**base, "found": False, "message": f"'{company.name}'의 상장 시장 정보가 없어 시장 지수와 비교할 수 없습니다."}
        index_name = MARKET_INDEXES[index_code]
        base.update(market=company.market, index_code=index_code, index_name=index_name)

        # 종목 주가가 지수보다 최근까지 있을 수 있어(수집 시점 차이) 지수의 마지막 날짜까지만 본다.
        last_index_date = (
            db.query(MarketIndex.price_date)
            .filter(MarketIndex.index_code == index_code)
            .order_by(MarketIndex.price_date.desc())
            .limit(1)
            .scalar()
        )
        if last_index_date is None:
            return {**base, "found": False, "message": f"{index_name} 지수 데이터가 아직 수집되지 않았습니다."}

        # 겹치지 않는 날짜가 섞일 수 있어 여유를 두고 가져온다.
        stock_rows = (
            db.query(StockPrice.price_date, StockPrice.close_price)
            .filter(StockPrice.company_id == company.id, StockPrice.price_date <= last_index_date)
            .order_by(StockPrice.price_date.desc())
            .limit(period_days + 1 + 5)
            .all()
        )
        if len(stock_rows) < 2:
            return {**base, "found": False, "message": f"'{company.name}'의 비교에 필요한 주가 데이터가 부족합니다."}
        stock_closes = [(d, float(c)) for d, c in reversed(stock_rows)]

        index_rows = (
            db.query(MarketIndex.price_date, MarketIndex.close_price)
            .filter(
                MarketIndex.index_code == index_code,
                MarketIndex.price_date >= stock_closes[0][0],
                MarketIndex.price_date <= stock_closes[-1][0],
            )
            .order_by(MarketIndex.price_date)
            .all()
        )
        cmp = compare_returns(stock_closes, [(d, float(c)) for d, c in index_rows], period_days)
        if cmp is None:
            return {**base, "found": False, "message": f"'{company.name}'와 {index_name}의 주가가 겹치는 날짜가 부족해 비교할 수 없습니다."}

        out = {**base, **cmp, "found": True, "summary": _summary(company.name, index_name, cmp)}
        notes = []
        if cmp["period_days"] < period_days:
            notes.append(f"요청한 {period_days}거래일보다 적은 {cmp['period_days']}거래일만 비교했습니다(데이터 범위 제한).")
        latest_stock_date = db.query(func.max(StockPrice.price_date)).filter(StockPrice.company_id == company.id).scalar()
        if latest_stock_date is not None and latest_stock_date > date.fromisoformat(cmp["end_date"]):
            notes.append(f"{index_name} 지수 데이터가 {cmp['end_date']}까지만 있어 그 날짜까지 비교했습니다(종목 주가는 {latest_stock_date.isoformat()}까지 있음).")
        if notes:
            out["note"] = " ".join(notes)
        return out
    finally:
        db.close()
