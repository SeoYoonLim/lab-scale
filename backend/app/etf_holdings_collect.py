"""ETF 구성종목 수집(KODEX: 삼성자산운용, TIGER: 미래에셋자산운용 홈페이지의 공시 PDF). 파싱은 app/etf_holdings.py, 저장은 app/etf_holdings_db.py.

예의: 하루 한 번 안팎으로만 돌린다(수동 실행). 요청 사이에 delay초(기본 2.0)를 쉬고, 브라우저로 위장하지 않고 용도를 밝힌 User-Agent를 쓴다.
robots.txt는 두 사이트 모두 허용이다(삼성 allow: /, 미래에셋 /magi/ 일부만 금지). 두 사이트 모두 이 주소를 자기 상품 페이지가 쓰는
비공개 문서화 API라서 형식이 바뀌면 ParseError로 그 ETF만 실패한다. 미래에셋 이용약관(회원 대상)에는 얻은 정보의 사전 동의 없는 복제·제공 금지
조항이 있다. 이 프로젝트는 학습·시연용이고 출처를 밝혀 상위 일부만 보여주지만, 서비스를 공개할 때는 운용사 허락을 따로 받아야 한다.

한 ETF의 실패(HTTP 오류·형식 변경·빈 결과)는 그 ETF만 실패로 남기고 다음으로 넘어간다. 기존 timeout은 app.http_retry.get을 쓰되, 재시도는
이 모듈이 한다: 연결 오류·5xx는 간격을 두고 한 번, 403·429는 재시도하지 않는다.

차단 존중(2026-10-29): 삼성 사이트는 Cloudflare 뒤에 있고, 짧은 시간에 요청이 몰리면(조사 중 약 100건) 429 + "Just a moment..." 확인 화면을 준다.
이 확인 화면을 우회하지 않는다. 403·429를 받은 호스트로는 그 실행이 끝날 때까지 더 요청하지 않고(HostBlocked) 사유만 남기며, 나중에 다시 실행한다.
상품 목록(fId 매핑, 14페이지)은 로컬 캐시(backups/kodex_fid_cache.json)에 두고 모르는 종목이 있을 때만 다시 받아 일상 실행의 요청 수를 줄인다.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import http_retry
from app.collectors import _safe_error
from app.etf_holdings import (
    KODEX,
    TIGER,
    ParseError,
    Snapshot,
    issuer_of,
    kodex_list_page,
    parse_kodex_pdf,
    parse_tiger_as_of,
    parse_tiger_pdf,
    tiger_isin,
    total_weight,
    weight_warnings,
)
from app.etf_holdings_db import save_snapshot
from app.models import Company

KST = ZoneInfo("Asia/Seoul")
USER_AGENT = "lab-scale-research-demo/1.0 (learning project; low-frequency public PDF lookup)"
KODEX_API = "https://www.samsungfund.com/api/v1/kodex"
TIGER_DETAIL = "https://investments.miraeasset.com/tigeretf/ko/product/search/detail"
DEFAULT_DELAY_S = 3.0
FID_CACHE = Path(__file__).resolve().parents[1] / "backups" / "kodex_fid_cache.json"  # backups/는 gitignore 대상
BLOCK_STATUSES = (403, 429)
RETRY_PAUSE_S = 30.0  # 일시적 오류(429·5xx·연결)로 실패한 ETF는 이만큼 쉰 뒤 한 번만 다시 시도한다
KODEX_MAX_PAGES = 30
TIGER_LIST_COUNT = 2000


@dataclass
class EtfResult:
    ticker: str
    name: str
    ok: bool = False
    skipped: str | None = None  # 수집 대상이 아닌 이유(미지원 운용사)
    source: str | None = None
    as_of: date | None = None
    rows: int = 0
    total_weight_pct: float | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    retryable: bool = False  # 실패 원인이 일시적 오류라 2차 시도 대상

    def as_dict(self) -> dict:
        return {"ticker": self.ticker, "name": self.name, "ok": self.ok, "skipped": self.skipped, "source": self.source,
                "as_of": self.as_of.isoformat() if self.as_of else None, "rows": self.rows,
                "total_weight_pct": self.total_weight_pct, "warnings": self.warnings, "error": self.error}


class HostBlocked(Exception):
    """호스트가 403·429(요청 과다·봇 확인 화면)로 막았다. 이번 실행에서는 그 호스트로 더 요청하지 않는다."""


def _status(exc: BaseException) -> int | None:
    return getattr(getattr(exc, "response", None), "status_code", None)


def is_challenge(resp) -> bool:
    """Cloudflare 봇 확인 화면인지(상태코드가 200·503이어도): cf-mitigated: challenge 헤더 또는 본문 앞부분의 "Just a moment..."."""
    if resp is None:
        return False
    headers = getattr(resp, "headers", None) or {}
    if str(headers.get("cf-mitigated", "")).lower() == "challenge":
        return True
    text = getattr(resp, "text", "")
    return isinstance(text, str) and "Just a moment..." in text[:2000]


class _Challenge(Exception):
    def __init__(self, resp):
        self.resp = resp


class PoliteClient:
    """요청마다 직전 요청과 delay초 간격을 둔다. 403·429를 받은 호스트는 막고(blocked) 다시 요청하지 않는다.
    get/sleep/clock을 주입할 수 있어 테스트가 기다리지 않는다."""

    def __init__(self, delay: float = DEFAULT_DELAY_S, get: Callable = http_retry.get, sleep: Callable = time.sleep,
                 clock: Callable = time.monotonic):
        self.delay, self._get, self._sleep, self._clock = delay, get, sleep, clock
        self._last: float | None = None
        self.requests = 0
        self.blocked: dict[str, str] = {}

    def _wait(self) -> None:
        if self._last is not None:
            wait = self.delay - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)

    def get(self, url: str, params: dict | None = None, label: str = ""):
        host = urlparse(url).netloc
        if host in self.blocked:
            raise HostBlocked(self.blocked[host])
        for attempt in (0, 1):
            self._wait()
            try:
                resp = self._get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=(5, 25), label=label, retries=0)
                if is_challenge(resp):
                    raise _Challenge(resp)
                return resp
            except Exception as e:  # noqa: BLE001 - 분류 후 다시 올린다
                if isinstance(e, _Challenge) or _status(e) in BLOCK_STATUSES or is_challenge(getattr(e, "response", None)):
                    code = getattr(getattr(e, "response", None), "status_code", None) or getattr(getattr(e, "resp", None), "status_code", None)
                    self.blocked[host] = (f"{host}가 요청을 막았습니다(HTTP {code}, 요청 과다 또는 봇 확인 화면). "
                                          "우회하지 않고 이번 실행에서는 이 사이트에 더 요청하지 않습니다. 잠시 뒤 다시 실행하세요.")
                    raise HostBlocked(self.blocked[host]) from None
                if attempt == 0 and http_retry.is_transient(e):  # 연결 오류·5xx: 간격을 두고 한 번만 더
                    continue
                raise
            finally:
                self._last = self._clock()
                self.requests += 1


def _load_cache(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def kodex_fid_map(client: PoliteClient, wanted: set[str], cache_path: Path | None = FID_CACHE) -> dict[str, str]:
    """종목코드 → KODEX 상품 id(fId). 캐시에 있는 종목은 요청하지 않고, 모르는 종목이 있을 때만 상품 목록(20개씩)을 넘겨 가며
    찾는다(다 나오면 멈춤). 새로 알게 된 매핑은 캐시에 합쳐 저장한다."""
    found = {t: f for t, f in (_load_cache(cache_path) if cache_path else {}).items() if t in wanted}
    if wanted <= set(found):
        return found
    learned: dict[str, str] = {}
    for page in range(1, KODEX_MAX_PAGES + 1):
        resp = client.get(f"{KODEX_API}/product.do", {"ordrColm": "NAV", "ordrSort": "DESC", "pageNo": page}, "KODEX 상품 목록")
        items = kodex_list_page(resp.json())
        if not items:
            break
        for it in items:
            learned[it["ticker"]] = it["fid"]
        if wanted <= set(found) | set(learned):
            break
    found.update({t: f for t, f in learned.items() if t in wanted})
    if cache_path and learned:
        try:
            cache_path.parent.mkdir(exist_ok=True)
            cache_path.write_text(json.dumps({**_load_cache(cache_path), **learned}, ensure_ascii=False, indent=0), encoding="utf-8")
        except OSError:
            pass
    return found


def fetch_kodex(client: PoliteClient, fid: str, today: date) -> Snapshot:
    resp = client.get(f"{KODEX_API}/product-pdf/{fid}.do", {"gijunYMD": today.strftime("%Y.%m.%d")}, "KODEX 구성종목")
    return parse_kodex_pdf(resp.json())


def fetch_tiger(client: PoliteClient, ticker: str) -> Snapshot:
    isin = tiger_isin(ticker)
    shell = client.get(f"{TIGER_DETAIL}/pdf.ajax", {"ksdFund": isin}, "TIGER 구성종목 기준일")
    as_of = parse_tiger_as_of(shell.text)
    table = client.get(
        f"{TIGER_DETAIL}/pdfListAjax.ajax",
        {"ksdFund": isin, "pageIndex": 1, "firstIndex": 0, "listCnt": TIGER_LIST_COUNT, "fixDate": as_of.strftime("%Y.%m.%d")},
        "TIGER 구성종목",
    )
    return parse_tiger_pdf(as_of, table.text)


def collect(db: Session, tickers: list[str] | None = None, delay: float = DEFAULT_DELAY_S, dry_run: bool = False,
            today: date | None = None, client: PoliteClient | None = None, log: Callable[[str], None] = print,
            retry_pause: float = RETRY_PAUSE_S, fid_cache: Path | None = FID_CACHE) -> list[EtfResult]:
    """company의 ETF(tickers가 있으면 그것만)를 수집한다. 결과는 ETF마다 한 개이고 실패해도 다음으로 넘어간다.
    일시적 오류로 실패한 ETF는 전부 끝난 뒤 retry_pause초 쉬고 한 번만 더 시도한다(운용사 서버의 429를 달래는 용도)."""
    today = today or datetime.now(KST).date()
    client = client or PoliteClient(delay)
    q = select(Company.id, Company.ticker, Company.name).where(Company.asset_type == "etf").order_by(Company.name)
    if tickers:
        q = q.where(Company.ticker.in_(tickers))
    etfs = db.execute(q).all()

    results = [EtfResult(e.ticker, e.name) for e in etfs]
    for r in results:
        r.source = issuer_of(r.name)
        if r.source is None:
            r.skipped = "수집 대상 운용사가 아님(KODEX·TIGER만)"
    fids: dict[str, str] = {}
    wanted = {r.ticker for r in results if r.source == KODEX}
    if wanted:
        try:
            fids = kodex_fid_map(client, wanted, fid_cache)
        except Exception as e:  # noqa: BLE001 - KODEX 전체가 실패해도 TIGER는 계속한다
            for r in results:
                if r.source == KODEX:
                    r.error = f"KODEX 상품 목록 조회 실패: {_safe_error(e)}"

    ids = {e.ticker: e.id for e in etfs}

    def attempt(r: EtfResult) -> None:
        r.error, r.retryable = None, False
        try:
            if r.source == KODEX:
                if r.ticker not in fids:
                    raise ParseError("KODEX 상품 목록에서 이 종목코드를 찾지 못했습니다")
                snap = fetch_kodex(client, fids[r.ticker], today)
            else:
                snap = fetch_tiger(client, r.ticker)
            if not snap.holdings:
                raise ParseError("현금성 자산을 뺀 구성종목이 0개입니다")
            r.as_of, r.rows, r.total_weight_pct = snap.as_of, len(snap.holdings), total_weight(snap.holdings)
            r.warnings = [w["message"] for w in weight_warnings(r.name, r.total_weight_pct, r.rows)] + snap.notes
            if not dry_run:
                save_snapshot(db, ids[r.ticker], snap)
            r.ok = True
        except Exception as e:  # noqa: BLE001 - 한 ETF의 실패가 전체를 막지 않는다
            db.rollback()
            r.error, r.retryable = _safe_error(e), isinstance(e, http_retry.TransientHTTPError) or http_retry.is_transient(e)
        log(f"{r.name}({r.ticker}) " + (f"OK {r.as_of} {r.rows}종목 합계 {r.total_weight_pct}%" if r.ok else f"실패 - {r.error}"))

    for r in results:
        if r.skipped:
            log(f"{r.name}({r.ticker}) 건너뜀 - {r.skipped}")
        elif not r.error:
            attempt(r)
    again = [r for r in results if r.retryable and not r.ok]
    if again:
        log(f"일시적 오류로 실패한 {len(again)}개를 {retry_pause:g}초 쉰 뒤 한 번 더 시도합니다.")
        client._sleep(retry_pause)
        for r in again:
            attempt(r)
    return results

