# 승인 전 대기 마이그레이션

이 폴더의 마이그레이션은 `alembic.ini`의 기본 위치(`alembic/versions`)에 없으므로 일반 `alembic upgrade head`와
`/api/health`의 migrations 검사에 들어가지 않는다. 개발 DB에 적용하기 전에 시험 DB에서만 검증하려고 따로 둔다.

- 시험 DB 적용: `python -m scripts.alembic_pending --database-url <시험 DB URL> upgrade 0f6d0ec52ac3`
  (개발 DB를 가리키면 거부한다)
- 승인 뒤: 파일을 `alembic/versions/`로 옮기고(`git mv`), `app/models/__init__.py`에 `StockPriceSession`을 등록한 다음
  개발 DB 백업 → `alembic upgrade head` 순서로 적용한다. 절차: `docs/SESSION_MARKET_DATA_IMPLEMENTATION_REHEARSAL_53.md` 17절

| 리비전 | 내용 | 상태 |
| --- | --- | --- |
| `0f6d0ec52ac3` | `stock_price_session`(세션별 시세, 53차) | 시험 DB 검증 완료, 개발 DB 미적용·승인 대기 |
