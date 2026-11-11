"""데이터 갱신을 한 번에 실행한다. 기존 수집 스크립트/함수를 순서대로 호출하고(새로 구현하지 않음) 단계마다
data_refresh_log에 한 행씩 기록한다. GET /api/status의 last_refresh가 이 기록을 읽는다.

단계(순서 고정): prices(주식 300 + ETF 50) -> index(코스피/코스닥) -> fx(환율) -> news(뉴스·공시, ETF는 뉴스만)
-> embed(임베딩, Ollama bge-m3 필요) -> content(공시 본문)

한 단계가 실패해도 다음 단계로 계속 진행하고, 마지막에 단계별 결과 표를 출력한다. 하나라도 실패하면 종료 코드 1.
외부 호출의 재시도/속도 제한은 기존 수집 함수를 그대로 따른다. 모든 수집은 upsert라서 다시 실행해도 안전하다.
평일 15:30(KST) 이후에 실행하는 것을 권장한다(장중에 실행하면 당일 행이 장중 값이다. 이후 실행에서 종가로 덮어쓴다).

사용 예:
  python -m scripts.refresh_data --dry-run                  # 실제 호출 없이 계획만 출력
  python -m scripts.refresh_data                            # 전 단계
  python -m scripts.refresh_data --only prices,index,fx     # 일부 단계만
  python -m scripts.refresh_data --skip news,embed,content  # 일부 단계 제외
  python -m scripts.refresh_data --days 30                  # 주가/지수/환율 조회 기간(달력 일), 기본 10
  python -m scripts.refresh_data --only news --news-only --news-state backups/news_state.json  # 뉴스만, 끊기면 이어서
  python -m scripts.refresh_data --only fx --earnings        # 실적(DART 주요 계정)도 함께(기본 단계에는 없다)
  python -m scripts.refresh_data --earnings-only             # 실적만(다른 단계 실행·기록 없음)

운영 기능(2026-11-07, docs/DATA_REFRESH_MONITORING.md):
  python -m scripts.refresh_data --all                       # 기본 6단계 전부(옵션 없음과 같다)
  python -m scripts.refresh_data --source stock_price,dart    # 데이터 이름으로 단계 고르기(dart = news 단계)
  python -m scripts.refresh_data --etf-holdings               # 기본 단계 + ETF 구성종목(KODEX·TIGER)
  python -m scripts.refresh_data --retry-failed               # 마지막 시도가 실패·부분 성공인 단계만 다시
  python -m scripts.refresh_data --status [--json]            # 수집 없이 소스별 상태 보고서(종료 코드 0 정상 / 3 확인 필요)
  python -m scripts.refresh_data --prune-logs [--dry-run]     # 180일 넘은 갱신 기록 정리(단계별 마지막 성공·실패는 남김)
- 실행 전체를 Postgres advisory lock 하나로 잡는다. 다른 실행이 돌고 있으면 바로 종료 코드 4(중복 실행 안 함).
- data_refresh_run에 실행 한 건, data_refresh_log에 단계마다 한 행(run_id, 소요 시간, 재시도 수, 오류 코드, 품질 상태).
- 단계 결과: success / partial(종목별 실패가 절반 미만) / failed / skipped. 일시적 오류(timeout·연결·5xx)로 실패한 단계는
  --step-retries번(기본 1) 다시 시도한다. 인증·요청 제한(401·403·429)·데이터 형식 오류는 다시 시도하지 않는다.
- 실행 뒤 품질 검사(app/data_monitor)를 돌려 보고서(logs/refresh/refresh_status.{json,md})를 쓰고 알림(표준 오류)을 낸다.
- 종료 코드: 0 전부 성공·품질 error 없음 / 1 실패 단계 있음 / 2 사용법 오류 / 3 실패는 없지만 부분 성공 또는 품질 error /
  4 다른 실행이 락을 잡고 있음 / 5 DB에 연결할 수 없음.

실적(--earnings, 2026-11-03): 기본 단계가 아니다. 분기마다 한 번이면 충분하고 종목당 DART 요청 12회(약 3,600회)라 따로 켠다.
app.earnings.collect를 마지막에 실행하고 요약(요청·저장·없음·오류 수)을 출력한다. data_refresh_log에는 기록하지 않는다
(GET /api/status의 last_refresh 단계 구성이 그대로이도록).
"""

import argparse
import contextlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

STEP_ORDER = ("prices", "index", "fx", "news", "embed", "content")
# 기본 실행에 들지 않는 단계(2026-11-07). GET /api/status의 last_refresh(REFRESH_STEPS = STEP_ORDER)에는 들지 않는다.
OPTIONAL_STEPS = ("etf_holdings",)
ALL_STEPS = STEP_ORDER + OPTIONAL_STEPS
# --source 이름 -> 단계. 같은 단계가 여러 데이터를 만든다(prices: 주식·ETF 종가, news: 뉴스·DART 공시).
SOURCE_STEPS = {
    "stock_price": "prices", "etf_price": "prices", "prices": "prices",
    "market_index": "index", "index": "index",
    "exchange_rate": "fx", "fx": "fx",
    "news": "news", "dart": "news", "disclosure": "news",
    "embeddings": "embed", "embed": "embed",
    "disclosure_content": "content", "content": "content",
    "etf_holdings": "etf_holdings",
}
UNSUPPORTED_SOURCES = {
    "kospi200": "KOSPI200 수집기가 없다(market_index는 KS11·KQ11만)",
    "interest_rate": "금리 수집기가 없다",
    "overseas_stock": "해외 주식 수집기가 없다",
    "earnings": "분기 실적은 --earnings / --earnings-only로 따로 실행한다(자동 실행 대상 아님)",
}
STEP_RETRIES = 1  # 일시적 오류로 실패한 단계의 재시도 횟수(기본)
STEP_RETRY_BACKOFF_S = 60.0  # 재시도 전 대기(초), 다음 재시도마다 2배
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PARTIAL, EXIT_LOCKED, EXIT_DB = 0, 1, 2, 3, 4, 5

