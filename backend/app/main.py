import html
import os
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from pykrx import stock
from sqlalchemy import or_

from app.db.session import SessionLocal
from app.models import Company, News, StockPrice

load_dotenv()

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
HTML_TAG_RE = re.compile(r"<[^>]+>")


def get_or_create_company(db, ticker: str, name: str) -> Company:
    # ticker 또는 name 둘 중 하나만 일치해도 같은 회사로 취급한다.
    # (뉴스 수집은 종목코드를 모르는 채로 이름만 가지고 회사를 조회/생성하기 때문에,
    #  ticker만으로 매칭하면 이미 주가 수집으로 만들어진 회사와 별개의 중복 행이 생긴다.)
    company = (
        db.query(Company)
        .filter(or_(Company.ticker == ticker, Company.name == name))
        .first()
    )
    if company is None:
        company = Company(ticker=ticker, name=name)
        db.add(company)
        db.flush()
    return company


def _clean_text(raw: str) -> str:
    return html.unescape(HTML_TAG_RE.sub("", raw)).strip()


def fetch_naver_news(company_name: str, display: int = 10) -> list[dict]:
    client_id = os.environ["NAVER_CLIENT_ID"]
    client_secret = os.environ["NAVER_CLIENT_SECRET"]

    resp = requests.get(
        NAVER_NEWS_URL,
        params={"query": company_name, "display": display, "start": 1, "sort": "date"},
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        },
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


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


# 네이버 뉴스 검색 API로 뉴스 수집 후 DB 저장 함수
def fetch_and_save_news(company_name: str, display: int = 10, dry_run: bool = False):
    print(f"[{company_name}] 뉴스 {display}건 수집 중...")

    items = fetch_naver_news(company_name, display=display)

    db = SessionLocal()
    try:
        company = get_or_create_company(db, ticker=company_name, name=company_name)

        to_insert = []
        for item in items:
            url = item.get("link") or item.get("originallink")

            # 중복 저장 방지 (url 기준)
            exists = db.query(News).filter(News.url == url).first()
            if exists:
                continue

            to_insert.append(
                News(
                    company_id=company.id,
                    title=_clean_text(item["title"]),
                    content=_clean_text(item["description"]),
                    source=urlparse(item.get("originallink") or url).netloc,
                    url=url,
                    published_at=parsedate_to_datetime(item["pubDate"]),
                )
            )

        if dry_run:
            print(f"[dry-run] 신규 저장 예정 {len(to_insert)}건 (전체 조회 {len(items)}건 중)")
            return to_insert

        for record in to_insert:
            db.add(record)
        db.commit()
        print(f"DB 저장 완료: 신규 {len(to_insert)}건 데이터 추가됨")
    except Exception as e:
        db.rollback()
        print(f"오류 발생: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    fetch_and_save_stock("005930", "삼성전자")
