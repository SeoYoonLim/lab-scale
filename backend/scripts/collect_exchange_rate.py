"""환율(USD/KRW) 일별 종가를 FinanceDataReader로 수집해 exchange_rate에 upsert한다.

FR-07(경제지표 영향 분석)의 1차 범위는 환율만이다 - 금리는 조사 결과(README FR-07 상세) 쓸 만한 무료 소스를
찾지 못해 범위 밖이다. scripts/collect_market_index.py와 같은 패턴. 이미 저장된 날짜는 갱신하므로 다시
실행해도 안전하다(멱등).

사용 예:
  python -m scripts.collect_exchange_rate --dry-run     # DB에 쓰지 않고 확인
  python -m scripts.collect_exchange_rate --days 180
"""

import argparse

from app.collectors import fetch_and_save_exchange_rate
from app.models.exchange_rate import EXCHANGE_RATES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180, help="조회 기간(달력 기준 일)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    failed = False
    for code, name in EXCHANGE_RATES.items():
        res = fetch_and_save_exchange_rate(code, days=args.days, dry_run=args.dry_run)
        status = "OK" if res.ok else f"FAIL ({res.error})"
        print(f"{name}({code}) {status} 조회 {res.fetched}건 / 신규 {res.saved}건")
        failed |= not res.ok

    if args.dry_run:
        print("[dry-run] DB에는 아무것도 쓰지 않았습니다.")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
