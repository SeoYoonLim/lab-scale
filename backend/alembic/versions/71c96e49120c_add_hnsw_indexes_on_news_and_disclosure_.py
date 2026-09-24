"""add hnsw indexes on news and disclosure embedding

Revision ID: 71c96e49120c
Revises: 330629072da3
Create Date: 2026-09-24 20:12:12.687221

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '71c96e49120c'
down_revision: Union[str, Sequence[str], None] = '330629072da3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 코사인 거리(<=>) 유사도 검색용 HNSW 인덱스. m/ef_construction은 pgvector 기본값(16/64)을 명시한 것이다.
# 인덱스는 `ORDER BY embedding <=> 벡터 LIMIT k` 형태의 쿼리만 가속하며 근사(approximate) 검색이다.
INDEXES = (
    ("idx_news_embedding_hnsw", "news"),
    ("idx_disclosure_embedding_hnsw", "disclosure"),
)


def upgrade() -> None:
    """Upgrade schema."""
    for name, table in INDEXES:
        op.execute(
            f"CREATE INDEX {name} ON {table} USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for name, _ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
