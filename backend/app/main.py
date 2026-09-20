from datetime import datetime, timedelta

from pykrx import stock

from app.db.session import SessionLocal
from app.models import Company, StockPrice


def get_or_create_company(db, ticker: str, name: str) -> Company:
    company = db.query(Company).filter(Company.ticker == ticker).first()
    if company is None:
        company = Company(ticker=ticker, name=name)
        db.add(company)
        db.flush()
    return company


# pykrx로 주가 수집 후 DB 저장 함수
def fetch_and_save_stock(ticker: str, name: str, days: int = 7):
    today = datetime.now()
    start_date = (today - timedelta(days=days)).strftime("%Y%m%d")
    end_date = today.strftime("%Y%m%d")

    print(f"[{name}({ticker})] {start_date} ~ {end_date} 주가 데이터 수집 중...")

    # pykrx 데이터 수집
    df = stock.get_market_ohlcv_by_date(start_date, end_date, ticker)

    db = SessionLocal()
    try:
        company = get_or_create_company(db, ticker, name)

        count = 0
        for date_index, row in df.iterrows():
            current_date = date_index.date()

            # 중복 저장 방지
            exists = (
                db.query(StockPrice)
                .filter(
                    StockPrice.company_id == company.id,
                    StockPrice.price_date == current_date,
                )
                .first()
            )

            if not exists:
                price_record = StockPrice(
                    company_id=company.id,
                    price_date=current_date,
                    open_price=float(row["시가"]),
                    high_price=float(row["고가"]),
                    low_price=float(row["저가"]),
                    close_price=float(row["종가"]),
                    volume=int(row["거래량"]),
                    change_pct=float(row["등락률"]),
                )
                db.add(price_record)
                count += 1

        db.commit()
        print(f"DB 저장 완료: 신규 {count}건 데이터 추가됨")
    except Exception as e:
        db.rollback()
        print(f"오류 발생: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    fetch_and_save_stock("005930", "삼성전자")
