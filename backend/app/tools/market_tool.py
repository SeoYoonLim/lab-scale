from datetime import date

from sqlalchemy import func

from app.db.session import SessionLocal
from app.korean import josa
from app.models import MarketIndex, StockPrice
from app.models.market_index import MARKET_INDEXES
from app.tools.company_resolver import not_found_response, resolve_company, to_int
from app.tools.stock_tool import MAX_PERIOD_DAYS, _to_date

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


def is_index_current(latest_stock_date: date | None, latest_index_date: date | None) -> bool:
    """지수가 종목의 가장 최근 주가 날짜까지 수집돼 있으면 True(둘 중 하나라도 없으면 False)."""
    return latest_stock_date is not None and latest_index_date is not None and latest_index_date >= latest_stock_date


def market_data_is_current(ticker: str) -> bool:
    """이 종목을 지금 시장 지수와 비교해도 되는지: 상장 시장의 지수가 있고, 그 지수가 종목의 최신 주가 날짜까지
    수집돼 있으면 True.

    market_tool 자체는 지수가 뒤처져 있어도 지수의 마지막 날짜까지만 비교하고 note로 알려준다. 이 함수는 사용자가
    시장 비교를 요청하지 않았는데 코드가 market_tool을 덧붙여도 되는지 판단하는 용도라서(app/agent.py의
    _add_requested_tools) 더 엄격하다: 지수가 하루라도 뒤처져 있으면 "최근 하락"을 옛 구간의 시장 대비로 설명하게
    되므로 False다. 등록되지 않은 종목, 코스피/코스닥이 아닌 종목, 주가나 지수가 없는 경우도 False다."""
    db = SessionLocal()
    try:
        company = resolve_company(db, ticker).company
        if company is None:
            return False
        index_code = index_code_for_market(company.market)
        if index_code is None:
            return False
        latest_stock = db.query(func.max(StockPrice.price_date)).filter(StockPrice.company_id == company.id).scalar()
        latest_index = db.query(func.max(MarketIndex.price_date)).filter(MarketIndex.index_code == index_code).scalar()
        return is_index_current(latest_stock, latest_index)
    finally:
        db.close()


def compare_range(
    stock_closes: list[tuple[date, float]], index_closes: list[tuple[date, float]], start: date, end: date
) -> dict | None:
    """종목과 지수의 start~end 등락률을 같은 날짜 구간으로 비교한다. 기준은 start 직전의 공통 거래일 종가다(없으면 구간의 첫 공통 거래일).
    겹치는 날짜가 2개 미만이면 None."""
    index_by_date = dict(index_closes)
    common = [(d, c) for d, c in stock_closes if d in index_by_date and d <= end]
    inside = [(d, c) for d, c in common if d >= start]
    before = [(d, c) for d, c in common if d < start]
    if not inside:
        return None
    window = before[-1:] + inside
    return compare_returns([(d, c) for d, c in window], [(d, index_by_date[d]) for d, _ in window], max(len(window) - 1, 1))


def _range_note(index_name: str, cmp: dict, start: date, end: date, first_common: date | None, last_common: date | None) -> str | None:
    """지수·종목 데이터가 요청 구간을 다 덮지 못하면 실제로 비교한 범위를 밝힌다."""
    notes = []
    if first_common is not None and first_common > start:
        notes.append(f"요청 구간의 시작({start.isoformat()})보다 뒤인 {first_common.isoformat()}부터 데이터가 있습니다")
    if last_common is not None and last_common < end:
        notes.append(f"{last_common.isoformat()}까지만 데이터가 있습니다")
    if not notes:
        return None
    return (f"요청 구간 {start.isoformat()}~{end.isoformat()} 중 {' / '.join(notes)}. "
            f"{cmp['start_date']}~{cmp['end_date']} 구간만 {index_name}와 비교했습니다.")


def market_tool(ticker: str, period_days: int = 5, start_date=None, end_date=None) -> dict:
    """종목의 최근 N거래일 등락률을 그 종목이 상장된 시장(코스피/코스닥) 지수의 같은 기간 등락률과 비교한다.

    start_date·end_date(ISO 날짜)는 LLM 스키마에 없고 코드(app.agent._apply_past_window)만 넘긴다. 둘 다 있으면 최근 N거래일 대신 그 구간을
    비교한다(stock_tool과 같은 구간). 기간을 60거래일로 자르지 않는다(2026-10-31: "2024년 코스피 대비"가 최근 구간과 비교되던 문제)."""
    period_days = to_int(period_days, default=5, hi=MAX_PERIOD_DAYS)
    start, end = _to_date(start_date), _to_date(end_date)
    db = SessionLocal()
    try:
        res = resolve_company(db, ticker)
        if res.company is None:
            return not_found_response({"ticker": ticker, "period_days": period_days}, res)
        company = res.company
        base = {"ticker": company.ticker, "company_name": company.name, "period_days": period_days}
        if res.corrected_from:
            base["corrected_from"] = res.corrected_from

        if company.asset_type == "etf":
            return {
                **base,
                "found": False,
                "asset_type": "etf",
                "message": f"{josa(company.name, '은/는')} ETF(상장지수펀드)라서 코스피/코스닥 같은 상장 시장 구분이 없어 "
                "시장 지수와 비교하지 않습니다. 가격 흐름은 주가 조회 결과로 확인할 수 있습니다.",
            }
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

        if start is not None and end is not None:
            return _market_range_result(db, company, base, index_code, index_name, start, end)

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


def _market_range_result(db, company, base: dict, index_code: str, index_name: str, start: date, end: date) -> dict:
    """start~end 구간의 종목·지수 비교. 데이터가 구간을 다 덮지 못하면 덮는 범위만 비교하고 note로 밝힌다. 겹치는 날이 없으면 found=False."""
    requested = {"requested_start": start.isoformat(), "requested_end": end.isoformat()}
    stock_rows = (
        db.query(StockPrice.price_date, StockPrice.close_price)
        .filter(StockPrice.company_id == company.id, StockPrice.price_date <= end)
        .order_by(StockPrice.price_date)
        .all()
    )
    index_rows = (
        db.query(MarketIndex.price_date, MarketIndex.close_price)
        .filter(MarketIndex.index_code == index_code, MarketIndex.price_date <= end)
        .order_by(MarketIndex.price_date)
        .all()
    )
    stock_closes = [(d, float(c)) for d, c in stock_rows]
    index_closes = [(d, float(c)) for d, c in index_rows]
    cmp = compare_range(stock_closes, index_closes, start, end)
    if cmp is None:
        return {**base, **requested, "found": False,
                "message": f"요청 구간 {start.isoformat()}~{end.isoformat()}에 '{company.name}'와 {index_name}를 비교할 데이터가 없습니다."}
    index_dates = {d for d, _ in index_closes}
    inside = [d for d, _ in stock_closes if d in index_dates and start <= d <= end]
    out = {**base, **requested, **cmp, "found": True, "summary": _summary(company.name, index_name, cmp)}
    note = _range_note(index_name, cmp, start, end, inside[0], inside[-1])
    if note:
        out["note"] = note
    return out
