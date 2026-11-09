"""CI용 종목(company) 시드. 시세·뉴스·공시·리포트 없이 종목 목록(공개 정보)만 담는다.

CI의 빈 pgvector DB는 `alembic upgrade head`만 거친 상태라 company 테이블이 비어 있다. 종목 해석(resolve_company)과 평가 offline
모드, 종목이 있어야 하는 통합 테스트가 이 시드로 돈다. 시드 파일(ci_seed_companies.json)은 개발 DB의 company 테이블에서 만든다:
ticker, name, market, sector, asset_type만 들어 있고 개인정보·시크릿·시세는 없다. 별칭은 DB가 아니라 코드(app/company_aliases.py)에 있다.

    python -m scripts.ci_seed            # 시드 JSON을 DATABASE_URL의 DB에 적재 (이미 있는 ticker는 건너뜀: 멱등)
    python -m scripts.ci_seed --export   # DATABASE_URL의 company 테이블에서 시드 JSON을 다시 만든다 (읽기만 함)
"""

import argparse
import json
import sys
from pathlib import Path

SEED_PATH = Path(__file__).with_name("ci_seed_companies.json")
FIELDS = ("ticker", "name", "market", "sector", "asset_type")


def read_seed(path: Path = SEED_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def export_seed(db, path: Path = SEED_PATH) -> int:
    """company 테이블을 시드 JSON으로 쓴다(SELECT만). 쓴 행 수를 돌려준다."""
    from app.models import Company

    rows = [
        {k: getattr(c, k) for k in FIELDS}
        for c in db.query(Company).order_by(Company.id)
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return len(rows)


def load_seed(db, path: Path = SEED_PATH, commit: bool = True) -> tuple[int, int]:
    """시드를 company에 넣는다. 이미 있는 ticker는 건너뛴다. (새로 넣은 수, 이미 있던 수)."""
    from sqlalchemy.dialects.postgresql import insert

    from app.models import Company

    rows = read_seed(path)
    inserted = 0
    for row in rows:
        stmt = insert(Company).values(**{k: row[k] for k in FIELDS}).on_conflict_do_nothing(index_elements=["ticker"])
        # rowcount는 드라이버에 따라 -1이 나와서, 실제로 들어간 행만 RETURNING으로 센다
        inserted += 1 if db.execute(stmt.returning(Company.ticker)).first() is not None else 0
    if commit:
        db.commit()
    return inserted, len(rows) - inserted


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CI용 종목 시드")
    ap.add_argument("--export", action="store_true", help="DATABASE_URL의 company 테이블에서 시드 JSON을 만든다(읽기 전용)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        if args.export:
            print(f"시드 {export_seed(db)}개 -> {SEED_PATH.name}")
        else:
            inserted, existing = load_seed(db)
            print(f"시드 적재: 새로 {inserted}개, 이미 있음 {existing}개")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
