"""
외부 데이터 소스(pykrx, 네이버 뉴스, DART) 수집 후 DB 저장 스크립트 모음.

FastAPI 앱(app/main.py)과는 별개로, 배치성으로 직접 실행하거나
(python -m app.collectors) 다른 스크립트에서 함수 단위로 import해서 쓴다.
"""

import html
import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from pykrx import stock
from sqlalchemy import or_

from app.db.session import SessionLocal
from app.models import Company, Disclosure, News, StockPrice

load_dotenv()

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
HTML_TAG_RE = re.compile(r"<[^>]+>")

DART_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_DISCLOSURE_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
KST = ZoneInfo("Asia/Seoul")

# stock_code -> corp_code 매핑 캐시. corpCode.xml이 11만 건이 넘는 zip이라
# 프로세스당 한 번만 내려받아 메모리에 캐싱한다.
_dart_stock_to_corp_code: dict[str, str] | None = None
# corp_name(DART 정식 회사명) -> 상장 stock_code 목록. 티커 조회(resolve_ticker)용.
_dart_name_to_stock_codes: dict[str, list[str]] = {}

# KRX 종목코드 형식 (6자리 숫자, 신규 상장분은 영문 대문자 포함 가능)
TICKER_RE = re.compile(r"^[0-9A-Z]{6}$")


def _load_dart_corp_code_map() -> dict[str, str]:
    global _dart_stock_to_corp_code
    if _dart_stock_to_corp_code is not None:
        return _dart_stock_to_corp_code

    key = os.environ["DART_API_KEY"]
    resp = requests.get(DART_CORP_CODE_URL, params={"crtfc_key": key})
    resp.raise_for_status()

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    root = ET.fromstring(zf.read("CORPCODE.xml"))

    mapping = {}
    names: dict[str, list[str]] = {}
    for el in root.findall("list"):
        stock_code = (el.findtext("stock_code") or "").strip()
        if stock_code:
            mapping[stock_code] = el.findtext("corp_code")
            names.setdefault((el.findtext("corp_name") or "").strip(), []).append(stock_code)

    _dart_name_to_stock_codes.update(names)
    _dart_stock_to_corp_code = mapping
    return mapping


def get_dart_corp_code(ticker: str) -> str | None:
    """company.ticker(종목코드)로 DART corp_code를 찾는다. stock_code 기준 정확 매칭."""
    return _load_dart_corp_code_map().get(ticker)


def resolve_ticker(name: str) -> str | None:
    """DART 상장사 정식 회사명으로 종목코드를 찾는다. 정확 일치 + 유일할 때만 반환.

    부분 일치는 쓰지 않는다 ('현대차' -> '현대차증권', '카카오' -> '카카오뱅크' 오매칭 방지).
    약칭('현대차' 등)처럼 정식명과 다르면 None이므로 호출측이 ticker를 직접 넘겨야 한다.
    """
    _load_dart_corp_code_map()
    codes = _dart_name_to_stock_codes.get(name.strip(), [])
    return codes[0] if len(codes) == 1 else None


