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
from dotenv import load_dotenv
from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import http_retry
from app.db.session import SessionLocal
from app.etf import mentions_etf_name
from app.models import Company, Disclosure, ExchangeRate, MarketIndex, News, StockPrice

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


# 회사명이 프로 스포츠팀/연예 등 다른 개체 이름과 겹쳐서, 회사명만으로 검색하면 스포츠·연예 기사가 섞여
# 들어오는 종목. 위 도메인 필터로 걸러낸 뒤에도 일반 경제 매체에 실린 스포츠 기사(예: '프로농구 현대모비스
# 시범경기')가 남아서, 이 종목들에 한해 검색어에 재무 키워드를 붙여(앵커링) 검색 단계에서 걸러낸다.
# 근거: 2026-09 저장된 뉴스 3,060건 중 스포츠/연예 도메인 기사가 3건 이상 섞인 14개 종목(건수 내림차순).
# 1~2건만 섞인 24개 종목(삼성전자, 셀트리온, 신세계 등)은 우연한 혼입이라 대상에서 뺐다. 앵커링은 "주가/실적"이
# 없는 일반 기업 뉴스를 대부분 걸러내므로(같은 기간 기준 기존 결과와의 겹침 14~24%) 꼭 필요한 종목에만 쓴다.
# 목록에 없는 종목은 기존대로 회사명 단독으로 검색한다.
NAME_COLLISION_COMPANIES = frozenset(
    {
        "KCC", "현대모비스", "한국가스공사", "NC", "KT", "LG", "두산", "한화생명", "CJ", "SK",
        "대한항공", "하이브", "한화", "삼성생명",
    }
)
NAME_COLLISION_ANCHOR = "주가 실적"


def news_search_query(company_name: str) -> str:
    """네이버 뉴스 검색어. 이름 충돌 종목이면 재무 키워드를 붙이고, 아니면 회사명 단독이다."""
    if company_name in NAME_COLLISION_COMPANIES:
        return f"{company_name} {NAME_COLLISION_ANCHOR}"
    return company_name


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
    # corpCode.xml은 11만 건이 넘는 zip이라 읽기 timeout을 길게 둔다
    resp = http_retry.get(DART_CORP_CODE_URL, params={"crtfc_key": key}, timeout=(5, 60), label="DART corpCode")

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
        resp = http_retry.get(
            DART_DISCLOSURE_LIST_URL,
            params={
                "crtfc_key": key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": page_no,
                "page_count": 100,
            },
            label="DART list",
        )
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


ASSET_TYPES = ("stock", "etf")


def get_or_create_company(db, ticker: str, name: str, asset_type: str = "stock") -> Company:
    """(ticker, name) 쌍으로 회사를 조회/생성한다. 회사 row를 만드는 유일한 경로.

    ticker가 KRX 코드 형식이 아니면(예: 회사명이 그대로 들어온 경우) 예외를 던져
    잘못된 row가 만들어지지 않게 한다. asset_type('stock'|'etf')은 새로 만들 때만 쓰고,
    이미 있는 회사의 asset_type은 바꾸지 않는다.
    """
    if asset_type not in ASSET_TYPES:
        raise ValueError(f"유효하지 않은 asset_type {asset_type!r}; {ASSET_TYPES} 중 하나여야 합니다.")
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

    company = Company(ticker=ticker, name=name, asset_type=asset_type)
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
    source: str | None = None


_API_KEY_RE = re.compile(r"(crtfc_key=)[^&\s]+")


def _read_fdr(reader, symbol: str, start_date: str, end_date: str, retries: int = http_retry.RETRIES):
    """FinanceDataReader 호출에 상한(http_retry.LIBRARY_TIMEOUT_S)과 일시적 오류 재시도(기본 2회)를 둔다.
    FDR은 내부에서 timeout 없이 requests를 부르기 때문이다. 일시적 오류가 아닌 예외는 바로 올라간다."""
    return http_retry.call_with_retry(lambda: http_retry.with_timeout(lambda: reader(symbol, start_date, end_date)),
                                      retries=retries, label=f"FDR {symbol}")


def _safe_error(e: Exception) -> str:
    # requests 예외 메시지에는 요청 URL이 들어가고 DART URL에는 API 키가 있어 마스킹한다.
    return _API_KEY_RE.sub(r"\1***", f"{type(e).__name__}: {e}")


