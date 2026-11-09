"""ETF 구성종목·비중 수집: 삼성 KODEX와 미래에셋 TIGER의 공시 구성종목(PDF)을 받아 etf_holding에 저장한다(멱등: (ETF, 기준일) 단위 교체).

- 대상: company의 ETF 중 KODEX·TIGER. 다른 운용사 ETF(RISE·ACE·SOL·HANARO·PLUS·TIME)는 건너뛴다(채팅은 "제공하지 않음"을 유지).
- 외부 사이트에 부담을 주지 않게 하루 한 번 안팎으로만 실행한다. 요청 사이에 --delay초(기본 3.0)를 쉰다(전체 약 3~4분). 일시적 오류(429 등)로 실패한 ETF는 30초 쉰 뒤 한 번 더 시도한다.
- 한 ETF의 실패는 그 ETF만 실패로 남기고 계속한다. 결과(ETF별 기준일·종목 수·비중 합계·실패 사유)는 요약 출력과 --report JSON에 남는다.
- 수집한 시각은 etf_holding.collected_at에 남고 API(`GET /api/etf/{ticker}/holdings`)가 collected_at으로 보여준다. data_refresh_log에는 쓰지 않는다.
- 이 프로젝트의 수집은 모두 수동이다(스케줄러 없음). 구성종목은 영업일마다 바뀌므로 필요할 때 돌린다.

사용 예:
  python -m scripts.collect_etf_holdings --dry-run                     # DB에 쓰지 않고 받아서 파싱만
  python -m scripts.collect_etf_holdings                               # 전체
  python -m scripts.collect_etf_holdings --tickers 069500,133690       # 일부만
"""

import argparse
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.etf_holdings_collect import DEFAULT_DELAY_S, collect

DEFAULT_REPORT = Path(__file__).resolve().parent / "etf_holdings_report.json"  # *_report.json은 gitignore 대상


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default=None, help="쉼표 구분 종목코드(기본: company의 ETF 전체)")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY_S, help="요청 사이 대기(초)")
    ap.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않음(외부 조회와 파싱만)")
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    args = ap.parse_args(argv)
    if args.delay < 0.5:
        raise SystemExit("--delay는 0.5초 이상이어야 합니다(외부 사이트 보호).")

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    db = SessionLocal()
    try:
        results = collect(db, tickers, delay=args.delay, dry_run=args.dry_run)
    finally:
        db.close()

    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok and not r.skipped]
    skipped = [r for r in results if r.skipped]
    sums = sorted(r.total_weight_pct for r in ok if r.total_weight_pct is not None)
    summary = {
        "etfs": len(results), "ok": len(ok), "failed": len(failed), "skipped_unsupported": len(skipped),
        "rows_saved": 0 if args.dry_run else sum(r.rows for r in ok),
        "as_of_dates": sorted({r.as_of.isoformat() for r in ok if r.as_of}),
        "weight_sum_min_median_max": [sums[0], sums[len(sums) // 2], sums[-1]] if sums else None,
        "failures": [(r.ticker, r.name, r.error) for r in failed],
        "warnings": [(r.ticker, r.name, w) for r in ok for w in r.warnings],
    }
    print("\n===== 요약 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    Path(args.report).write_text(json.dumps({"summary": summary, "etfs": [r.as_dict() for r in results]},
                                            ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"결과 저장: {args.report}")
    raise SystemExit(1 if failed and not ok else 0)


if __name__ == "__main__":
    main()