PROGRESS_INTERVAL_SEC = 30  # 진행 줄(`[i/N] ...`)은 이 간격으로만 출력한다.
DEFAULT_TIMEOUT_MIN = 30  # 한 단계가 이보다 오래 걸리면 중단하고 failed(StepTimeout)로 기록한다.
MAX_FAILURE_RATIO = 0.5  # 종목별 수집 실패가 이 비율 이상이면 그 단계를 실패로 본다(일부 실패는 경고만).

_QUERY_SECRET_RE = re.compile(
    r"(?i)((?:crtfc_key|api[_-]?key|apikey|client[_-]?secret|client[_-]?id|secret|token|key)=)[^&\s]+"
)
_PROGRESS_RE = re.compile(r"^\[\d+/\d+\]")
_ALWAYS_SHOW_RE = re.compile(r"FAIL|실패|오류|Error|Traceback")


# ---------------------------------------------------------------- 순수 로직


def _env_secrets() -> tuple[str, ...]:
    names = ("NAVER_CLIENT_SECRET", "NAVER_CLIENT_ID", "DART_API_KEY", "JWT_SECRET")
    return tuple(v for n in names if len(v := os.environ.get(n, "")) >= 8)


def mask_secrets(text: str, secrets: tuple[str, ...] | None = None) -> str:
    """URL 쿼리스트링의 키/토큰 값과, 환경변수에 든 비밀값 자체를 ***로 가린다."""
    text = _QUERY_SECRET_RE.sub(r"\1***", text)
    if secrets is None:
        secrets = _env_secrets()
    for s in secrets:
        text = text.replace(s, "***")
    return text


def is_market_hours(now: datetime) -> bool:
    """거래일 09:00~15:30 KST이면 True. 2026-11-08(36차)부터 KRX 휴장일은 장중이 아니다(app.krx_calendar)."""
    from app.krx_calendar import HOLIDAY, day_status

    now = now.astimezone(KST)
    return now.weekday() < 5 and day_status(now.date()) != HOLIDAY and (9, 0) <= (now.hour, now.minute) <= (15, 30)


def select_steps(only: list[str] | None, skip: list[str] | None) -> list[str]:
    """--only/--skip으로 실행할 단계를 STEP_ORDER 순서로 돌려준다. 모르는 이름은 ValueError."""
    for name in (only or []) + (skip or []):
        if name not in STEP_ORDER:
            raise ValueError(f"알 수 없는 단계 '{name}' (가능: {', '.join(STEP_ORDER)})")
    chosen = set(only) if only else set(STEP_ORDER)
    return [s for s in STEP_ORDER if s in chosen and s not in set(skip or [])]


# 기본 조회 기간(--days를 주지 않을 때). 예전 기본값은 10일 고정이라 마지막 데이터가 그보다 오래되면 공백이 남았다
# (2026-10-14 이후 2주간 갱신하지 않았다가 10-28에 돌렸더니 10-15, 10-16 두 거래일이 비었다).
DAYS_MIN = 10  # 최소(데이터가 최신이어도 이만큼은 다시 받아 장중 행을 종가로 덮는다)
DAYS_CAP = 60  # 상한
DAYS_SLACK = 3  # 여유
GAP_LOOKBACK_DAYS = 30  # 이 기간 안에서 시세가 없는 평일(공백)을 찾는다


def default_days(present: set[date], today: date, *, minimum: int = DAYS_MIN, cap: int = DAYS_CAP,
                 slack: int = DAYS_SLACK, lookback: int = GAP_LOOKBACK_DAYS) -> int:
    """기본 조회 기간(달력 일). 마지막 시세 날짜(present의 최댓값)와, 최근 lookback일 안에서 시세가 없는 가장 이른 평일
    중 더 이른 날부터 오늘까지 + slack일, [minimum, cap]으로 자른다. 시세가 하나도 없으면 cap.

    present는 최근 lookback일 이상의 시세 날짜 집합이다. 2026-11-08(36차)부터 KRX 휴장일은 공백으로 보지 않는다(app.krx_calendar).
    달력으로 확인할 수 없는 평일은 공백일 수 있는 날로 본다. 오늘은 공백으로 보지 않는다(아직 수집 전일 수 있음)."""
    from app.krx_calendar import HOLIDAY, day_status

    if not present:
        return cap
    start = max(present)
    first = min(present)
    d = max(today - timedelta(days=lookback), first)
    while d < today:
        if d.weekday() < 5 and day_status(d) != HOLIDAY and d not in present:
            start = min(start, d)
            break
        d += timedelta(days=1)
    return max(minimum, min(cap, (today - start).days + slack))


def parse_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


class StepTimeout(BaseException):
    """단계 시간 초과. 수집 함수의 `except Exception`에 삼켜지지 않도록 BaseException으로 둔다."""


class FailureRatioTooHigh(Exception):
    pass


