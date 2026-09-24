"""
외부 데이터 소스(FinanceDataReader 주가, 네이버 뉴스, DART) 수집 후 DB 저장 스크립트 모음.

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

import FinanceDataReader as fdr
import requests
from dotenv import load_dotenv
from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import SessionLocal
from app.models import Company, Disclosure, News, StockPrice

load_dotenv()

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
NAVER_MAX_DISPLAY = 100
# 걸러낼 기사를 감안해 요청 건수의 몇 배를 받아온 뒤 필터링해서 요청 건수만큼만 남긴다.
NEWS_OVERFETCH = 3
HTML_TAG_RE = re.compile(r"<[^>]+>")

# 종목명이 프로 스포츠팀 이름과 겹치는 종목(현대모비스, KCC, 한국가스공사, NC, KT 등)의 뉴스 검색에는
# 스포츠/연예 기사가 섞여 들어온다. 저장된 뉴스 3,060건의 도메인 집계에서 네이버 스포츠/연예 섹션이 4.4%
# (135건)를 차지했고, 현대모비스는 20건 중 18건이 농구 기사였다.
# 도메인 이름의 키워드('star', 'game' 등)로 거르면 주가 시세 기사를 쓰는 매체(topstarnews)나 게임사 관련
# 기사를 싣는 게임 전문지까지 걸리므로, 내용이 명백히 비금융인 도메인만 정확히 지정한다(하위 도메인 포함).
NON_FINANCIAL_DOMAINS = (
    "sports.naver.com",
    "entertain.naver.com",
    "basketkorea.com",
    "sportalkorea.com",
    "thesportstimes.co.kr",
    "golfhankook.hankooki.com",
    "ent.sbs.co.kr",
    "maxmovie.com",
)


def is_non_financial_host(host: str) -> bool:
    host = (host or "").lower()
    return any(host == d or host.endswith("." + d) for d in NON_FINANCIAL_DOMAINS)


def _is_non_financial_item(item: dict) -> bool:
    # 네이버 뉴스 링크(link)와 원문 링크(originallink) 중 하나라도 비금융 도메인이면 제외한다.
    return any(is_non_financial_host(urlparse(item.get(k) or "").netloc) for k in ("link", "originallink"))

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

    saved: 신규 저장 건수 (dry_run이면 저장 예정 건수), fetched: 필터 후 저장 대상으로 검토한 건수,
    skipped: 비금융 도메인이라 제외한 건수(뉴스만), records: dry_run일 때만 채워지는 저장 예정 레코드.
    """

    ok: bool
    saved: int = 0
    fetched: int = 0
    error: str | None = None
    records: list = field(default_factory=list)
    skipped: int = 0


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


_STOCK_PRICE_COLUMNS = ("open_price", "high_price", "low_price", "close_price", "volume", "change_pct")


def _price_row(date_index, row) -> dict:
    # FDR의 Change는 소수(0.0577)라서 등락률(%)로 바꾼다. 결측이면 None.
    change = row["Change"]
    return {
        "price_date": date_index.date(),
        "open_price": float(row["Open"]),
        "high_price": float(row["High"]),
        "low_price": float(row["Low"]),
        "close_price": float(row["Close"]),
        "volume": int(row["Volume"]),
        "change_pct": None if change != change else round(float(change) * 100, 2),
    }


def _price_values(record: dict) -> tuple:
    return (
        round(record["open_price"], 2),
        round(record["high_price"], 2),
        round(record["low_price"], 2),
        round(record["close_price"], 2),
        int(record["volume"]),
        None if record["change_pct"] is None else round(record["change_pct"], 2),
    )