def fetch_dart_disclosures(corp_code: str, days: int = 90) -> list[dict]:
    key = os.environ["DART_API_KEY"]
    end_de = datetime.now().strftime("%Y%m%d")
    bgn_de = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    items = []
    page_no = 1
    while True:
        resp = requests.get(
            DART_DISCLOSURE_LIST_URL,
            params={
                "crtfc_key": key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": page_no,
                "page_count": 100,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "000":
            # "013"은 조회 결과 없음 -> 정상적으로 빈 리스트 취급
            if data.get("status") == "013":
                return items
            raise RuntimeError(f"DART API 오류 [{data.get('status')}] {data.get('message')}")

        items.extend(data.get("list", []))

        if page_no >= data.get("total_page", 1):
            break
        page_no += 1

    return items


def get_or_create_company(db, ticker: str, name: str) -> Company:
    """(ticker, name) 쌍으로 회사를 조회/생성한다. 회사 row를 만드는 유일한 경로.

    ticker가 KRX 코드 형식이 아니면(예: 회사명이 그대로 들어온 경우) 예외를 던져
    잘못된 row가 만들어지지 않게 한다.
    """
    if not TICKER_RE.match(ticker):
        raise ValueError(f"유효하지 않은 종목코드 {ticker!r} (name={name!r}); KRX 6자리 코드가 필요합니다.")

    company = db.query(Company).filter(Company.ticker == ticker).first()
    if company is not None:
        return company

    same_name = db.query(Company).filter(Company.name == name).first()
    if same_name is not None:
        raise ValueError(
            f"name={name!r}인 회사가 이미 다른 종목코드({same_name.ticker})로 존재합니다. 요청 종목코드={ticker}"
        )

    company = Company(ticker=ticker, name=name)
    db.add(company)
    db.flush()
    return company


def _clean_text(raw: str) -> str:
    return html.unescape(HTML_TAG_RE.sub("", raw)).strip()


@dataclass
class CollectResult:
    """수집 함수의 결과. ok=False이면 error에 실패 사유가 들어간다.

    saved: 신규 저장 건수 (dry_run이면 저장 예정 건수), fetched: API가 돌려준 전체 건수,
    records: dry_run일 때만 채워지는 저장 예정 레코드.
    """

    ok: bool
    saved: int = 0
    fetched: int = 0
    error: str | None = None
    records: list = field(default_factory=list)


_API_KEY_RE = re.compile(r"(crtfc_key=)[^&\s]+")


def _safe_error(e: Exception) -> str:
    # requests 예외 메시지에는 요청 URL이 들어가고 DART URL에는 API 키가 있어 마스킹한다.
    return _API_KEY_RE.sub(r"\1***", f"{type(e).__name__}: {e}")


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
def fetch_and_save_news(
    company_name: str, display: int = 10, dry_run: bool = False, ticker: str | None = None
) -> CollectResult:
    """ticker를 주면 (ticker, company_name) 쌍으로 회사를 조회/생성한다.

    ticker가 없으면 이미 등록된 회사(name 또는 ticker 일치)를 쓰고, 없으면 DART 정식
    회사명으로 종목코드를 조회한다. 조회에 실패하면 회사를 만들지 않고 중단한다.
    """
    print(f"[{company_name}] 뉴스 {display}건 수집 중...")

    try:
        items = fetch_naver_news(company_name, display=display)
    except Exception as e:
        msg = _safe_error(e)
        print(f"오류 발생: {msg}")
        return CollectResult(ok=False, error=msg)

    db = SessionLocal()
    try:
        if ticker is not None:
            company = get_or_create_company(db, ticker=ticker, name=company_name)
        else:
            company = (
                db.query(Company)
                .filter(or_(Company.name == company_name, Company.ticker == company_name))
                .first()
            )
            if company is None:
                resolved = resolve_ticker(company_name)
                if resolved is None:
                    msg = (
                        f"'{company_name}'의 종목코드를 찾지 못해 중단합니다. "
                        f"ticker 인자로 종목코드를 직접 넘기거나 DART 정식 회사명을 사용하세요."
                    )
                    print(msg)
                    return CollectResult(ok=False, fetched=len(items), error=msg)
                company = get_or_create_company(db, ticker=resolved, name=company_name)

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
            return CollectResult(ok=True, saved=len(to_insert), fetched=len(items), records=to_insert)

        for record in to_insert:
            db.add(record)
        db.commit()
        print(f"DB 저장 완료: 신규 {len(to_insert)}건 데이터 추가됨")
        return CollectResult(ok=True, saved=len(to_insert), fetched=len(items))
    except Exception as e:
        db.rollback()
        msg = _safe_error(e)
        print(f"오류 발생: {msg}")
        return CollectResult(ok=False, fetched=len(items), error=msg)
    finally:
        db.close()


# DART Open API로 공시 수집 후 DB 저장 함수
def fetch_and_save_disclosures(
    company_name: str, days: int = 90, dry_run: bool = False
) -> CollectResult:
    db = SessionLocal()
    items: list[dict] = []
    try:
        company = (
            db.query(Company)
            .filter(or_(Company.name == company_name, Company.ticker == company_name))
            .first()
        )
        if company is None:
            msg = f"'{company_name}'는 company 테이블에 등록되지 않았습니다. 먼저 주가 수집 등으로 등록해주세요."
            print(msg)
            return CollectResult(ok=False, error=msg)

        corp_code = get_dart_corp_code(company.ticker)
        if corp_code is None:
            msg = f"'{company_name}'(ticker={company.ticker})에 해당하는 DART corp_code를 찾지 못했습니다."
            print(msg)
            return CollectResult(ok=False, error=msg)

        print(f"[{company_name}] DART 공시 수집 중... (corp_code={corp_code})")
        items = fetch_dart_disclosures(corp_code, days=days)

        to_insert = []
        for item in items:
            # 임원/주요주주 개인 명의 공시는 제외하고, 회사 명의 공시만 저장
            if item.get("flr_nm") != item.get("corp_name"):
                continue

            source_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcptNo={item['rcept_no']}"

            # 중복 저장 방지 (source_url 기준)
            exists = db.query(Disclosure).filter(Disclosure.source_url == source_url).first()
            if exists:
                continue

            disclosed_at = datetime.strptime(item["rcept_dt"], "%Y%m%d").replace(tzinfo=KST)

            to_insert.append(
                Disclosure(
                    company_id=company.id,
                    title=item["report_nm"].strip(),
                    disclosure_type=item["report_nm"].strip()[:50],
                    content=None,
                    source_url=source_url,
                    disclosed_at=disclosed_at,
                )
            )

        if dry_run:
            print(
                f"[dry-run] 신규 저장 예정 {len(to_insert)}건 "
                f"(전체 조회 {len(items)}건 중, 회사 명의만 필터링)"
            )
            return CollectResult(ok=True, saved=len(to_insert), fetched=len(items), records=to_insert)

        for record in to_insert:
            db.add(record)
        db.commit()
        print(f"DB 저장 완료: 신규 {len(to_insert)}건 데이터 추가됨")
        return CollectResult(ok=True, saved=len(to_insert), fetched=len(items))
    except Exception as e:
        db.rollback()
        msg = _safe_error(e)
        print(f"오류 발생: {msg}")
        return CollectResult(ok=False, fetched=len(items), error=msg)
    finally:
        db.close()


if __name__ == "__main__":
    fetch_and_save_stock("005930", "삼성전자")