def check_failure_ratio(output: str) -> str | None:
    """collect_prices/collect_universe 요약 JSON의 targets/failures를 읽어, 실패가 절반 이상이면 예외를 던진다.
    일부 실패면 경고 문구를, 없으면 None을 돌려준다."""
    targets = re.search(r'"targets":\s*(\d+)', output)
    failures = re.search(r'"failures":\s*(\d+)', output)
    if not targets or not failures:
        return None
    t, f = int(targets.group(1)), int(failures.group(1))
    if f == 0:
        return None
    if t and f / t >= MAX_FAILURE_RATIO:
        raise FailureRatioTooHigh(f"{t}건 중 {f}건 실패")
    return f"일부 실패 {f}/{t}건(단계는 성공으로 처리)"


class Partial(str):
    """단계가 일부 대상만 실패했다는 표식(경고 문구). 일반 문자열 경고는 예전처럼 success다."""


_TRANSIENT_NAMES = ("Timeout", "TimeoutError", "ReadTimeout", "ConnectTimeout", "ConnectionError", "TransientHTTPError", "StepTimeout")


def classify_error(e: BaseException) -> tuple[str, bool]:
    """실패 원인 코드와 단계 재시도 여부. 수집 함수 안에서 이미 http_retry가 요청 단위로 다시 시도하므로, 단계 재시도는
    일시적 오류(timeout·연결·5xx)만 한다. 인증 실패·요청 제한(429·차단)·데이터 형식 오류는 다시 해도 같거나 상대 서버에 부담이다."""
    import requests

    name = type(e).__name__
    if isinstance(e, StepTimeout) or isinstance(e, (TimeoutError, requests.Timeout)):
        return "timeout", True
    if isinstance(e, SystemExit):
        return ("rate_limited", False) if e.code == 3 else ("script_exit", False)
    if isinstance(e, FailureRatioTooHigh):
        return "failure_ratio", False
    if isinstance(e, requests.HTTPError):
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (401, 403):
            return "auth", False
        if status == 429:
            return "rate_limited", False
        if status is not None and status >= 500:
            return "server_error", True
        return "http_error", False
    if isinstance(e, (requests.ConnectionError, ConnectionError)):
        return "network", True
    if name == "HostBlocked":
        return "rate_limited", False
    if isinstance(e, (ValueError, KeyError, TypeError, IndexError)):
        return "bad_data", False
    if isinstance(e, RuntimeError):  # prices·etf_holdings가 하위 실패를 모아 올린 것: 전부 일시적 오류면 다시 시도
        parts = [p.split(":")[-1].strip() for p in str(e).split(";") if p.strip()]
        if parts and all(any(t in p for t in _TRANSIENT_NAMES) for p in parts):
            return "timeout" if all("Timeout" in p for p in parts) else "network", True
        return "collector_error", False
    return "unknown", False


@dataclass
class Step:
    key: str
    label: str
    run: Callable[[], str | None]  # 경고 문구를 돌려주거나 None
    count: Callable[[], int | None] = lambda: None  # 추가된 행 수 계산용(실행 전후 차이)
    latest: Callable[[], date | None] = lambda: None


@dataclass
class StepResult:
    step: str
    status: str  # success | failed | skipped
    started_at: datetime
    finished_at: datetime
    rows_added: int | None = None
    latest_date: date | None = None
    error_type: str | None = None
    error_message: str = ""  # 화면 출력용(마스킹됨). DB에는 저장하지 않는다.
    warning: str | None = None
    sec: float = 0.0
    retry_count: int = 0
    error_code: str | None = None


def run_steps(
    steps: list[Step],
    selected: list[str],
    recorder: Callable[[StepResult], None] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(KST),
    mono: Callable[[], float] = time.monotonic,
    retries: int = 0,
    backoff: float = STEP_RETRY_BACKOFF_S,
    sleep: Callable[[float], None] = time.sleep,
    before_step: Callable[[str], None] | None = None,
    quality_counts: Callable[[str], dict] | None = None,
) -> list[StepResult]:
    """steps를 순서대로 실행한다. 한 단계가 실패(예외/SystemExit/시간 초과)해도 다음 단계로 계속하고,
    선택되지 않은 단계는 skipped로 기록한다. 단계마다 recorder(결과)를 호출한다(기록 실패는 실행을 막지 않는다).
    retries > 0이면 일시적 오류(classify_error)로 실패한 단계를 backoff x 2^n초 쉰 뒤 다시 실행한다(수집은 upsert라 안전).
    before_step(단계): 실행 직전 호출(격리 기록의 실행 맥락 설정). quality_counts(단계) -> {"quarantined", "warning"}: 실행 뒤
    저장 전 검증의 격리 건수. 격리가 있으면 성공 단계를 부분 성공(error_code=quality_quarantined)으로 바꾼다(2026-11-08, 36차:
    수집 실패와 품질 격리를 다른 코드로 구분, 부분 성공을 전체 성공으로 표시하지 않는다)."""
    results = []
    for step in steps:
        started = clock()
        t0 = mono()
        if step.key not in selected:
            res = StepResult(step.key, "skipped", started, started)
        else:
            if before_step:
                before_step(step.key)
            res = _run_one(step, started, t0, clock, mono)
            attempt = 0
            while res.status == "failed" and res.error_code and attempt < retries and _RETRYABLE.get(res.error_code, False):
                wait = backoff * (2 ** attempt)
                print(f"[재시도] {step.key}: {res.error_code}({res.error_type}), {wait:.0f}초 후 {attempt + 1}/{retries}")
                sleep(wait)
                attempt += 1
                res = _run_one(step, started, t0, clock, mono)
            res.retry_count = attempt
            if quality_counts and res.status != "failed":
                apply_quality_counts(res, quality_counts(step.key))
        results.append(res)
        if recorder:
            try:
                recorder(res)
            except Exception as e:  # noqa: BLE001
                print(f"[경고] {step.key} 기록 실패: {type(e).__name__}")
    return results