# FinanceDataReader(KRX 로그인 불필요)로 주가 수집 후 DB에 upsert하는 함수.
# 이미 있는 날짜는 최신 값으로 갱신한다. 장 마감 전에 저장된 행(거래량 등)이 남지 않게 하기 위해서다.
def fetch_and_save_stock(
    ticker: str, name: str, days: int = 7, dry_run: bool = False
) -> CollectResult:
    today = datetime.now(KST)
    start_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    print(f"[{name}({ticker})] {start_date} ~ {end_date} 주가 데이터 수집 중...")

    try:
        df = fdr.DataReader(ticker, start_date, end_date)
    except Exception as e:
        msg = _safe_error(e)
        print(f"오류 발생: {msg}")
        return CollectResult(ok=False, error=msg)

    if df is None or df.empty:
        msg = f"FinanceDataReader가 '{ticker}'의 {start_date}~{end_date} 주가 데이터를 반환하지 않았습니다."
        print(msg)
        return CollectResult(ok=False, error=msg)

    records = [_price_row(i, r) for i, r in df.dropna(subset=["Close"]).iterrows()]

    db = SessionLocal()
    try:
        company = get_or_create_company(db, ticker, name)

        existing = {
            row.price_date: (
                round(float(row.open_price), 2) if row.open_price is not None else None,
                round(float(row.high_price), 2) if row.high_price is not None else None,
                round(float(row.low_price), 2) if row.low_price is not None else None,
                round(float(row.close_price), 2),
                int(row.volume) if row.volume is not None else None,
                round(float(row.change_pct), 2) if row.change_pct is not None else None,
            )
            for row in db.query(StockPrice).filter(
                StockPrice.company_id == company.id,
                StockPrice.price_date >= records[0]["price_date"],
            )
        }
        new = [r for r in records if r["price_date"] not in existing]
        changed = [r for r in records if r["price_date"] in existing and existing[r["price_date"]] != _price_values(r)]
        summary = f"신규 {len(new)}건, 값 갱신 {len(changed)}건, 변경 없음 {len(records) - len(new) - len(changed)}건"

        if dry_run:
            print(f"[dry-run] {summary} (조회 {len(records)}건)")
            return CollectResult(ok=True, saved=len(new), fetched=len(records))

        stmt = pg_insert(StockPrice).values([{**r, "company_id": company.id} for r in records])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_stock_price_company_date",
            set_={col: stmt.excluded[col] for col in _STOCK_PRICE_COLUMNS},
        )
        db.execute(stmt)
        db.commit()
        print(f"DB 저장 완료: {summary}")
        return CollectResult(ok=True, saved=len(new), fetched=len(records))
    except Exception as e:
        db.rollback()
        msg = _safe_error(e)
        print(f"오류 발생: {msg}")
        return CollectResult(ok=False, fetched=len(records), error=msg)
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
        candidates = fetch_naver_news(company_name, display=min(NAVER_MAX_DISPLAY, display * NEWS_OVERFETCH))
    except Exception as e:
        msg = _safe_error(e)
        print(f"오류 발생: {msg}")
        return CollectResult(ok=False, error=msg)

    # 비금융 도메인 기사를 제외하고, 요청한 건수만큼만 남긴다.
    items, skipped = [], 0
    for it in candidates:
        if len(items) >= display:
            break
        if _is_non_financial_item(it):
            skipped += 1
        else:
            items.append(it)

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

            # 중복 저장 방지 (종목별 url 기준). 여러 종목이 함께 언급된 기사는 종목마다 저장한다.
            exists = (
                db.query(News)
                .filter(News.url == url, News.company_id == company.id)
                .first()
            )
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
            print(
                f"[dry-run] 신규 저장 예정 {len(to_insert)}건 "
                f"(검토 {len(items)}건 중, 비금융 도메인 제외 {skipped}건)"
            )
            return CollectResult(
                ok=True, saved=len(to_insert), fetched=len(items), records=to_insert, skipped=skipped
            )

        for record in to_insert:
            db.add(record)
        db.commit()
        print(f"DB 저장 완료: 신규 {len(to_insert)}건 데이터 추가됨 (비금융 도메인 제외 {skipped}건)")
        return CollectResult(ok=True, saved=len(to_insert), fetched=len(items), skipped=skipped)
    except Exception as e:
        db.rollback()
        msg = _safe_error(e)
        print(f"오류 발생: {msg}")
        return CollectResult(ok=False, fetched=len(items), error=msg, skipped=skipped)
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
