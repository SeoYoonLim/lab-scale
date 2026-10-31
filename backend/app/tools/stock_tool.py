from datetime import date

from app.db.session import SessionLocal
from app.price_history import last_row_before, price_rows_between, recent_price_rows
from app.tools.company_resolver import not_found_response, resolve_company, to_int

# 조회할 수 있는 최대 거래일 수(약 10년). 보유 시세는 그보다 짧으니 사실상 전 기간이다.
MAX_PERIOD_DAYS = 2600
# LLM에 넘기는 일별 시세 행 수. 긴 기간은 요약(summary)이 전체 기간을 말하고 행만 최근 것으로 줄인다(프롬프트 크기 때문).
MAX_PRICE_ROWS = 60


def _to_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _price_row(row) -> dict:
    return {
        "price_date": row.price_date.isoformat(),
        "close_price": float(row.close_price),
        "volume": row.volume,
        "change_pct": float(row.change_pct) if row.change_pct is not None else None,
    }


def summarize_period(company_name: str, prices: list[dict], base: dict | None = None) -> dict:
    """여러 거래일 주가(prices, 날짜 오름차순)의 기간 등락률 요약. 2건 미만이면 빈 dict.

    최상위 change_pct는 '가장 최근 하루'의 등락률이라, 이 요약이 없으면 모델이 그 값을 기간 전체의 등락률로
    잘못 옮긴다(실측: 30거래일 조회 결과에서 하루치 -1.69%를 "지난 한 달 1.69% 하락"이라고 답함).
    base는 구간 직전 거래일의 {price_date, close_price}다. 있으면 그 종가 대비 len(prices)거래일간의 등락률을,
    없으면(저장된 주가가 모자람) prices의 첫 종가 대비 len(prices)-1거래일간의 등락률을 계산한다.
    market_tool/진단의 20거래일 수익률과 같은 정의(구간 직전 종가 대비)다."""
    if len(prices) < 2:
        return {}
    start, trading_days = (base, len(prices)) if base else (prices[0], len(prices) - 1)
    if not start["close_price"]:
        return {}
    end = prices[-1]
    change = round((end["close_price"] / start["close_price"] - 1) * 100, 2)
    high = max(prices, key=lambda p: p["close_price"])
    low = min(prices, key=lambda p: p["close_price"])

    summary = (
        f"{company_name}의 {end['price_date']} 종가는 {end['close_price']:,.0f}원으로, {start['price_date']} 종가 "
        f"{start['close_price']:,.0f}원 대비 {trading_days}거래일간 {change:+.2f}%입니다. "
        f"이 기간 종가 최고는 {high['close_price']:,.0f}원({high['price_date']}), "
        f"최저는 {low['close_price']:,.0f}원({low['price_date']})입니다."
    )
    # 최상위 change_pct/volume이 '마지막 하루' 값이라는 것도 문장으로 못박는다(기간 값으로 옮기는 실수 방지).
    last_day = []
    if end["change_pct"] is not None:
        last_day.append(f"등락률은 {end['change_pct']:+.2f}%")
    if end.get("volume") is not None:
        last_day.append(f"거래량은 {end['volume']:,}주")
    if last_day:
        summary += f" 마지막 거래일({end['price_date']}) 하루 {', '.join(last_day)}입니다."
    return {
        "period_start_date": start["price_date"],
        "period_end_date": end["price_date"],
        "trading_days": trading_days,
        "period_change_pct": change,
        "summary": summary,
    }


def stock_tool(ticker: str, period_days: int = 1, start_date=None, end_date=None) -> dict:
    """지정한 종목(이름 또는 티커)의 최근 N일 주가(등락률/거래량 등)를 DB에서 조회한다.

    start_date·end_date(ISO 날짜)는 LLM 스키마에 없고 코드(app.agent._apply_past_window)만 넘긴다. 둘 다 있으면 최근 N일 대신 그 구간을
    조회하고, 구간에 시세가 없으면 다른 구간으로 답하지 않고 found=False다. 요청 기간이 MAX_PRICE_ROWS보다 길어도 자르지 않는다:
    기간 요약은 전체 기간으로 계산하고, 응답의 prices만 최근 MAX_PRICE_ROWS개로 줄인 뒤 그 사실을 prices_note로 밝힌다."""
    period_days = to_int(period_days, default=1, hi=MAX_PERIOD_DAYS)
    start, end = _to_date(start_date), _to_date(end_date)
    db = SessionLocal()
    try:
        res = resolve_company(db, ticker)
        if res.company is None:
            return not_found_response({"ticker": ticker, "period_days": period_days}, res)
        company = res.company
        corrected = {"corrected_from": res.corrected_from} if res.corrected_from else {}

        if start is not None and end is not None:
            rows = list(reversed(price_rows_between(db, company.id, start, end)))
            if not rows:
                return {
                    "ticker": company.ticker,
                    "company_name": company.name,
                    "requested_start": start.isoformat(),
                    "requested_end": end.isoformat(),
                    "found": False,
                    "message": f"'{company.name}'의 {start.isoformat()}~{end.isoformat()} 시세 데이터가 없습니다.",
                    **corrected,
                }
            before = last_row_before(db, company.id, start)
            period_days = len(rows)
            rows = rows + ([before] if before is not None else [])
        else:
            # 기간 등락률의 기준(구간 직전 거래일 종가)으로 쓰려고 한 건 더 가져온다. 응답의 prices에는 넣지 않는다.
            rows = recent_price_rows(db, company.id, period_days + 1)

        if not rows:
            return {
                "ticker": company.ticker,
                "company_name": company.name,
                "period_days": period_days,
                "found": False,
                "message": f"'{company.name}'는 등록된 종목이지만 아직 수집된 주가 데이터가 없습니다.",
                **corrected,
            }

        base_row = rows[period_days] if len(rows) > period_days else None
        # 최근 날짜가 먼저 오도록 조회했으니, 응답은 날짜 오름차순으로 보기 좋게 정렬
        rows = list(reversed(rows[:period_days]))

        prices = [_price_row(row) for row in rows]

        latest = prices[-1]
        base = (
            {"price_date": base_row.price_date.isoformat(), "close_price": float(base_row.close_price)}
            if base_row is not None
            else None
        )

        limited = {}
        if len(prices) < period_days and start is None:
            limited["data_note"] = f"보유 시세가 {prices[0]['price_date']}부터라 요청한 {period_days}거래일 중 {len(prices)}거래일만 있습니다."
        if len(prices) > MAX_PRICE_ROWS:
            limited["prices_note"] = (f"prices는 전체 {len(prices)}거래일 중 최근 {MAX_PRICE_ROWS}거래일만 담았습니다. "
                                      "기간 등락률과 최고·최저는 summary의 전체 기간 값을 쓰세요.")
        return {
            "ticker": company.ticker,
            "company_name": company.name,
            "period_days": period_days,
            "found": True,
            "change_pct": latest["change_pct"],
            "volume": latest["volume"],
            **summarize_period(company.name, prices, base),
            "prices": prices[-MAX_PRICE_ROWS:],
            **limited,
            **corrected,
        }
    finally:
        db.close()