_RETRYABLE = {"timeout": True, "network": True, "server_error": True}
QUALITY_QUARANTINED = "quality_quarantined"


def apply_quality_counts(res: StepResult, counts: dict | None) -> StepResult:
    """저장 전 검증 격리가 있으면 성공 단계를 부분 성공으로 바꾸고 이유를 붙인다(실패 단계는 그대로)."""
    q = (counts or {}).get("quarantined") or 0
    if not q or res.status == "failed":
        return res
    note = f"저장 전 검증 격리 {q}건(python -m scripts.quarantine list)"
    res.status = "partial"
    res.error_code = res.error_code or QUALITY_QUARANTINED
    res.warning = Partial(f"{res.warning}; {note}" if res.warning else note)
    return res


def _run_one(step: Step, started: datetime, t0: float, clock, mono) -> StepResult:
    try:
        before = step.count()
        warning = step.run()
        after = step.count()
        return StepResult(
            step.key, "partial" if isinstance(warning, Partial) else "success", started, clock(),
            rows_added=None if before is None or after is None else max(after - before, 0),
            latest_date=step.latest(), warning=warning, sec=mono() - t0,
        )
    except BaseException as e:  # noqa: BLE001 - SystemExit/StepTimeout도 이 단계의 실패로만 취급한다
        if isinstance(e, KeyboardInterrupt):
            raise
        if isinstance(e, SystemExit) and e.code in (0, None):
            return StepResult(step.key, "success", started, clock(), sec=mono() - t0)
        return StepResult(
            step.key, "failed", started, clock(), error_type=type(e).__name__,
            error_message=mask_secrets(str(e))[:300], sec=mono() - t0, error_code=classify_error(e)[0],
        )


def exit_code(results: list[StepResult]) -> int:
    return 1 if any(r.status == "failed" for r in results) else 0


def final_exit_code(results: list[StepResult], quality_error: bool = False) -> int:
    """1 = 실패 단계 있음, 3 = 실패는 없지만 부분 성공 단계가 있거나 품질 검사 error, 0 = 그 밖."""
    if any(r.status == "failed" for r in results):
        return EXIT_FAILED
    if any(r.status == "partial" for r in results) or quality_error:
        return EXIT_PARTIAL
    return EXIT_OK


def select_run_steps(only: list[str] | None, skip: list[str] | None, sources: list[str] | None = None,
                     with_optional: list[str] | None = None) -> list[str]:
    """실행할 단계(ALL_STEPS 순서). only·skip은 단계 이름(선택 단계 etf_holdings 포함), sources는 데이터 이름(SOURCE_STEPS).
    아무것도 고르지 않으면 기본 6단계(+ with_optional). 모르는 이름·미지원 데이터는 ValueError."""
    for name in (only or []) + (skip or []):
        if name not in ALL_STEPS:
            raise ValueError(f"알 수 없는 단계 '{name}' (가능: {', '.join(ALL_STEPS)})")
    chosen: set[str] = set(only or [])
    for src in sources or []:
        if src in UNSUPPORTED_SOURCES:
            raise ValueError(f"지원하지 않는 데이터 '{src}': {UNSUPPORTED_SOURCES[src]}")
        if src not in SOURCE_STEPS:
            raise ValueError(f"알 수 없는 데이터 '{src}' (가능: {', '.join(sorted(SOURCE_STEPS))})")
        chosen.add(SOURCE_STEPS[src])
    if not chosen:
        chosen = set(STEP_ORDER) | set(with_optional or [])
    return [s for s in ALL_STEPS if s in chosen and s not in set(skip or [])]


def format_table(results: list[StepResult]) -> str:
    lines = [f"{'step':<9}{'status':<9}{'rows_added':>11}{'latest_date':>14}{'sec':>8}  note", "-" * 70]
    for r in results:
        note = r.error_type or r.warning or ""
        lines.append(
            f"{r.step:<9}{r.status:<9}{'-' if r.rows_added is None else r.rows_added:>11}"
            f"{str(r.latest_date) if r.latest_date else '-':>14}{r.sec:>8.1f}  {note}"
        )
    return "\n".join(lines)


