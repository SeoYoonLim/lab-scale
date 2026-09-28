"""시장 전체 지수(코스피 KS11, 코스닥 KQ11)의 일별 종가를 FinanceDataReader로 수집해 market_index에 upsert하고,
company.market(상장 시장)이 비어 있는 종목의 시장 구분을 채운다.

market_tool은 종목이 상장된 시장의 지수와 등락률을 비교하므로 두 데이터가 모두 필요하다.
이미 저장된 날짜는 갱신하므로 다시 실행해도 안전하다(멱등).

사용 예:
  python -m scripts.collect_market_index --dry-run     # DB에 쓰지 않고 확인
  python -m scripts.collect_market_index --days 180
"""

import argparse

from app.collectors import backfill_company_market, fetch_and_save_market_index
from app.models.market_index import MARKET_INDEXES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180, help="조회 기간(달력 기준 일)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-market-backfill", action="store_true", help="company.market 채우기는 건너뜀")
    args = ap.parse_args()

    failed = False
    for code, name in MARKET_INDEXES.items():
        res = fetch_and_save_market_index(code, days=args.days, dry_run=args.dry_run)
        status = "OK" if res.ok else f"FAIL ({res.error})"
        print(f"{name}({code}) {status} 조회 {res.fetched}건 / 신규 {res.saved}건")
        failed |= not res.ok

    if not args.skip_market_backfill:
        updated, missing = backfill_company_market(dry_run=args.dry_run)
        print(f"company.market: 갱신 {updated}건, KRX 목록에 없어 못 채움 {missing}건")

    if args.dry_run:
        print("[dry-run] DB에는 아무것도 쓰지 않았습니다.")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
