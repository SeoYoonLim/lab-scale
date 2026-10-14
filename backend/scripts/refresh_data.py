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
"""

import argparse
import contextlib
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

STEP_ORDER = ("prices", "index", "fx", "news", "embed", "content")

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
    """평일 09:00~15:30 KST이면 True(공휴일은 모른다)."""
    now = now.astimezone(KST)
    return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (15, 30)


def select_steps(only: list[str] | None, skip: list[str] | None) -> list[str]:
    """--only/--skip으로 실행할 단계를 STEP_ORDER 순서로 돌려준다. 모르는 이름은 ValueError."""
    for name in (only or []) + (skip or []):
        if name not in STEP_ORDER:
            raise ValueError(f"알 수 없는 단계 '{name}' (가능: {', '.join(STEP_ORDER)})")
    chosen = set(only) if only else set(STEP_ORDER)
    return [s for s in STEP_ORDER if s in chosen and s not in set(skip or [])]


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


def run_steps(
    steps: list[Step],
    selected: list[str],
    recorder: Callable[[StepResult], None] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(KST),
    mono: Callable[[], float] = time.monotonic,
) -> list[StepResult]:
    """steps를 순서대로 실행한다. 한 단계가 실패(예외/SystemExit/시간 초과)해도 다음 단계로 계속하고,
    선택되지 않은 단계는 skipped로 기록한다. 단계마다 recorder(결과)를 호출한다(기록 실패는 실행을 막지 않는다)."""
    results = []
    for step in steps:
        started = clock()
        t0 = mono()
        if step.key not in selected:
            res = StepResult(step.key, "skipped", started, started)
        else:
            res = _run_one(step, started, t0, clock, mono)
        results.append(res)
        if recorder:
            try:
                recorder(res)
            except Exception as e:  # noqa: BLE001
                print(f"[경고] {step.key} 기록 실패: {type(e).__name__}")
    return results


def _run_one(step: Step, started: datetime, t0: float, clock, mono) -> StepResult:
    try:
        before = step.count()
        warning = step.run()
        after = step.count()
        return StepResult(
            step.key, "success", started, clock(),
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
            error_message=mask_secrets(str(e))[:300], sec=mono() - t0,
        )


def exit_code(results: list[StepResult]) -> int:
    return 1 if any(r.status == "failed" for r in results) else 0


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
            show = not line.startswith("[")
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


def build_steps(days: int, timeout_min: float) -> list[Step]:
    from app.data_status import _kst_date

    def prices():
        from scripts import collect_prices

        warnings, failed = [], []
        for universe in ("universe_top300.json", "universe_etf50.json"):
            try:
                out = _call_script(collect_prices.main, ["--universe", universe, "--days", str(days)], timeout_min)
                if w := check_failure_ratio(out):
                    warnings.append(f"{universe}: {w}")
            except (Exception, SystemExit, StepTimeout) as e:  # 한 유니버스가 실패해도 나머지는 진행한다
                failed.append(f"{universe}: {type(e).__name__}")
        if failed:
            raise RuntimeError("; ".join(failed))
        return "; ".join(warnings) or None

    def index():
        from scripts import collect_market_index

        _call_script(collect_market_index.main, ["--days", str(max(days, 10))], timeout_min)

    def fx():
        from scripts import collect_exchange_rate

        _call_script(collect_exchange_rate.main, ["--days", str(max(days, 10))], timeout_min)

    def news():
        from scripts import collect_universe

        warnings = []
        for universe in ("universe_top300.json", "universe_etf50.json"):
            out = _call_script(collect_universe.main, ["--universe", universe], timeout_min)
            if w := check_failure_ratio(out):
                warnings.append(f"{universe}: {w}")
        return "; ".join(warnings) or None

    def embed():
        from app.embeddings import embed_disclosure, embed_news

        tee = _Tee(sys.stdout, time.monotonic() + timeout_min * 60)
        with contextlib.redirect_stdout(tee):
            embed_news(dry_run=False)
            embed_disclosure(dry_run=False)

    def content():
        from scripts import collect_disclosure_content

        _call_script(collect_disclosure_content.main, ["--apply"], timeout_min)

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
    ]


def write_log(res: StepResult) -> None:
    from app.db.session import SessionLocal
    from app.models import DataRefreshLog

    db = SessionLocal()
    try:
        db.add(
            DataRefreshLog(
                step=res.step, status=res.status, started_at=res.started_at, finished_at=res.finished_at,
                rows_added=res.rows_added, latest_date=res.latest_date, error_type=res.error_type,
            )
        )
        db.commit()
    finally:
        db.close()


def print_plan(selected: list[str], days: int, timeout_min: float):
    print("[dry-run] 실제 호출·DB 기록 없이 계획만 출력합니다.")
    plan = {
        "prices": f"collect_prices x2 (universe_top300 + universe_etf50, --days {days})",
        "index": f"collect_market_index (--days {max(days, 10)}, 소스 폴백 체인)",
        "fx": f"collect_exchange_rate (--days {max(days, 10)})",
        "news": "collect_universe x2 (universe_top300: 뉴스+공시, universe_etf50: 뉴스만)",
        "embed": "app.embeddings embed_news + embed_disclosure (apply)",
        "content": "collect_disclosure_content --apply",
    }
    for i, key in enumerate(STEP_ORDER, 1):
        run = key in selected
        print(f"  {i}. {key:<8}{'실행' if run else '건너뜀(skipped)':<14}{plan[key] if run else ''}")
    print(
        f"단계별 제한 시간 {timeout_min:g}분, 진행 줄 출력 간격 {PROGRESS_INTERVAL_SEC}초, "
        "단계마다 data_refresh_log 한 행 기록"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="데이터 갱신 일괄 실행")
    ap.add_argument("--only", help=f"쉼표 구분 단계만 실행 ({','.join(STEP_ORDER)})")
    ap.add_argument("--skip", help="쉼표 구분 단계를 건너뜀")
    ap.add_argument("--days", type=int, default=10, help="주가/지수/환율 조회 기간(달력 일), 기본 10")
    ap.add_argument("--timeout-min", type=float, default=DEFAULT_TIMEOUT_MIN, help="단계별 제한 시간(분)")
    ap.add_argument("--dry-run", action="store_true", help="실제 호출 없이 계획만 출력")
    args = ap.parse_args(argv)

    try:
        selected = select_steps(parse_csv(args.only), parse_csv(args.skip))
    except ValueError as e:
        ap.error(str(e))

    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(errors="replace")

    now = datetime.now(KST)
    market_warning = (
        "[경고] 장중(평일 09:00~15:30 KST)입니다. 당일 행이 장중 값일 수 있습니다. "
        "15:30 이후 다시 실행하면 같은 날짜가 종가로 덮어써집니다."
    )
    if args.dry_run:
        print_plan(selected, args.days, args.timeout_min)
        if is_market_hours(now):
            print(market_warning)
        return 0

    if is_market_hours(now):
        print(market_warning)

    results = run_steps(build_steps(args.days, args.timeout_min), selected, recorder=write_log)

    print("\n===== 갱신 결과 =====")
    print(format_table(results))
    for r in results:
        if r.status == "failed":
            print(f"[실패] {r.step}: {r.error_type} {r.error_message}")
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