class _Tee:
    """수집 스크립트의 stdout을 가로채 비밀값을 가리고, 진행 줄은 간격을 두고만 내보내며, 시간 초과를 감시한다."""

    def __init__(self, out, deadline: float | None, interval: float = PROGRESS_INTERVAL_SEC, mono=time.monotonic):
        self.out, self.deadline, self.interval, self.mono = out, deadline, interval, mono
        self.buf, self.captured, self._last_shown = "", [], float("-inf")

    def write(self, s: str) -> int:
        self.buf += s
        *lines, self.buf = self.buf.split("\n")
        for line in lines:
            self._line(mask_secrets(line))
        return len(s)

    def flush(self):
        pass

    def _line(self, line: str):
        self.captured.append(line)
        now = self.mono()
        is_progress = bool(_PROGRESS_RE.match(line))
        if _ALWAYS_SHOW_RE.search(line):
            show = True  # 종목별 실패 줄은 진행 줄이어도 항상 보인다
        elif is_progress:
            show = now - self._last_shown >= self.interval
        else:
            # 종목별 "[이름(코드)] ... 수집 중" 같은 잡음은 숨기고, 요약/실패/오류 줄은 항상 보인다.
            show = not line.startswith(("[", "DB 저장 완료"))
        if show:
            if is_progress and not _ALWAYS_SHOW_RE.search(line):
                self._last_shown = now
            print(f"  {datetime.now(KST):%H:%M:%S} {line}", file=self.out)
        if self.deadline is not None and now > self.deadline:
            raise StepTimeout("단계 제한 시간 초과")


def _call_script(main: Callable[[], None], argv: list[str], timeout_min: float) -> str:
    """기존 스크립트의 main()을 argv로 호출한다. 출력은 _Tee를 거치고, 비정상 종료(SystemExit)는 예외로 전달한다."""
    old_argv = sys.argv
    sys.argv = ["script", *argv]
    tee = _Tee(sys.stdout, time.monotonic() + timeout_min * 60)
    try:
        with contextlib.redirect_stdout(tee):
            try:
                main()
            except SystemExit as e:
                if e.code not in (0, None):
                    raise
    finally:
        sys.argv = old_argv
    return "\n".join(tee.captured)


# ---------------------------------------------------------------- 단계 정의 (DB/네트워크를 쓰는 부분)


def _scalar(sql: str):
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return db.execute(text(sql)).scalar()
    finally:
        db.close()


def resolve_days(days: int | None, today: date) -> tuple[int, str]:
    """(조회 기간, 설명). --days를 주면 그대로, 아니면 DB 시세 날짜로 default_days. DB를 못 읽으면 DAYS_MIN."""
    if days is not None:
        return days, "--days 지정"
    try:
        from sqlalchemy import text

        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            since = today - timedelta(days=max(GAP_LOOKBACK_DAYS, DAYS_CAP))
            present = {r[0] for r in db.execute(
                text("SELECT DISTINCT price_date FROM stock_price WHERE price_date >= :d"), {"d": since})}
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 - 기간 계산 실패가 갱신 전체를 막지 않게
        return DAYS_MIN, f"DB 시세 날짜를 읽지 못해 기본 {DAYS_MIN}일({type(e).__name__})"
    n = default_days(present, today)
    last = max(present).isoformat() if present else "없음"
    return n, f"자동(마지막 시세 {last}, 최근 {GAP_LOOKBACK_DAYS}일 안의 빈 평일 포함, 상한 {DAYS_CAP}일)"


def build_steps(days: int, timeout_min: float, news_only: bool = False, news_state: str | None = None,
                close_basis: str = "source") -> list[Step]:
    from app.data_status import _kst_date

    def prices():
        from scripts import collect_prices

        warnings, failed = [], []
        for universe in ("universe_top300.json", "universe_etf50.json"):
            try:
                out = _call_script(collect_prices.main, ["--universe", universe, "--days", str(days), "--close-basis", close_basis],
                                   timeout_min)
                if w := check_failure_ratio(out):
                    warnings.append(f"{universe}: {w}")
            except (Exception, SystemExit, StepTimeout) as e:  # 한 유니버스가 실패해도 나머지는 진행한다
                failed.append(f"{universe}: {type(e).__name__}")
        if failed:
            raise RuntimeError("; ".join(failed))
        return Partial("; ".join(warnings)) if warnings else None

    def index():
        from scripts import collect_market_index

        _call_script(collect_market_index.main, ["--days", str(max(days, 10))], timeout_min)

    def fx():
        from scripts import collect_exchange_rate

        _call_script(collect_exchange_rate.main, ["--days", str(max(days, 10))], timeout_min)

    def news():
        from scripts import collect_universe

        warnings = []
        extra = (["--news-only"] if news_only else []) + (["--state", news_state] if news_state else [])
        for universe in ("universe_top300.json", "universe_etf50.json"):
            out = _call_script(collect_universe.main, ["--universe", universe, *extra], timeout_min)
            if w := check_failure_ratio(out):
                warnings.append(f"{universe}: {w}")
        return Partial("; ".join(warnings)) if warnings else None

    def embed():
        from app.embeddings import embed_disclosure, embed_news

        tee = _Tee(sys.stdout, time.monotonic() + timeout_min * 60)
        with contextlib.redirect_stdout(tee):
            embed_news(dry_run=False)
            embed_disclosure(dry_run=False)

    def content():
        from scripts import collect_disclosure_content

        _call_script(collect_disclosure_content.main, ["--apply"], timeout_min)

    def etf_holdings():
        """기존 app.etf_holdings_collect.collect(요청 간 3초, 429 등은 한 번만 다시 시도, (ETF, 기준일) 단위 교체)를 그대로 부른다."""
        from app.db.session import SessionLocal
        from app.etf_holdings_collect import collect

        tee = _Tee(sys.stdout, time.monotonic() + timeout_min * 60)
        db = SessionLocal()
        try:
            with contextlib.redirect_stdout(tee):
                results = collect(db, log=print)
        finally:
            db.close()
        return etf_holdings_outcome(results)

    return [
        Step("prices", "주가(주식 300 + ETF 50)", prices,
             lambda: _scalar("select count(*) from stock_price"),
             lambda: _scalar("select max(price_date) from stock_price")),
        Step("index", "시장 지수", index,
             lambda: _scalar("select count(*) from market_index"),
             lambda: _scalar("select min(d) from (select max(price_date) d from market_index group by index_code) t")),
        Step("fx", "환율", fx,
             lambda: _scalar("select count(*) from exchange_rate"),
             lambda: _scalar("select max(price_date) from exchange_rate")),
        Step("news", "뉴스·공시", news,
             lambda: _scalar("select (select count(*) from news) + (select count(*) from disclosure)"),
             lambda: _kst_date(_scalar("select max(published_at) from news"))),
        Step("embed", "임베딩", embed,
             lambda: _scalar("select (select count(embedding) from news) + (select count(embedding) from disclosure)")),
        Step("content", "공시 본문", content,
             lambda: _scalar("select count(content) from disclosure")),
        Step("etf_holdings", "ETF 구성종목(KODEX·TIGER)", etf_holdings,
             lambda: _scalar("select count(*) from etf_holding"),
             lambda: _scalar("select min(d) from (select max(as_of_date) d from etf_holding group by company_id) t")),
    ]


