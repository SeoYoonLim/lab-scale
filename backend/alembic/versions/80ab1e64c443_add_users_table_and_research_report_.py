"""add users table and research_report.user_id

Revision ID: 80ab1e64c443
Revises: d1bbfc5e2f0f
Create Date: 2026-10-07 14:44:04.149935

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '80ab1e64c443'
down_revision: Union[str, Sequence[str], None] = 'd1bbfc5e2f0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    - users: 아이디/비밀번호 로그인 사용자. 테이블 이름은 PostgreSQL 예약어 user를 피해 users.
    - research_report.user_id: 리포트 소유자. nullable이라 기존(로그인 도입 전) 리포트는 NULL로 보존된다.
    watchlist/virtual_account/holding/trade는 스키마를 바꾸지 않는다(device_id 컬럼에 `user:{id}`를 저장).
    """
    op.create_table(
        'users',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=20), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username', name='uq_users_username'),
        sa.CheckConstraint('username = lower(username)', name='ck_users_username_lowercase'),
    )
    op.add_column('research_report', sa.Column('user_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        'fk_research_report_user_id',
        'research_report', 'users',
        ['user_id'], ['id'],
        ondelete='CASCADE',
    )
    op.create_index('ix_research_report_user_id', 'research_report', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_research_report_user_id', table_name='research_report')
    op.drop_constraint('fk_research_report_user_id', 'research_report', type_='foreignkey')
    op.drop_column('research_report', 'user_id')
    op.drop_table('users')