def fetch_naver_news(company_name: str, display: int = 10, sort: str = "date") -> list[dict]:
    client_id = os.environ["NAVER_CLIENT_ID"]
    client_secret = os.environ["NAVER_CLIENT_SECRET"]

    resp = http_retry.get(
        NAVER_NEWS_URL,
        params={"query": company_name, "display": display, "start": 1, "sort": sort},
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        },
        label="Naver news",
    )
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
    ticker: str, name: str, days: int = 7, dry_run: bool = False, start_date: str | None = None, end_date: str | None = None
) -> CollectResult:
    """start_date/end_date(YYYY-MM-DD)를 주면 days 대신 그 기간을 받는다(scripts/backfill_history.py가 장기 이력에 쓴다)."""
    today = datetime.now(KST)
    start_date = start_date or (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = end_date or today.strftime("%Y-%m-%d")

    print(f"[{name}({ticker})] {start_date} ~ {end_date} 주가 데이터 수집 중...")

    try:
        df = _read_fdr(fdr.DataReader, ticker, start_date, end_date)
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


# 시장 전체 지수(MARKET_INDEXES) 수집. market_tool이 종목 등락률과 같은 기간으로 비교한다.
_MARKET_INDEX_COLUMNS = ("close_price", "change_pct")


def _index_row(date_index, row) -> dict:
    change = row["Change"] if "Change" in row else None
    return {
        "price_date": date_index.date(),
        "close_price": round(float(row["Close"]), 2),
        "change_pct": None if change is None or change != change else round(float(change) * 100, 2),
    }


# 지수 소스 폴백 체인. 앞에서부터 시도해 "최근 일봉이 오는" 첫 소스를 쓴다(2026-10-14 실측).
#  1) FDR 기본 심볼(KS11/KQ11)  2) FDR 별칭(KOSPI/KOSDAQ)  3) FDR의 Yahoo 경유 심볼(^KS11/^KQ11)
# 네이버 금융 지수 일별 시세 페이지(sise_index_day.naver)는 같은 날 확인 시 HTTP 410(서비스 종료)이라 체인에서 뺐다.
# yfinance는 3)이 이미 Yahoo를 쓰므로 의존성을 추가하지 않았다.
MARKET_INDEX_SOURCES = {
    "KS11": (("fdr:KS11", "KS11"), ("fdr:KOSPI", "KOSPI"), ("fdr-yahoo:^KS11", "^KS11")),
    "KQ11": (("fdr:KQ11", "KQ11"), ("fdr:KOSDAQ", "KOSDAQ"), ("fdr-yahoo:^KQ11", "^KQ11")),
}
# 가장 최근 일봉이 이보다 오래됐으면 "멈춘 소스"로 보고 다음 소스로 넘어간다(연휴를 감안한 달력 일수).
INDEX_MAX_STALE_DAYS = 10


def fetch_index_frame(index_code: str, start_date: str, end_date: str, reader=None, today=None):
    """폴백 체인을 순서대로 시도해 (DataFrame, 소스 이름, 시도별 실패 사유 목록)을 돌려준다. 모두 실패하면 df는 None."""
    reader = reader or fdr.DataReader
    today = today or datetime.now(KST).date()
    errors = []
    for source, symbol in MARKET_INDEX_SOURCES.get(index_code, ((f"fdr:{index_code}", index_code),)):
        try:
            # 소스 폴백 체인이 재시도 역할을 하므로 소스마다 다시 시도하지 않는다(timeout만)
            df = _read_fdr(reader, symbol, start_date, end_date, retries=0)
        except Exception as e:
            errors.append(f"{source}: {_safe_error(e)}")
            continue
        if df is None or df.empty or "Close" not in df or df["Close"].dropna().empty:
            errors.append(f"{source}: 데이터 없음")
            continue
        df = df.dropna(subset=["Close"])
        latest = df.index.max().date()
        if (today - latest).days > INDEX_MAX_STALE_DAYS:
            errors.append(f"{source}: 최신 일봉이 {latest}로 오래됨")
            continue
        return df, source, errors
    return None, None, errors


def fetch_and_save_market_index(
    index_code: str, days: int = 7, dry_run: bool = False, start_date: str | None = None, end_date: str | None = None
) -> CollectResult:
    """지수 일별 종가를 폴백 체인(MARKET_INDEX_SOURCES)으로 받아 market_index에 upsert한다(멱등).
    start_date/end_date를 주면 days 대신 그 기간을 받는다."""
    today = datetime.now(KST)
    start_date = start_date or (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = end_date or today.strftime("%Y-%m-%d")

    df, source, errors = fetch_index_frame(index_code, start_date, end_date, today=today.date())
    for msg in errors:
        print(f"[market_index {index_code}] 소스 실패 - {msg}")
    if df is None:
        return CollectResult(
            ok=False, error=f"'{index_code}' {start_date}~{end_date} 모든 지수 소스 실패: " + " | ".join(errors)
        )
    print(f"[market_index {index_code}] 소스 사용: {source}")

    records = [_index_row(i, r) for i, r in df.sort_index().iterrows()]

    db = SessionLocal()
    try:
        existing = {
            row.price_date: (float(row.close_price), None if row.change_pct is None else float(row.change_pct))
            for row in db.query(MarketIndex).filter(
                MarketIndex.index_code == index_code, MarketIndex.price_date >= records[0]["price_date"]
            )
        }
        new = [r for r in records if r["price_date"] not in existing]
        if dry_run:
            return CollectResult(ok=True, saved=len(new), fetched=len(records), source=source)

        stmt = pg_insert(MarketIndex).values([{**r, "index_code": index_code} for r in records])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_market_index_code_date",
            set_={col: stmt.excluded[col] for col in _MARKET_INDEX_COLUMNS},
        )
        db.execute(stmt)
        db.commit()
        return CollectResult(ok=True, saved=len(new), fetched=len(records), source=source)
    except Exception as e:
        db.rollback()
        return CollectResult(ok=False, fetched=len(records), error=_safe_error(e))
    finally:
        db.close()


# 환율(FR-07, 1차 범위는 환율만 - 금리는 쓸 만한 무료 소스를 찾지 못해 범위 밖). market_index와 달리 FDR이
# 통화쌍에는 Change 컬럼을 주지 않아서(실제 호출로 확인), 종가의 일별 변화율을 직접 계산한다.
_EXCHANGE_RATE_COLUMNS = ("close_price", "change_pct")


def fetch_and_save_exchange_rate(pair_code: str, days: int = 180, dry_run: bool = False) -> CollectResult:
    """FinanceDataReader로 환율 일별 종가를 받아 exchange_rate에 upsert한다(멱등). pair_code 예: 'USD/KRW'."""
    today = datetime.now(KST)
    start_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    try:
        df = _read_fdr(fdr.DataReader, pair_code, start_date, end_date)
    except Exception as e:
        return CollectResult(ok=False, error=_safe_error(e))

    if df is None or df.empty:
        return CollectResult(ok=False, error=f"'{pair_code}'의 {start_date}~{end_date} 환율 데이터를 받지 못했습니다.")

    df = df.dropna(subset=["Close"]).sort_index()
    change_pct = df["Close"].pct_change() * 100
    records = [
        {
            "price_date": idx.date(),
            "close_price": round(float(close), 2),
            # 첫 행은 전일 데이터가 없어 NaN(pandas 결측 비교는 자기 자신과도 다름을 이용해 None 처리).
            "change_pct": None if pct != pct else round(float(pct), 2),
        }
        for idx, close, pct in zip(df.index, df["Close"], change_pct)
    ]

    db = SessionLocal()
    try:
        existing = {
            row.price_date: (float(row.close_price), None if row.change_pct is None else float(row.change_pct))
            for row in db.query(ExchangeRate).filter(
                ExchangeRate.pair_code == pair_code, ExchangeRate.price_date >= records[0]["price_date"]
            )
        }
        new = [r for r in records if r["price_date"] not in existing]
        if dry_run:
            return CollectResult(ok=True, saved=len(new), fetched=len(records))

        stmt = pg_insert(ExchangeRate).values([{**r, "pair_code": pair_code} for r in records])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_exchange_rate_pair_date",
            set_={col: stmt.excluded[col] for col in _EXCHANGE_RATE_COLUMNS},
        )
        db.execute(stmt)
        db.commit()
        return CollectResult(ok=True, saved=len(new), fetched=len(records))
    except Exception as e:
        db.rollback()
        return CollectResult(ok=False, fetched=len(records), error=_safe_error(e))
    finally:
        db.close()


