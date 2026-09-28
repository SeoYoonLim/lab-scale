"""
news/disclosure embedding 파이프라인.

Ollama의 bge-m3 임베딩 모델(1024차원 - news/disclosure.embedding 컬럼과 동일)로
아직 embedding이 비어 있는 행에 벡터를 생성해 저장한다.

news/disclosure 둘 다 지원한다. 기존 fetch_and_save_* 함수들과 같은 관례로
dry_run=True가 기본값이며, 이 경우 DB에 쓰지 않고 대상 건수/샘플만 보여준다.
"""

import time
from zoneinfo import ZoneInfo

import ollama
from sqlalchemy.orm import joinedload

from app.db.session import SessionLocal
from app.models import Disclosure, News

EMBEDDING_MODEL = "bge-m3"
BATCH_SIZE = 32
MAX_TEXT_LEN = 500        # 본문을 이 길이로 잘라서 임베딩 (긴 본문에서 처리 속도 약 4배)
KST = ZoneInfo("Asia/Seoul")


def _build_text(title: str, content: str | None) -> str:
    text = f"{title}\n{content}" if content else title
    return text[:MAX_TEXT_LEN]


def _news_text(row: News) -> str:
    return _build_text(row.title, row.content)


def _disclosure_text(row: Disclosure) -> str:
    """공시 임베딩 텍스트 = '회사명 제목 (접수일 KST)'. 제목이 '증권발행실적보고서' 같은 서식명이라 제목만 쓰면
    6,308건 중 96.9%가 다른 공시와 코사인 0.99 이상으로 겹쳤다(서로 다른 벡터 478개). 회사명과 접수일을 붙이면
    30.4%로 줄고 '삼성전자 공시' 같은 종목 질문의 top-10 적중이 0~1건에서 9~10건이 된다.

    원문(row.content, DART document.xml에서 수집)은 일부러 넣지 않는다. 같은 8개 질의로 재본 결과 원문을 이어
    붙이면 중복이 줄지 않고(30.4% -> 28.2~31.5%, 남은 건 같은 회사의 정기 공시라 표지 문구가 같다) top-10
    적중은 오히려 78/80 -> 70~73/80으로 떨어졌다(표지/서식 문구가 제목·회사명 신호를 희석하고 '현대차 공시'에
    현대건설·현대글로비스가 섞임). 원문은 rag_search_tool의 content_snippet으로 LLM에 보여주는 용도로만 쓴다."""
    parts = [row.company.name, row.title]
    if row.disclosed_at is not None:
        parts.append(f"({row.disclosed_at.astimezone(KST):%Y-%m-%d})")
    return " ".join(parts)[:MAX_TEXT_LEN]


def _embed_pending(
    model, label: str, limit: int | None, batch_size: int, dry_run: bool, to_text, reembed: bool = False
) -> int:
    """model(News 또는 Disclosure)의 embedding이 비어 있는 행에 bge-m3 임베딩을 채운다.
    reembed=True면 이미 임베딩이 있는 행도 다시 만든다(텍스트 구성 방식을 바꿨을 때)."""
    db = SessionLocal()
    try:
        query = db.query(model).order_by(model.id)
        if not reembed:
            query = query.filter(model.embedding.is_(None))
        if model is Disclosure:
            query = query.options(joinedload(Disclosure.company))
        if limit is not None:
            query = query.limit(limit)
        rows = query.all()

        if not rows:
            print(f"임베딩이 필요한 {label}가 없습니다.")
            return 0

        if dry_run:
            print(f"[dry-run] 임베딩 대상 {len(rows)}건 (모델={EMBEDDING_MODEL}, 대상={label})")
            for row in rows[:5]:
                print(f"  - id={row.id} text={to_text(row)[:60]!r}")
            return len(rows)

        start = time.time()
        processed = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            texts = [to_text(r) for r in batch]
            response = ollama.embed(model=EMBEDDING_MODEL, input=texts)
            for row, vector in zip(batch, response["embeddings"]):
                row.embedding = vector
            db.commit()

            processed += len(batch)
            elapsed = time.time() - start
            rate = processed / elapsed if elapsed > 0 else 0
            print(f"임베딩 저장 완료: {processed}/{len(rows)}건 ({label}) — {rate:.1f}건/초")

        return processed
    except Exception as e:
        db.rollback()
        print(f"오류 발생: {e}")
        raise
    finally:
        db.close()


def embed_news(limit: int | None = None, batch_size: int = BATCH_SIZE, dry_run: bool = True) -> int:
    """embedding이 비어 있는 news 행에 bge-m3 임베딩을 채운다.

    Returns:
        처리(또는 dry-run 대상) 건수
    """
    return _embed_pending(News, "news", limit, batch_size, dry_run, _news_text)


def embed_disclosure(
    limit: int | None = None, batch_size: int = BATCH_SIZE, dry_run: bool = True, reembed: bool = False
) -> int:
    """embedding이 비어 있는 disclosure 행에 bge-m3 임베딩을 채운다. reembed=True면 전체를 다시 만든다.

    Returns:
        처리(또는 dry-run 대상) 건수
    """
    return _embed_pending(Disclosure, "disclosure", limit, batch_size, dry_run, _disclosure_text, reembed)


if __name__ == "__main__":
    import sys

    # python -m app.embeddings [--apply] [--reembed-disclosure]
    dry_run = "--apply" not in sys.argv
    embed_news(dry_run=dry_run)
    embed_disclosure(dry_run=dry_run, reembed="--reembed-disclosure" in sys.argv)
