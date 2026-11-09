"""저장된 뉴스에 관련성을 표시한다(app/news_relevance). 기본은 건수와 표본만 출력하고, --apply일 때만 news.is_relevant를 쓴다.

    cd backend
    python -m scripts.mark_news_relevance            # 건수·표본만
    python -m scripts.mark_news_relevance --apply    # 표시(행을 지우지 않는다. 되돌리려면 is_relevant를 NULL로)

외부 요청 없음. 실행 전에 backend/backups/에 DB 백업을 만드세요.
"""

import argparse
import random
import sys
from collections import Counter

from sqlalchemy import update

from app.db.session import SessionLocal
from app.models import Company, News
from app.news_relevance import is_relevant_news

BATCH = 1000


def judge_all(db) -> list[tuple[int, bool, str, str]]:
    """(뉴스 id, 판정, 종목명, 제목). company_id가 없는 행은 판정하지 않는다."""
    companies = {c.id: c for c in db.query(Company)}
    names = [c.name for c in companies.values()]
    out = []
    for nid, cid, title, content in db.query(News.id, News.company_id, News.title, News.content).yield_per(BATCH):
        c = companies.get(cid)
        if c is not None:
            out.append((nid, is_relevant_news(c.name, c.ticker, c.asset_type, title, content, names), c.name, title))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="저장된 뉴스 관련성 표시")
    ap.add_argument("--apply", action="store_true", help="news.is_relevant에 쓴다(없으면 건수·표본만)")
    ap.add_argument("--sample", type=int, default=30, help="출력할 표시 대상 표본 수")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    db = SessionLocal()
    try:
        judged = judge_all(db)
        hidden = [j for j in judged if not j[1]]
        print(f"판정 {len(judged)}건, 관련 없음 {len(hidden)}건 ({len(hidden) / max(len(judged), 1):.1%})")
        for name, n in Counter(j[2] for j in hidden).most_common(30):
            print(f"  {name}: {n}")
        for _, _, name, title in random.Random(7).sample(hidden, min(args.sample, len(hidden))):
            print(f"  [{name}] {title[:70]}")
        if not args.apply:
            print("--apply 없이 끝냅니다(아무것도 쓰지 않음).")
            return 0
        for value in (True, False):
            ids = [j[0] for j in judged if j[1] is value]
            for i in range(0, len(ids), BATCH):
                db.execute(update(News).where(News.id.in_(ids[i:i + BATCH])).values(is_relevant=value))
        db.commit()
        print("표시 완료")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
