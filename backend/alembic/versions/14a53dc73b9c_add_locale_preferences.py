"""사용자 언어·시간대 설정과 리서치·진단 기록의 작성 언어

Revision ID: 14a53dc73b9c
Revises: c4d2e8f61a93
Create Date: 2026-11-05 11:00:00

다국어(ko-KR·en-US·ja-JP·zh-CN, 2026-11-05).
- users.preferred_locale: 기본 'ko-KR'. 기존 사용자도 'ko-KR'로 채워진다(server_default). 지원하는 4개 값만 허용(CHECK).
- users.preferred_timezone: IANA 이름. NULL이면 Asia/Seoul로 본다(기존 사용자는 NULL).
- research_report.locale: 리포트를 만들 때 쓴 언어. 기존 리포트는 'ko-KR'로 채워진다(본문은 건드리지 않는다).
  진단(portfolio_diagnosis)은 컬럼을 늘리지 않는다: ko-KR이 아닌 진단만 저장본 JSON(result)에 "locale"을 넣고, 키가 없으면 ko-KR이다.
- research_report.original_content: ko-KR이 아닌 기록에서, 번역 전에 검증을 거친 한국어 답변(후속 질문 맥락용). ko-KR 기록은 NULL.
- research_report.translation_status: ko-KR이 아닌 기록의 번역 결과('translated' | 'localized' | 'fallback_original'). ko-KR 기록은 NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '14a53dc73b9c'
down_revision: Union[str, Sequence[str], None] = 'c4d2e8f61a93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCALES = "('ko-KR', 'en-US', 'ja-JP', 'zh-CN')"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('preferred_locale', sa.String(length=10), nullable=False, server_default='ko-KR'))
    op.add_column('users', sa.Column('preferred_timezone', sa.String(length=64), nullable=True))
    op.create_check_constraint('ck_users_preferred_locale', 'users', f"preferred_locale IN {LOCALES}")
    op.add_column('research_report', sa.Column('locale', sa.String(length=10), nullable=False, server_default='ko-KR'))
    op.add_column('research_report', sa.Column('original_content', sa.Text(), nullable=True))
    op.add_column('research_report', sa.Column('translation_status', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('research_report', 'translation_status')
    op.drop_column('research_report', 'original_content')
    op.drop_column('research_report', 'locale')
    op.drop_constraint('ck_users_preferred_locale', 'users', type_='check')
    op.drop_column('users', 'preferred_timezone')
    op.drop_column('users', 'preferred_locale')