def backfill_company_market(dry_run: bool = False) -> tuple[int, int]:
    """company.market(KOSPI/KOSDAQ/KOSDAQ GLOBAL)을 KRX 상장 목록(fdr, build_universe와 같은 소스)에서 채운다.

    이미 값이 같으면 건드리지 않는다. 목록에 없는 종목은 그대로 둔다. (갱신 건수, 목록에 없어 못 채운 건수)를 돌려준다.
    """
    listing = http_retry.call_with_retry(lambda: http_retry.with_timeout(lambda: fdr.StockListing("KRX")),
                                         label="FDR StockListing")
    market_by_ticker = dict(zip(listing["Code"], listing["Market"]))

    db = SessionLocal()
    try:
        updated = missing = 0
        for company in db.query(Company).all():
            market = market_by_ticker.get(company.ticker)
            if market is None:
                missing += 1
            elif company.market != market:
                updated += 1
                if not dry_run:
                    company.market = market
        if not dry_run:
            db.commit()
        return updated, missing
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _etf_news_candidates(etf_name: str, display: int) -> tuple[list[dict], int]:
    """ETF 뉴스 후보: ETF 이름으로 최신순·관련도순 두 번 검색해 합치고(같은 기사는 한 번), 제목·본문에 이 ETF 이름이
    직접 나오는 기사만 최신순으로 display건 남긴다. (후보 목록, 이름이 없어 뺀 건수).

    이름 그대로 최신순으로만 검색하면 ETF 시장 전반 기사나 같은 계열의 다른 상품 기사가 섞여서, 1단계 수집분은 제목·본문에
    ETF 이름이 들어 있는 기사가 평균 50%였다("RISE 200"에는 무관한 기사도 섞였다). "KODEX 200"이 "KODEX 200TR"처럼
    더 긴 다른 ETF 이름의 일부로만 나오는 기사도 이 ETF의 기사가 아니라서 뺀다."""
    db = SessionLocal()
    try:
        others = [n for (n,) in db.query(Company.name).filter(Company.asset_type == "etf", Company.name != etf_name)]
    finally:
        db.close()
    seen, merged = set(), []
    for sort in ("date", "sim"):
        for it in fetch_naver_news(etf_name, display=NAVER_MAX_DISPLAY, sort=sort):
            link = it.get("link") or it.get("originallink")
            if link in seen:
                continue
            seen.add(link)
            merged.append(it)
    related = [
        it for it in merged
        if mentions_etf_name(f"{_clean_text(it.get('title', ''))} {_clean_text(it.get('description', ''))}", etf_name, others)
    ]
    related.sort(key=lambda it: parsedate_to_datetime(it["pubDate"]), reverse=True)
    return related[: display * NEWS_OVERFETCH], len(merged) - len(related)


# 네이버 뉴스 검색 API로 뉴스 수집 후 DB 저장 함수
def fetch_and_save_news(
    company_name: str, display: int = 10, dry_run: bool = False, ticker: str | None = None, etf: bool = False
) -> CollectResult:
    """ticker를 주면 (ticker, company_name) 쌍으로 회사를 조회/생성한다.

    ticker가 없으면 이미 등록된 회사(name 또는 ticker 일치)를 쓰고, 없으면 DART 정식
    회사명으로 종목코드를 조회한다. 조회에 실패하면 회사를 만들지 않고 중단한다.
    etf=True면 ETF 이름이 직접 나오는 기사만 저장한다(_etf_news_candidates). 주식(etf=False)은 이전과 같다.
    """
    print(f"[{company_name}] 뉴스 {display}건 수집 중...")

    unrelated = 0
    try:
        if etf:
            candidates, unrelated = _etf_news_candidates(company_name, display)
        else:
            candidates = fetch_naver_news(
                news_search_query(company_name), display=min(NAVER_MAX_DISPLAY, display * NEWS_OVERFETCH)
            )
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
    if etf:
        print(f"ETF 이름이 나오지 않아 제외한 기사 {unrelated}건")

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