def etf_holdings_outcome(results) -> str | None:
    """ETF별 결과 -> 단계 결과. 대상(지원 운용사) 중 실패가 절반 이상이면 실패, 일부면 부분 성공(Partial), 없으면 None."""
    targets = [r for r in results if not r.skipped]
    # 2026-11-08(36차): 저장 전 검증에서 격리된 ETF는 수집 실패가 아니다(품질 문제, 부분 성공으로 따로 센다)
    quarantined = [r for r in targets if getattr(r, "quarantined", False)]
    failed = [r for r in targets if not r.ok and not getattr(r, "quarantined", False)]
    if not targets:
        return None
    if quarantined and not failed:
        return Partial(f"격리 {len(quarantined)}/{len(targets)}건(저장 전 검증)")
    if len(failed) / len(targets) >= MAX_FAILURE_RATIO:
        raise RuntimeError("; ".join(f"{r.ticker}: {(r.error or '실패')[:60]}" for r in failed[:5])
                           + (f" 외 {len(failed) - 5}건" if len(failed) > 5 else ""))
    return Partial(f"일부 실패 {len(failed)}/{len(targets)}건") if failed else None


def write_log(res: StepResult, run_id: int | None = None) -> None:
    from app.db.session import SessionLocal
    from app.models import DataRefreshLog

    db = SessionLocal()
    try:
        db.add(
            DataRefreshLog(
                step=res.step, status=res.status, started_at=res.started_at, finished_at=res.finished_at,
                rows_added=res.rows_added, latest_date=res.latest_date, error_type=res.error_type,
                run_id=run_id, duration_ms=None if res.status == "skipped" else round(res.sec * 1000),
                retry_count=None if res.status == "skipped" else res.retry_count, error_code=res.error_code,
                warning_count=1 if res.warning else (None if res.status == "skipped" else 0),
            )
        )
        db.commit()
        if run_id is not None:
            from app import refresh_runs

            refresh_runs.heartbeat(db, run_id)
    finally:
        db.close()


def print_plan(selected: list[str], days: int, timeout_min: float, close_basis: str = "source"):
    print("[dry-run] 실제 호출·DB 기록 없이 계획만 출력합니다.")
    plan = {
        "prices": f"collect_prices x2 (universe_top300 + universe_etf50, --days {days}, --close-basis {close_basis})",
        "index": f"collect_market_index (--days {max(days, 10)}, 소스 폴백 체인)",
        "fx": f"collect_exchange_rate (--days {max(days, 10)})",
        "news": "collect_universe x2 (universe_top300: 뉴스+공시, universe_etf50: 뉴스만)",
        "embed": "app.embeddings embed_news + embed_disclosure (apply)",
        "content": "collect_disclosure_content --apply",
        "etf_holdings": "app.etf_holdings_collect.collect (KODEX·TIGER, 요청 간 3초)",
    }
    for i, key in enumerate(ALL_STEPS, 1):
        run = key in selected
        print(f"  {i}. {key:<13}{'실행' if run else '건너뜀(skipped)':<14}{plan[key] if run else ''}")
    print(
        f"단계별 제한 시간 {timeout_min:g}분, 진행 줄 출력 간격 {PROGRESS_INTERVAL_SEC}초, "
        "단계마다 data_refresh_log 한 행 기록"
    )


