"""거래일 하루의 네이버 분봉 원본을 새 스테이징 폴더에 받는다(2026-11-14, 52차). DB를 쓰지 않는다.

보존(검증·체크섬)은 scripts/preserve_session_minute.py가 한다. 이 스크립트는 원본 폴더를 만드는 일만 한다.
운영 절차: docs/SESSION_MINUTE_DATA_PRESERVATION_52.md

  python -m scripts.fetch_session_minute --date 2026-11-16 --tickers-from backups/session_minute/2026-11-13 \\
      --out backups/session_minute_staging/2026-11-16_<수집시각>
  python -m scripts.fetch_session_minute ... --dry-run     # 요청·쓰기 없이 대상만 확인

- 원천: app.session_ohlcv.fetch_minute(api.stock.naver.com 분봉, 비공식, KRX 체결만). 최근 7거래일 안팎만 준다.
- 그날 분봉이 끝난 뒤(20:10 KST 이후)에만 받는다. 거래일이 아니거나 KRX 달력으로 확인할 수 없는 날은 거부한다.
- --out은 없거나 비어 있어야 한다(기존 파일을 덮어쓰지 않음). 파일은 임시 이름에 쓰고 끝까지 쓴 뒤 이름을 바꾼다.
- 그날 봉이 0개인 종목은 파일을 만들지 않고 fetch_log.json의 no_bars에 남긴다(빈 파일을 만들지 않음).
- 출력 형식은 50·51차와 같다: <out>/raw/{ticker}.json(응답 목록 그대로 json.dumps), <out>/fetch_log.json.

종료 코드: 0 = 모든 종목 받음, 1 = 실패·봉 없음 종목 있음(받은 파일은 남는다), 2 = 거부.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from app import krx_calendar, session_ohlcv as so
from scripts.preserve_session_minute import TICKER_RE, Refused, read_ticker_file, tickers_in

TOOL = "fetch_session_minute"
KST = krx_calendar.KST


def check_target(d: date, now: datetime) -> None:
    st = krx_calendar.is_trading_day(d)
    if st is False:
        raise Refused(f"{d}는 KRX 거래일이 아닙니다")
    if st is None:
        raise Refused(f"{d}는 KRX 달력 범위 밖이라 거래일인지 확인할 수 없습니다(app/data/krx_calendar 갱신 필요)")
    if not so.day_complete(d, now):
        raise Refused(f"{d} 분봉은 아직 끝나지 않았습니다. 그날 {so.DAY_COMPLETE_AT.strftime('%H:%M')} KST 뒤에 실행하세요")


def _write_new(path: Path, text: str) -> None:
    tmp = path.with_name("." + path.name + ".partial")
    with open(tmp, "x", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    if path.exists():
        tmp.unlink()
        raise FileExistsError(path)
    os.replace(tmp, path)


def fetch(d: date, tickers: list[str], out: Path, get=None, sleep: float = 0.35, clock=None) -> dict:
    clock = clock or (lambda: datetime.now(KST))
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)  # 비어 있음은 main()이 확인한다
    log = {"tool": TOOL, "source_url": so.MINUTE_URL, "target_date": d.isoformat(),
           "params": {"startDateTime": d.strftime("%Y%m%d") + "0000", "endDateTime": d.strftime("%Y%m%d") + "2359"},
           "started_at": clock().isoformat(), "tickers": len(tickers), "failed": [], "no_bars": [], "fetched_at": {},
           "bars": {}, "other_date_bars": {}}
    want = d.strftime("%Y%m%d")
    try:
        for i, t in enumerate(tickers):
            try:
                rows = so.fetch_minute(t, d, d, get=get)
            except Exception as e:  # noqa: BLE001 - 종목 하나의 실패로 멈추지 않는다
                log["failed"].append({"ticker": t, "error": str(e)[:160]})
                continue
            n = sum(1 for r in rows if isinstance(r, dict) and str(r.get("localDateTime", ""))[:8] == want)
            if n == 0:
                log["no_bars"].append({"ticker": t, "rows": len(rows)})
            else:
                _write_new(raw / f"{t}.json", json.dumps(rows, ensure_ascii=False))
                log["fetched_at"][t] = clock().isoformat(timespec="seconds")
                log["bars"][t] = n
                if len(rows) != n:
                    log["other_date_bars"][t] = len(rows) - n
            if i % 50 == 0:
                print(f"  {i}/{len(tickers)} {t}", flush=True)
            if sleep:
                time.sleep(sleep)
    finally:  # 중간에 멈춰도 받은 만큼의 기록은 남긴다
        log["finished_at"] = clock().isoformat()
        log["complete"] = len(log["fetched_at"]) + len(log["failed"]) + len(log["no_bars"]) == len(tickers)
        _write_new(out / "fetch_log.json", json.dumps(log, ensure_ascii=False, indent=1))
    return log


def main(argv=None, now: datetime | None = None, get=None, sleep: float | None = None, clock=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", required=True, type=date.fromisoformat, help="대상 거래일 YYYY-MM-DD")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tickers-from", type=Path, help="종목 = 이 폴더의 raw/*.json 이름(이전 보존본, 읽기만)")
    g.add_argument("--tickers-file", type=Path, help="종목 목록 파일(한 줄에 하나, #은 주석)")
    ap.add_argument("--out", required=True, type=Path, help="새 스테이징 폴더(없거나 비어 있어야 함)")
    ap.add_argument("--sleep", type=float, default=0.35, help="요청 간격(초)")
    ap.add_argument("--dry-run", action="store_true", help="요청·쓰기 없이 대상만 출력")
    args = ap.parse_args(argv)
    now = now or datetime.now(KST)
    try:
        tickers = tickers_in(args.tickers_from) if args.tickers_from else read_ticker_file(args.tickers_file)
        bad = [t for t in tickers if not TICKER_RE.fullmatch(t)]
        if bad or len(set(tickers)) != len(tickers) or not tickers:
            raise Refused(f"종목 목록이 비었거나 형식이 틀리거나 중복이 있습니다: {bad[:10]}")
        check_target(args.date, now)
        out = args.out.resolve()
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise Refused(f"--out이 이미 있고 비어 있지 않습니다(덮어쓰지 않음): {out}")
    except Refused as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    print(f"대상 {args.date}, 종목 {len(tickers)}개, 출력 {out}, 기준 시각 {now.isoformat(timespec='minutes')}")
    if args.dry_run:
        print("dry-run: 요청하지 않고 끝냅니다")
        return 0
    log = fetch(args.date, tickers, out, get=get, sleep=args.sleep if sleep is None else sleep, clock=clock)
    print(f"받음 {len(log['fetched_at'])}, 실패 {len(log['failed'])}, 그날 봉 없음 {len(log['no_bars'])} / {len(tickers)}")
    for k in ("failed", "no_bars"):
        if log[k]:
            print(f"  {k}: {log[k][:10]}")
    return 0 if not log["failed"] and not log["no_bars"] else 1


if __name__ == "__main__":
    sys.exit(main())
