"""
news/disclosure embedding 파이프라인.

Ollama의 bge-m3 임베딩 모델(1024차원 - news/disclosure.embedding 컬럼과 동일)로
아직 embedding이 비어 있는 행에 벡터를 생성해 저장한다.

우선 news 테이블만 지원한다 (disclosure는 검증 후 동일한 패턴으로 추가 예정).
기존 fetch_and_save_* 함수들과 같은 관례로 dry_run=True가 기본값이며,
이 경우 DB에 쓰지 않고 대상 건수/샘플만 보여준다.
"""

import ollama

from app.db.session import SessionLocal
from app.models import News

EMBEDDING_MODEL = "bge-m3"
BATCH_SIZE = 16


def _build_text(title: str, content: str | None) -> str:
    if content:
        return f"{title}\n{content}"
    return title


def embed_news(limit: int | None = None, batch_size: int = BATCH_SIZE, dry_run: bool = True) -> int:
    """embedding이 비어 있는 news 행에 bge-m3 임베딩을 채운다.

    Returns:
        처리(또는 dry-run 대상) 건수
    """
    db = SessionLocal()
    try:
        query = db.query(News).filter(News.embedding.is_(None)).order_by(News.id)
        if limit is not None:
            query = query.limit(limit)
        rows = query.all()

        if not rows:
            print("임베딩이 필요한 news가 없습니다.")
            return 0

        if dry_run:
            print(f"[dry-run] 임베딩 대상 {len(rows)}건 (모델={EMBEDDING_MODEL})")
            for row in rows[:5]:
                print(f"  - id={row.id} title={row.title[:40]!r}")
            return len(rows)

        processed = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            texts = [_build_text(r.title, r.content) for r in batch]
            response = ollama.embed(model=EMBEDDING_MODEL, input=texts)
            for row, vector in zip(batch, response["embeddings"]):
                row.embedding = vector
            db.commit()
            processed += len(batch)
            print(f"임베딩 저장 완료: {processed}/{len(rows)}건")

        return processed
    except Exception as e:
        db.rollback()
        print(f"오류 발생: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import sys

    dry_run = "--apply" not in sys.argv
    embed_news(dry_run=dry_run)