REPORT_DIR = Path(__file__).resolve().parents[1] / "logs" / "refresh"  # backend/logs/refresh (gitignore)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="데이터 갱신 일괄 실행")
    ap.add_argument("--only", help=f"쉼표 구분 단계만 실행 ({','.join(ALL_STEPS)})")
    ap.add_argument("--skip", help="쉼표 구분 단계를 건너뜀")
    ap.add_argument("--all", action="store_true", help="기본 6단계 전부(옵션을 주지 않은 것과 같다)")
    ap.add_argument("--source", help=f"쉼표 구분 데이터 이름으로 단계 선택({','.join(sorted(SOURCE_STEPS))})")
    ap.add_argument("--etf-holdings", action="store_true", help="기본 단계에 ETF 구성종목(etf_holdings)을 더한다")
    ap.add_argument("--retry-failed", action="store_true", help="마지막 시도가 실패·부분 성공인 단계만 다시 실행")
    ap.add_argument("--step-retries", type=int, default=STEP_RETRIES,
                    help=f"일시적 오류로 실패한 단계의 재시도 횟수(기본 {STEP_RETRIES}, 0이면 안 함)")
    ap.add_argument("--status", action="store_true", help="수집 없이 소스별 상태 보고서만 출력")
    ap.add_argument("--json", action="store_true", help="--status: 표 대신 JSON을 표준 출력으로")
    ap.add_argument("--report-dir", default=str(REPORT_DIR), help="상태 보고서(JSON·Markdown) 저장 폴더")
    ap.add_argument("--no-report", action="store_true", help="보고서 파일을 쓰지 않음")
    ap.add_argument("--prune-logs", action="store_true", help="오래된 갱신 기록 정리(--keep-days, --dry-run이면 세기만)")
    ap.add_argument("--keep-days", type=int, default=180, help="--prune-logs 보관 기간(일, 30 이상)")
    ap.add_argument("--trigger", default="manual", choices=["manual", "scheduler"], help="실행 기록에 남길 실행 주체")
    ap.add_argument("--close-basis", choices=["source", "official"], default="source",
                    help="주가 종가 기준(45차). source(기본): FinanceDataReader 그대로. official: 공식 종가(app/official_close.py)")
    ap.add_argument("--days", type=int, default=None,
                    help=f"주가/지수/환율 조회 기간(달력 일). 생략하면 DB의 마지막 시세 날짜·최근 빈 평일부터 오늘까지 + {DAYS_SLACK}일"
                         f"(최소 {DAYS_MIN}, 상한 {DAYS_CAP})")
    ap.add_argument("--timeout-min", type=float, default=DEFAULT_TIMEOUT_MIN, help="단계별 제한 시간(분)")
    ap.add_argument("--dry-run", action="store_true", help="실제 호출 없이 계획만 출력")
    ap.add_argument("--news-only", action="store_true", help="news 단계에서 공시를 수집하지 않는다")
    ap.add_argument("--news-state", default=None, help="news 단계의 이어하기 파일(collect_universe --state)")
    ap.add_argument("--earnings", action="store_true", help="마지막에 분기 실적(DART 주요 계정)을 수집한다(기본 꺼짐)")
    ap.add_argument("--earnings-limit", type=int, default=None, help="실적 수집 종목 수 제한(시험용)")
    ap.add_argument("--earnings-only", action="store_true",
                    help="기본 단계를 하나도 실행·기록하지 않고 실적만 수집한다(data_refresh_log에 행을 남기지 않음)")
    return ap


def status_main(args) -> int:
    """--status: 수집 없이 상태 보고서. 락을 잡지 않는다(실행 중이면 running으로 보인다)."""
    from app import data_monitor, refresh_runs
    from app.db.session import SessionLocal

    try:
        db = SessionLocal()
        try:
            running = refresh_runs.current_run(db) if refresh_runs.lock_is_held_elsewhere(db) else None
            report = data_monitor.build_report(db, running_steps=set(running["steps"].split(",")) if running else None,
                                               last_runs=refresh_runs.recent_runs(db, 10))
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        print(f"[실패] DB에 연결하지 못해 상태를 만들 수 없습니다: {type(e).__name__}", file=sys.stderr)
        return EXIT_DB
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    else:
        print(data_monitor.render_table(report))
        print(f"\n전체 {report['summary']['overall']} · 분석에 쓸 수 없는 소스: {', '.join(report['summary']['not_usable']) or '없음'}")
    if not args.no_report:
        jp, mp = data_monitor.write_reports(report, args.report_dir)
        print(f"보고서: {jp} · {mp}", file=sys.stderr if args.json else sys.stdout)
    return data_monitor.status_exit_code(report)


