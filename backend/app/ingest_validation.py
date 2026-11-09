"""수집 데이터의 저장 전 검증(2026-11-08, 36차). DB를 쓰지 않는 순수 함수다(저장·격리 기록은 app/quarantine.py, 연결은 app/collectors.py).

레코드마다 판정한다.
- valid: 그대로 저장한다.
- warning: 저장하되 경고를 남긴다(실제 급등락처럼 이상해 보여도 틀렸다고 단정할 수 없는 값).
- quarantined: 기존 업무 테이블에 넣지 않고 격리한다(값 자체가 불가능하거나 종목·날짜 연결이 틀린 값, 기존 정상 값과 크게 어긋나는 값).

원칙
- 저장 직전에 판정을 끝낸다. 격리된 값은 기존 행을 갱신하지 않는다(기존 정상 데이터 보호).
- 한 레코드의 문제로 묶음 전체를 버리지 않는다. 단 ETF 구성종목은 (ETF, 기준일) 한 묶음이 한 단위라(저장이 그 날짜를 통째로 교체)
  묶음 단위로 판정한다(기존 트랜잭션 규칙 우선).
- 소스마다 규칙이 다르다: 급등락은 경고(가격제한폭·액면분할·실제 급등락), 환율은 KRX 달력을 쓰지 않는다(출처가 일요일 날짜·한국
  휴장일에도 값을 준다), 공시의 접수번호 날짜와 접수일 차이는 다음 영업일 접수면 정상, ETF 음수 비중은 정상일 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

VALID, WARNING, QUARANTINED = "valid", "warning", "quarantined"

# 가격 규칙: 한국 시장 가격제한폭 ±30%에 상한가 반올림 여유(app.price_quality와 같은 값)
PRICE_LIMIT = 0.305
OHLC_TOLERANCE = 0.005  # 종가가 고가·저가 밖으로 이만큼(0.5%)까지는 반올림 차이로 본다
INDEX_JUMP_WARN = 0.08  # 지수 하루 ±8%(서킷브레이커 1단계) 넘으면 경고
INDEX_CONFLICT = 0.15  # 같은 날짜의 기존 지수와 15% 넘게 다르면 격리
FX_JUMP_WARN = 0.05  # 환율 하루 ±5% 넘으면 경고(data_monitor 품질 검사와 같은 값)
FX_CONFLICT = 0.05  # 같은 날짜의 기존 환율과 5% 넘게 다르면 격리
FX_RANGES = {"USD/KRW": (500.0, 5000.0)}  # 통화쌍이 뒤집히거나(0.0007) 다른 쌍이 오면 이 범위를 벗어난다
SUPPORTED_INDEXES = ("KS11", "KQ11")
NEWS_OLD_DAYS = 30
FUTURE_TOLERANCE = timedelta(hours=1)  # 뉴스 발행 시각의 시계 차이 허용
ETF_WEIGHT_TOLERANCE = 5.0  # 비중 합계가 기대값(100·200)에서 이만큼(%p) 넘게 벗어나면 경고
ETF_WEIGHT_MAX_RATIO = 1.5  # 기대값의 1.5배를 넘으면 중복 집계 등 형식 오류로 보고 격리

_TICKER_RE = re.compile(r"^[0-9A-Z]{6}$")
_RCEPT_NO_RE = re.compile(r"^\d{14}$")
_URL_RE = re.compile(r"^https?://[^\s/]+\.[^\s]+$", re.I)


@dataclass
class Issue:
    code: str
    detail: str
    severity: str  # warning | quarantined


@dataclass
class Verdict:
    issues: list[Issue] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(i.severity == QUARANTINED for i in self.issues):
            return QUARANTINED
        return WARNING if self.issues else VALID

    def warn(self, code: str, detail: str) -> None:
        self.issues.append(Issue(code, detail, WARNING))

    def reject(self, code: str, detail: str) -> None:
        self.issues.append(Issue(code, detail, QUARANTINED))

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.issues]


@dataclass
class Checked:
    """판정 결과 한 건. record는 수집기가 만든 레코드(dict)이고 key는 격리 기록의 식별자(source_key)다."""
    record: dict
    key: str
    verdict: Verdict

    @property
    def status(self) -> str:
        return self.verdict.status


def split(checked: list[Checked]) -> tuple[list[Checked], list[Checked]]:
    """(저장할 것: valid+warning, 격리할 것)."""
    keep = [c for c in checked if c.status != QUARANTINED]
    return keep, [c for c in checked if c.status == QUARANTINED]


def _today(today: date | None) -> date:
    return today or datetime.now(KST).date()


def valid_ticker(ticker: str | None) -> bool:
    """KRX 단축코드 6자리(숫자, 신규 ETF·ETN은 영문 대문자 포함)."""
    return bool(ticker) and bool(_TICKER_RE.match(ticker))


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN


# ---------- 주가·ETF 종가 ----------


def check_prices(ticker: str, records: list[dict], existing: dict[date, float] | None = None,
                 prev_close: float | None = None, today: date | None = None) -> list[Checked]:
    """records: collectors._price_row 모양(price_date, open/high/low/close_price, volume, change_pct), 날짜 오름차순.
    existing: 같은 종목의 이미 저장된 {날짜: 종가}(같은 날짜 덮어쓰기 판정용). prev_close: 묶음 첫 날 앞의 저장된 종가(급등락 판정용)."""
    from app import krx_calendar

    today = _today(today)
    existing = existing or {}
    out: list[Checked] = []
    bad_ticker = not valid_ticker(ticker)
    dates: dict[date, list[int]] = {}
    for i, r in enumerate(records):
        dates.setdefault(r.get("price_date"), []).append(i)
    last_close = prev_close
    for i, r in enumerate(records):
        d = r.get("price_date")
        v = Verdict()
        key = f"stock_price:{ticker}:{d.isoformat() if isinstance(d, date) else d}"
        close = _num(r.get("close_price"))
        if bad_ticker:
            v.reject("invalid_ticker", f"종목코드 형식 오류: {ticker!r}")
        if not isinstance(d, date):
            v.reject("invalid_date", f"날짜 형식 오류: {d!r}")
        else:
            if d > today:
                v.reject("future_date", f"오늘({today})보다 뒤의 날짜")
            st = krx_calendar.day_status(d)
            if st in (krx_calendar.WEEKEND, krx_calendar.HOLIDAY):
                v.reject("non_trading_day", f"KRX 거래일이 아닌 날({st}: {krx_calendar.holiday_name(d) or '주말'})")
            if len(dates.get(d, [])) > 1:
                same = {tuple(_num(records[j].get(k)) for k in ("open_price", "high_price", "low_price", "close_price", "volume"))
                        for j in dates[d]}
                if len(same) > 1:
                    v.reject("duplicate_conflict", "같은 날짜에 값이 다른 행이 여러 개")
                elif dates[d][0] != i:
                    v.reject("duplicate_same", "같은 날짜·같은 값의 중복 행(첫 행만 저장)")
        if close is None:
            v.reject("close_missing", "종가 없음")
        elif close <= 0:
            v.reject("close_nonpositive", f"종가 {close} (0 이하)")
        vol = _num(r.get("volume"))
        if vol is not None and vol < 0:
            v.reject("volume_negative", f"거래량 {vol:.0f} (음수)")
        hi, lo = _num(r.get("high_price")), _num(r.get("low_price"))
        op = _num(r.get("open_price"))
        if any(x is not None and x < 0 for x in (hi, lo, op)):
            v.reject("price_negative", "시가·고가·저가 중 음수")
        elif close and close > 0 and hi and lo and hi > 0 and lo > 0:  # 거래정지일은 시가·고가·저가가 0으로 온다(정상)
            if hi < lo:
                v.reject("ohlc_inconsistent", f"고가 {hi} < 저가 {lo}")
            elif close > hi * (1 + OHLC_TOLERANCE) or close < lo * (1 - OHLC_TOLERANCE):
                v.reject("ohlc_inconsistent", f"종가 {close}가 고가·저가 범위({lo}~{hi}) 밖")
        if close and close > 0 and isinstance(d, date):
            old = existing.get(d)
            if old and old > 0 and abs(close / old - 1) > PRICE_LIMIT:
                v.reject("conflicts_existing", f"이미 저장된 {d} 종가 {old}와 {close / old * 100 - 100:+.1f}% 다름(기존 값 유지)")
            base = last_close
            if base and base > 0 and abs(close / base - 1) > PRICE_LIMIT:
                v.warn("extreme_move", f"직전 종가 {base} 대비 {close / base * 100 - 100:+.1f}%(가격제한폭 밖: 액면분할·실제 급등락 확인 필요)")
        if v.status != QUARANTINED and close:
            last_close = close
        out.append(Checked(r, key, v))
    return out


# ---------- 시장지수 ----------


def check_index(index_code: str, records: list[dict], existing: dict[date, float] | None = None,
                prev_close: float | None = None, today: date | None = None) -> list[Checked]:
    from app import krx_calendar

    today = _today(today)
    existing = existing or {}
    out, seen, last = [], set(), prev_close
    for r in records:
        d, v = r.get("price_date"), Verdict()
        key = f"market_index:{index_code}:{d.isoformat() if isinstance(d, date) else d}"
        close = _num(r.get("close_price"))
        if index_code not in SUPPORTED_INDEXES:
            v.reject("unsupported_index", f"지원하지 않는 지수 코드 {index_code!r}(KS11·KQ11만)")
        if not isinstance(d, date):
            v.reject("invalid_date", f"날짜 형식 오류: {d!r}")
        else:
            if d > today:
                v.reject("future_date", f"오늘({today})보다 뒤의 날짜")
            if krx_calendar.day_status(d) in (krx_calendar.WEEKEND, krx_calendar.HOLIDAY):
                v.reject("non_trading_day", "KRX 거래일이 아닌 날")
            if d in seen:
                v.reject("duplicate_same", "같은 날짜 중복 행(첫 행만 저장)")
            seen.add(d)
        if close is None:
            v.reject("close_missing", "지수 값 없음")
        elif close <= 0:
            v.reject("close_nonpositive", f"지수 {close} (0 이하)")
        elif isinstance(d, date):
            old = existing.get(d)
            if old and old > 0 and abs(close / old - 1) > INDEX_CONFLICT:
                v.reject("conflicts_existing", f"이미 저장된 {d} 지수 {old}와 {close / old * 100 - 100:+.1f}% 다름(기존 값 유지)")
            if last and last > 0 and abs(close / last - 1) > INDEX_JUMP_WARN:
                v.warn("extreme_move", f"직전 지수 {last} 대비 {close / last * 100 - 100:+.1f}%")
        if v.status != QUARANTINED and close:
            last = close
        out.append(Checked(r, key, v))
    return out


# ---------- 환율 ----------


def check_fx(pair_code: str, records: list[dict], existing: dict[date, float] | None = None,
             prev_close: float | None = None, today: date | None = None) -> list[Checked]:
    """KRX 달력을 쓰지 않는다: 출처가 한국 휴장일·일요일 날짜에도 값을 준다(2026-04~11 실측). 주말 날짜를 격리하지 않는다."""
    today = _today(today)
    existing = existing or {}
    lo_hi = FX_RANGES.get(pair_code)
    out, seen, last = [], set(), prev_close
    for r in records:
        d, v = r.get("price_date"), Verdict()
        key = f"exchange_rate:{pair_code}:{d.isoformat() if isinstance(d, date) else d}"
        close = _num(r.get("close_price"))
        if lo_hi is None:  # 정상 범위를 모르는 통화쌍은 범위로 판정하지 못한다(막지는 않고 경고만)
            v.warn("unknown_pair_range", f"정상 범위를 모르는 통화쌍 {pair_code!r}(범위 검사 생략)")
        if not isinstance(d, date):
            v.reject("invalid_date", f"날짜 형식 오류: {d!r}")
        else:
            if d > today:
                v.reject("future_date", f"오늘({today})보다 뒤의 날짜")
            if d in seen:
                v.reject("duplicate_same", "같은 날짜 중복 행(첫 행만 저장)")
            seen.add(d)
        if close is None:
            v.reject("close_missing", "환율 값 없음")
        elif close <= 0:
            v.reject("rate_nonpositive", f"환율 {close} (0 이하)")
        else:
            if lo_hi and not lo_hi[0] <= close <= lo_hi[1]:
                v.reject("pair_mismatch", f"{pair_code} 환율로 볼 수 없는 값 {close}(정상 범위 {lo_hi[0]:.0f}~{lo_hi[1]:.0f}, 통화쌍 뒤바뀜 의심)")
            if isinstance(d, date):
                old = existing.get(d)
                if old and old > 0 and abs(close / old - 1) > FX_CONFLICT:
                    v.reject("conflicts_existing", f"이미 저장된 {d} 환율 {old}와 {close / old * 100 - 100:+.1f}% 다름(기존 값 유지)")
            if last and last > 0 and abs(close / last - 1) > FX_JUMP_WARN:
                v.warn("extreme_move", f"직전 환율 {last} 대비 {close / last * 100 - 100:+.1f}%")
        if v.status != QUARANTINED and close:
            last = close
        out.append(Checked(r, key, v))
    return out


# ---------- 뉴스 ----------


def check_news(company_ticker: str | None, items: list[dict], now: datetime | None = None) -> list[Checked]:
    """items: {"title", "url", "published_at"(aware datetime 또는 None), "source"}. 같은 url은 묶음 안에서 한 번만.
    다른 언론사가 같은 사건을 쓴 기사(url이 다름)는 중복이 아니다(제목이 비슷해도 지우지 않는다)."""
    now = now or datetime.now(KST)
    out, seen = [], set()
    for it in items:
        v = Verdict()
        url = (it.get("url") or "").strip()
        key = f"news:{company_ticker}:{url or it.get('title') or '?'}"[:300]
        if not valid_ticker(company_ticker):
            v.reject("company_missing", "기사를 연결할 종목이 없음")
        title = (it.get("title") or "").strip()
        if not title:
            v.reject("title_missing", "제목 없음")
        if not url:
            v.reject("url_missing", "원문 링크 없음")
        elif not _URL_RE.match(url):
            v.reject("url_invalid", f"원문 링크 형식 오류: {url[:80]}")
        elif url in seen:
            v.reject("duplicate_same", "같은 기사(url) 중복(첫 건만 저장)")
        seen.add(url)
        pub = it.get("published_at")
        if not isinstance(pub, datetime):
            v.reject("published_at_invalid", f"발행 시각 없음·형식 오류: {pub!r}"[:120])
        else:
            if pub.tzinfo is None:
                v.reject("published_at_invalid", "발행 시각에 시간대가 없음")
            elif pub > now + FUTURE_TOLERANCE:
                v.reject("future_published", f"발행 시각 {pub.isoformat()}이 지금보다 뒤")
            elif pub < now - timedelta(days=NEWS_OLD_DAYS):
                v.warn("old_article", f"발행이 {NEWS_OLD_DAYS}일 넘게 지난 기사")
        out.append(Checked(it, key, v))
    return out


# ---------- DART 공시 ----------

_CORRECTION_RE = re.compile(r"^\[(?:기재정정|첨부정정|첨부추가|발행조건확정|정정제출요구|정정명령부과|기재정정\s*)\]")


def is_correction(title: str) -> bool:
    """정정·추가 공시(제목 앞 [기재정정] 등). 원본 공시와 접수번호가 다른 별개 공시다(덮어쓰지 않는다)."""
    return bool(_CORRECTION_RE.match((title or "").strip()))


def check_disclosures(company_ticker: str | None, items: list[dict], now: datetime | None = None) -> list[Checked]:
    """items: DART list.json 항목(rcept_no, rcept_dt(YYYYMMDD), report_nm, corp_name, flr_nm). 회사 명의 필터는 수집기가 먼저 한다.

    접수번호 앞 8자리(제출일)와 접수일(rcept_dt)이 다른 것은 격리하지 않는다: 34차 dev DB의 5건은 모두 접수일이 접수번호 날짜의
    다음 영업일이었다(정정 4건·원본 1건, 영업시간 뒤 제출로 보인다). 다음 영업일이면 receipt_next_business_day(경고),
    그 밖의 차이는 receipt_date_mismatch(경고)로 남긴다."""
    from app import krx_calendar

    now = now or datetime.now(KST)
    out, seen = [], set()
    for it in items:
        v = Verdict()
        rno = str(it.get("rcept_no") or "").strip()
        key = f"disclosure:{rno or '?'}"
        if not valid_ticker(company_ticker):
            v.reject("company_missing", "공시를 연결할 종목이 없음")
        if not _RCEPT_NO_RE.match(rno):
            v.reject("receipt_invalid", f"접수번호 형식 오류: {rno!r}")
        elif rno in seen:
            v.reject("duplicate_same", "같은 접수번호 중복(첫 건만 저장)")
        seen.add(rno)
        if not (it.get("report_nm") or "").strip():
            v.reject("title_missing", "공시 제목 없음")
        try:
            rdt = datetime.strptime(str(it.get("rcept_dt") or ""), "%Y%m%d").date()
        except ValueError:
            v.reject("receipt_date_invalid", f"접수일 형식 오류: {it.get('rcept_dt')!r}")
            rdt = None
        if rdt is not None:
            if rdt > now.astimezone(KST).date():
                v.reject("future_disclosed", f"접수일 {rdt}이 오늘보다 뒤")
            if _RCEPT_NO_RE.match(rno):
                try:
                    filed = datetime.strptime(rno[:8], "%Y%m%d").date()
                except ValueError:
                    filed = None
                if filed is None:
                    v.reject("receipt_invalid", f"접수번호 날짜 부분 오류: {rno[:8]}")
                elif filed != rdt:
                    if rdt == krx_calendar.next_trading_day(filed) or rdt == _next_weekday_after(filed):
                        v.warn("receipt_next_business_day", f"접수번호 날짜 {filed}의 다음 영업일 {rdt}에 접수(영업시간 뒤 제출로 보임)")
                    else:
                        v.warn("receipt_date_mismatch", f"접수번호 날짜 {filed}와 접수일 {rdt}가 다름")
        out.append(Checked(it, key, v))
    return out


def _next_weekday_after(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# ---------- ETF 구성종목(스냅샷 단위) ----------


def check_etf_snapshot(etf_ticker: str, etf_name: str, as_of: date | None, holdings: list[dict], expected_total: float,
                       today: date | None = None) -> Verdict:
    """(ETF, 기준일) 한 묶음 판정. holdings: [{"name", "code", "weight_pct"}].
    - 격리: 기준일 없음·미래, 구성종목 0개, 양의 비중 합계 0 이하, 비중 합계가 기대값의 1.5배 초과(중복 집계 등 형식 오류),
      이름 없는 종목, 비중이 숫자가 아님
    - 경고: 비중 합계가 기대값에서 5%p 넘게 벗어남(현금 제외·일부 누락), 음수 비중(파생상품·운용사 공시 구조에서 정상일 수 있다.
      look-through는 음수 비중을 빼고 계산한다), 같은 종목코드 중복
    - 기준일이 오래된 것은 판정하지 않는다(오래된 데이터와 틀린 데이터는 다르다: data_monitor가 따로 본다)."""
    today = _today(today)
    v = Verdict()
    if not valid_ticker(etf_ticker):
        v.reject("invalid_ticker", f"ETF 종목코드 형식 오류: {etf_ticker!r}")
    if not isinstance(as_of, date):
        v.reject("as_of_missing", "기준일 없음")
    elif as_of > today + timedelta(days=1):  # 운용사는 다음 영업일 기준 PDF를 전날 저녁에 올리기도 한다
        v.reject("future_as_of", f"기준일 {as_of}이 내일보다 뒤")
    if not holdings:
        v.reject("empty_snapshot", "구성종목 0개")
        return v
    weights = [_num(h.get("weight_pct")) for h in holdings]
    if any(w is None for w in weights):
        v.reject("weight_invalid", "비중이 숫자가 아닌 종목이 있음")
        return v
    if any(not (h.get("name") or "").strip() for h in holdings):
        v.reject("holding_name_missing", "이름 없는 구성종목")
    positive = sum(w for w in weights if w > 0)
    total = sum(weights)
    if positive <= 0:
        v.reject("nonpositive_weight_sum", f"양의 비중 합계 {positive:.2f}%")
    elif total > expected_total * ETF_WEIGHT_MAX_RATIO:
        v.reject("weight_sum_implausible", f"비중 합계 {total:.2f}%가 기대값 {expected_total:.0f}%의 {ETF_WEIGHT_MAX_RATIO}배 초과(중복 집계 의심)")
    elif abs(total - expected_total) > ETF_WEIGHT_TOLERANCE:
        v.warn("weight_sum_out_of_range", f"비중 합계 {total:.2f}%(기대 {expected_total:.0f}%)")
    if negatives := sum(1 for w in weights if w < 0):
        v.warn("negative_weight", f"음수 비중 {negatives}종목(파생상품·차입 등, look-through는 빼고 계산)")
    codes = [h.get("code") for h in holdings if h.get("code")]
    if len(codes) != len(set(codes)):
        v.warn("duplicate_code", "같은 종목코드가 두 번 이상(이름만 다른 행)")
    return v