def prune_main(args) -> int:
    from app import refresh_runs
    from app.db.session import SessionLocal

    try:
        db = SessionLocal()
        try:
            out = refresh_runs.prune_logs(db, keep_days=args.keep_days, dry_run=args.dry_run)
        finally:
            db.close()
    except ValueError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as e:  # noqa: BLE001
        print(f"[실패] DB 오류: {type(e).__name__}", file=sys.stderr)
        return EXIT_DB
    verb = "지울 예정" if args.dry_run else "지움"
    print(f"갱신 기록 정리({args.keep_days}일 이전, {out['cutoff']}): 단계 기록 {out['logs']}행, 실행 기록 {out['runs']}행 {verb}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(errors="replace")

    if args.status:
        return status_main(args)
    if args.prune_logs:
        return prune_main(args)

    try:
        selected = select_run_steps(parse_csv(args.only), parse_csv(args.skip), parse_csv(args.source),
                                    list(OPTIONAL_STEPS) if args.etf_holdings else None)
    except ValueError as e:
        ap.error(str(e))

    now = datetime.now(KST)
    args.days, days_reason = resolve_days(args.days, now.date())
    print(f"조회 기간: {args.days}일 ({days_reason})")
    market_warning = (
        "[경고] 장중(평일 09:00~15:30 KST)입니다. 당일 행이 장중 값일 수 있습니다. "
        "15:30 이후 다시 실행하면 같은 날짜가 종가로 덮어써집니다."
    )
    if args.dry_run and not args.retry_failed:
        print_plan(selected, args.days, args.timeout_min, args.close_basis)
        if args.earnings:
            print("  +. earnings 실행            app.earnings.collect (DART fnlttSinglAcnt, 종목당 최대 12회, 요청 간격 0.15초)")
        if is_market_hours(now):
            print(market_warning)
        return 0

    if is_market_hours(now):
        print(market_warning)

    if args.earnings_only:
        # 2026-11-03: --skip로 모든 단계를 건너뛰어도 run_steps는 skipped 행을 남긴다. 실적만 다시 채울 때는 기록 없이 실행한다.
        return run_earnings(args.earnings_limit)

    from app import refresh_runs

    # 중복 실행 방지: 실행 전체를 advisory lock 하나로 잡는다(프로세스가 죽으면 Postgres가 풀어 준다)
    try:
        lock = refresh_runs.acquire_lock()
    except refresh_runs.LockBusy:
        print("[중단] 다른 데이터 갱신이 실행 중입니다(advisory lock). 이번 실행은 아무것도 하지 않고 끝냅니다.")
        return EXIT_LOCKED
    except Exception as e:  # noqa: BLE001
        print(f"[실패] DB에 연결하지 못했습니다: {type(e).__name__}")
        return EXIT_DB
    try:
        return _locked_run(args, selected)
    finally:
        lock.release()


def _locked_run(args, selected: list[str]) -> int:
    from app import data_monitor, refresh_runs
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        recovered = refresh_runs.recover_aborted(db)
        if recovered:
            print(f"[복구] 끝나지 못한 이전 실행 {recovered}건을 aborted로 표시했습니다(수집은 upsert라 다시 실행해도 안전).")
        if args.retry_failed:
            selected = refresh_runs.failed_steps(db, list(ALL_STEPS))
            if not selected:
                print("다시 실행할 실패 단계가 없습니다(마지막 시도가 모두 성공).")
                return EXIT_OK
            print(f"다시 실행할 단계: {', '.join(selected)}")
            if args.dry_run:
                return EXIT_OK
        trigger = "retry" if args.retry_failed else args.trigger
        run_id = refresh_runs.start_run(db, selected, trigger)
    finally:
        db.close()

    steps = [st for st in build_steps(args.days, args.timeout_min, args.news_only, args.news_state, args.close_basis)
             if st.key in STEP_ORDER or st.key in selected]  # 선택 단계(etf_holdings)는 고른 경우에만 skipped 행을 남긴다
    from app import quarantine

    def counts(step: str) -> dict:
        db = SessionLocal()
        try:
            return quarantine.counts_for_run(db, run_id, step)
        except Exception as e:  # noqa: BLE001 - 세기 실패가 수집 결과를 바꾸지 않는다
            print(f"[경고] {step} 격리 건수를 세지 못했습니다: {type(e).__name__}")
            return {}
        finally:
            db.close()

    try:
        results = run_steps(steps, selected, recorder=lambda r: write_log(r, run_id), retries=max(0, args.step_retries),
                            before_step=lambda key: quarantine.set_context(run_id, key), quality_counts=counts)
    finally:
        quarantine.clear_context()

    print("\n===== 갱신 결과 =====")
    print(format_table(results))
    for r in results:
        if r.status == "failed":
            print(f"[실패] {r.step}: {r.error_code or '-'} {r.error_type} {r.error_message}")
        elif r.status == "partial":
            print(f"[부분 성공] {r.step}: {r.warning}")

    # 수집 뒤 품질 검사·상태 보고서. 수집 성공과 품질 정상은 따로 본다(품질 error면 종료 코드 3)
    quality_error = False
    db = SessionLocal()
    try:
        try:
            report = data_monitor.build_report(db, last_runs=refresh_runs.recent_runs(db, 10))
            for step, (q, warn) in data_monitor.step_quality(report).items():
                if step in selected:
                    refresh_runs.set_step_quality(db, run_id, step, q, warn)
            ran = {r.step for r in results if r.status != "skipped"}
            quality_error = any(r["quality_status"] == "error" and r["step"] in ran for r in report["sources"])
            print("\n===== 데이터 상태 =====")
            print(data_monitor.render_table(report))
            data_monitor.dispatch(report)
            if not args.no_report:
                jp, mp = data_monitor.write_reports(report, args.report_dir)
                print(f"보고서: {jp} · {mp}")
        except Exception as e:  # noqa: BLE001 - 보고서 실패가 수집 결과를 바꾸지 않는다
            print(f"[경고] 상태 보고서를 만들지 못했습니다: {type(e).__name__}")
        code = final_exit_code(results, quality_error)
        if args.earnings:
            code = max(code, run_earnings(args.earnings_limit))
        status = refresh_runs.run_status([r.status for r in results])
        refresh_runs.finish_run(db, run_id, status, code, note="품질 error" if quality_error else None)
    finally:
        db.close()
    return code


def run_earnings(limit: int | None) -> int:
    """분기 실적 수집(app.earnings.collect). 실패해도 다른 단계 결과는 그대로다. 오류가 있으면 1."""
    from app import earnings
    from app.db.session import SessionLocal

    started = time.perf_counter()
    db = SessionLocal()
    try:
        stats = earnings.collect(db, limit=limit, log=lambda m: print(mask_secrets(m), flush=True))
    except Exception as e:  # noqa: BLE001 - 요약에 사유만 남긴다
        print(f"[실패] earnings: {type(e).__name__} {mask_secrets(str(e))}")
        return 1
    finally:
        db.close()
    print(f"\n===== 실적 수집 =====\n{stats} ({time.perf_counter() - started:.0f}s)")
    return 1 if stats["errors"] or stats["stopped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
